"""One normal, visible Apple sign-in attempt. No auth endpoints or OTP replay."""
import asyncio
import re
from urllib.parse import urlsplit

from playwright.async_api import Error as PlaywrightError, expect

from app.models.config import apple_url
from app.models.flow import UserAction
from app.services.login import (apple_auth_url, shop_signin_url,
    store_page_url, follow_store_account, authenticated_account_page)


ACCOUNT_LABEL = re.compile(r'Apple\s*(账户|ID)|电子邮件|邮件地址|邮箱|电话号码|Email|phone', re.I)
NEXT_LABEL = re.compile(r'^(继续|登录|Sign In|Continue)$', re.I)
TRUST_HEADING = re.compile(r'信任.{0,4}浏览器|Trust this browser', re.I)


def credential_origin(url):
    """Only the Store sign-in page and its IDMS auth frame accept secrets."""
    path = urlsplit(url).path
    return (shop_signin_url(url)
            or (apple_auth_url(url) and urlsplit(url).hostname in ('idmsa.apple.com','idmsa.apple.com.cn')
                and path.startswith('/appleauth/')))


def login_top_origin(url):
    return ((apple_url(url) and urlsplit(url).path.startswith('/shop/'))
            or credential_origin(url))


async def auth_frames(page):
    if not login_top_origin(page.url):
        raise UserAction('登录页已离开 Apple 官网；已停止自动填写账户')
    result = []
    for frame in page.frames:
        if not credential_origin(frame.url):
            continue
        current = frame
        while current.parent_frame:
            if not (apple_url(current.parent_frame.url) or apple_auth_url(current.parent_frame.url)):
                break
            if not await (await current.frame_element()).is_visible():
                break
            current = current.parent_frame
        else:
            result.append(frame)
    return result


async def sign_in_form(page):
    forms = []
    for frame in await auth_frames(page):
        account = frame.get_by_role('textbox', name=ACCOUNT_LABEL).filter(visible=True)
        password = frame.locator('input[type="password"]').filter(visible=True)
        if await account.count() or await password.count():
            if await account.count() > 1 or await password.count() > 1:
                raise UserAction('官网账户表单无法唯一确认，请在浏览器处理')
            forms.append((frame, account, password))
    if len(forms) > 1:
        raise UserAction('官网同时显示多个登录表单，请在浏览器选择当前账户')
    return forms[0] if forms else None


async def sign_in_button(frame):
    # The observed IDMS arrow is #sign-in. Its accessible label can include
    # loading/help text, or become visible just after the password is filled.
    # Restrict the fallback to a real button in the already-validated auth frame.
    known = frame.locator('button#sign-in, #sign-in[role="button"], '
                          'input#sign-in[type="submit"], input#sign-in[type="button"]').filter(visible=True)
    count = await known.count()
    if count:
        return known if count == 1 else None
    named = frame.get_by_role('button', name=NEXT_LABEL).filter(visible=True)
    count = await named.count()
    if count:
        return named if count == 1 else None
    try:
        await known.wait_for(state='visible', timeout=5000)
    except PlaywrightError:
        return None
    return known if await known.count() == 1 else None


async def activate_login_control(control):
    # Qt's CDP input can acknowledge an iframe click without activating its
    # control. Use this uniquely observed, enabled element's standard action;
    # the page still handles authentication and any security verification.
    await expect(control).to_be_enabled(timeout=5000)
    await control.evaluate('(element) => element.click()')


class SignInController:
    def __init__(self):
        self.attempted = set()
        self.trust_attempted = set()
        self.pending_page = None

    def authenticated(self, credential_id):
        # Only a subsequent confirmed success permits a later expiry attempt.
        self.attempted.discard(credential_id)
        self.pending_page = None

    async def run(self, page, config, *, provider, guard, control, progress, timeout=15, wait_for_form=False):
        if not config.auto_login or not config.credential_id or not provider:
            return False
        try:
            return await self._run(page, config, provider, guard, control, progress, timeout, wait_for_form)
        except (PlaywrightError, AssertionError):
            # Playwright can include a typed value in its diagnostic; never pass it on.
            # An auth iframe can disappear during the successful parent return.
            # Only observe that return; do not repeat a typed or submitted value.
            deadline=asyncio.get_running_loop().time()+1
            while asyncio.get_running_loop().time()<deadline:
                await control.sleep(.1)
                try:
                    await guard()
                    if not await sign_in_form(page) and await self._success(page,config,progress):
                        return True
                except PlaywrightError:
                    continue
            raise UserAction('Apple 登录操作尚未完成；已保留页面，不会重复提交密码') from None

    async def _blocked_step(self, frames, progress):
        for frame in frames:
            text = await frame.locator('body').inner_text()
            if re.search(r'captcha|verify you are human|访问被拒绝|access denied|验证码.{0,6}(图片|字符)', text, re.I):
                raise UserAction('Apple 要求安全验证或拒绝访问；已停止自动登录，请处理官网提示')
            change = frame.get_by_role('heading', name=re.compile(r'更改密码|新密码|重设密码|change.{0,6}password|reset.{0,6}password', re.I)).filter(visible=True)
            if await change.count() or await frame.locator('input[autocomplete="new-password"]').filter(visible=True).count():
                raise UserAction('Apple 显示修改密码页面，请自行处理；软件不会填写新密码')
            if re.search(r'无法验证.{0,8}身份|could not verify|unable to verify', text, re.I):
                raise UserAction('Apple 提示无法验证身份；原因尚未确认，请查看官网提示，不会重复提交密码')
            if re.search(r'密码.{0,8}(错误|不正确)|账户或密码.{0,8}(错误|不正确)|incorrect|invalid password|账户.{0,8}(锁定|停用)|account.{0,8}locked', text, re.I):
                raise UserAction('Apple 未接受账户登录；请检查账户或密码，软件不会再次提交')
            codes = frame.locator('input[autocomplete="one-time-code"], input[inputmode="numeric"][maxlength="1"]').filter(visible=True)
            if await codes.count() or re.search(r'请输入.{0,8}验证码|输入.{0,8}六位|验证码已发送|双重认证|verification code|two.factor authentication|安全问题|security questions', text, re.I):
                message = 'Apple 要求验证码或安全验证；已保留登录页面，请在官网完成后继续检查'
                progress('verification_required', {'kind': 'apple_account', 'message': message})
                raise UserAction(message)

    async def _trust(self, frames, config, guard, progress):
        for frame in frames:
            heading = frame.get_by_role('heading', name=TRUST_HEADING).filter(visible=True)
            if not await heading.count():
                continue
            if not config.trust_browser:
                raise UserAction('Apple 询问是否信任此浏览器；请在官网自行选择后继续')
            button = frame.get_by_role('button', name=re.compile(r'^(信任|Trust)$', re.I)).filter(visible=True)
            key = (config.credential_id, frame)
            if await heading.count() != 1 or await button.count() != 1:
                raise UserAction('Apple 的信任浏览器选项无法唯一确认，请自行选择')
            if key not in self.trust_attempted:
                await guard()
                self.trust_attempted.add(key)
                await activate_login_control(button)
                progress('auto_login', {'status': 'trust_selected', 'message': '已按提前选择确认信任此浏览器；后续是否要求验证仍由 Apple 决定'})
            return True
        return False

    async def _success(self, page, config, progress):
        if not apple_url(page.url):
            return False
        path = urlsplit(page.url).path
        if store_page_url(page.url):
            if await follow_store_account(page):return False
        if await authenticated_account_page(page):
            self.authenticated(config.credential_id)
            return True
        if path.rstrip('/') == '/shop/checkout':
            for name in ('同意并继续', '继续填写取货详情', '继续选择付款方式', '检查订单', '立即下单'):
                if await page.get_by_role('button', name=name, exact=True).is_visible():
                    self.authenticated(config.credential_id)
                    progress('auto_login', {'status': 'checkout_returned', 'message': '账户登录已返回官网结账，继续保留当前购物袋'})
                    return True
        return False

    async def _run(self, page, config, provider, guard, control, progress, timeout, wait_for_form):
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            await guard()
            frames = await auth_frames(page)
            await self._blocked_step(frames, progress)
            form = await sign_in_form(page)
            if not form and await self._success(page, config, progress):
                return True
            trust = await self._trust(frames, config, guard, progress)
            if form or trust:
                break
            if not wait_for_form:
                return False
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise UserAction('Apple 结账登录表单仍在加载；保留购物袋，不会提前切换游客或提交密码')
            await control.sleep(min(.15, remaining))
        credential = None
        account_entered = None
        account_submitted = password_submitted = False
        if form:
            if config.credential_id in self.attempted:
                raise UserAction('本次账户登录已尝试；请完成官网验证或更新保存的密码，不会重复提交')
            credential = provider(config.credential_id)
            credential.validate()
            self.attempted.add(config.credential_id)
            progress('auto_login', {'status': 'started', 'message': '官网明确要求登录，正在使用本次已填写的账号密码尝试一次'})
        while True:
            await guard()
            frames = await auth_frames(page)
            await self._blocked_step(frames, progress)
            trust = await self._trust(frames, config, guard, progress)
            form = await sign_in_form(page)
            if not form and not trust and await self._success(page, config, progress):
                return True
            if form and credential:
                frame, account, password = form
                if not password_submitted and await password.count():
                    if await account.count():
                        await guard()
                        if not await account.is_enabled() or await account.get_attribute('readonly') is not None:
                            if (await account.input_value()).casefold() != credential.account.casefold():
                                raise UserAction('官网显示的账户与保存账户不同，请手动确认')
                        else:
                            await account.fill(credential.account, timeout=5000)
                        if (await account.input_value()).casefold() != credential.account.casefold():
                            raise UserAction('官网未接受保存的账户，请手动确认')
                    elif account_entered is not frame and not await frame.get_by_text(credential.account, exact=True).filter(visible=True).count():
                        raise UserAction('无法确认密码页属于保存的账户，请手动确认')
                    await guard()
                    await password.fill(credential.password, timeout=5000)
                    remember = frame.get_by_role('checkbox', name=re.compile(r'^(记住我|记住我的账户|Remember me|Remember my Apple Account)$', re.I)).filter(visible=True)
                    if config.trust_browser and await remember.count() == 1:
                        if not await remember.is_checked():
                            await guard()
                            await activate_login_control(remember)
                        await expect(remember).to_be_checked(timeout=5000)
                    button = await sign_in_button(frame)
                    if button is None:
                        raise UserAction('官网登录按钮无法唯一确认，请手动继续')
                    await guard()
                    password_submitted = True
                    await activate_login_control(button)
                elif not account_submitted and not password_submitted and await account.count():
                    await guard()
                    await account.fill(credential.account, timeout=5000)
                    if (await account.input_value()).casefold() != credential.account.casefold():
                        raise UserAction('官网未接受保存的账户，请手动确认')
                    button = await sign_in_button(frame)
                    if button is None:
                        raise UserAction('官网账户继续按钮无法唯一确认，请手动继续')
                    account_entered = frame
                    account_submitted = True
                    await guard()
                    await activate_login_control(button)
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise UserAction('Apple 登录尚未返回明确结果；已保留会话，不会重复提交密码')
            await control.sleep(min(.15, remaining))
