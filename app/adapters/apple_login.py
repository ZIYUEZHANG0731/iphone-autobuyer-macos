"""Store session verification and login recovery, shared by all browser modes."""
import asyncio
from datetime import datetime, timezone
import re
from playwright.async_api import Error as PlaywrightError
from app.models.flow import RateLimited, UserAction
from app.services.login import (LOGOUT_LABEL, STORE_ENTRY, account_check_url,
    apple_auth_url, authenticated_account_page, follow_store_account, show_store_bag,
    storefront_page_url, store_page_url, store_signin_link, visible_login_form,
    ACCOUNT_ENTRY)


class AppleLogin:
    async def open_store_signin(self, page=None):
        page=page or self.page
        if not store_page_url(page.url):
            await page.goto(STORE_ENTRY,wait_until='domcontentloaded',timeout=15000)
        await self.guard(page)
        try:
            menu=await show_store_bag(page)
        except PlaywrightError:
            await self.guard(page)
            raise UserAction('商店购物袋菜单尚未载入；保留页面，请从购物袋的“登录”入口继续') from None
        deadline=asyncio.get_running_loop().time()+8
        while True:
            await self.guard(page)
            if await menu.get_by_role('link',name=LOGOUT_LABEL).filter(visible=True).count():
                self.progress('login',{'authenticated':None,'status':'remembered',
                    'reason':'购物袋记住了账户，正在通过官网账户入口复核有效登录'})
                await follow_store_account(page,menu)
                await self.login_guard(page)
                return
            login=await store_signin_link(page,menu)
            if login is not None:
                # The website owns the destination, SSI and redirect context.
                # Click the actual menu link; do not persist or replay its URL.
                # The official redirect can finish after five seconds. Allow
                # it to complete without abandoning the already-opened form
                # or clicking the login link a second time.
                await login.click(timeout=15000)
                await page.wait_for_load_state('domcontentloaded',timeout=15000)
                await self.login_guard(page)
                return
            if asyncio.get_running_loop().time()>=deadline:
                raise UserAction('购物袋尚未显示登录入口；保留商店页面，请稍后继续')
            await self.control.sleep(.15)

    async def login_status(self,page=None,*,timeout=3):
        # Observe the current login return; do not submit credentials or retry it.
        page=page or self.page
        deadline=asyncio.get_running_loop().time()+timeout
        bag_checked=False
        while True:
            if getattr(self,'auth_service_status',None):
                raise UserAction('Apple 认证请求返回 HTTP 503，暂时无法验证身份；这不等于密码错误，保留当前页面')
            if apple_auth_url(page.url):
                self.check_auth_limit()
            else:await self.guard(page,allow_busy=True)
            if store_page_url(page.url) and not bag_checked:
                bag_checked=True
                try:
                    if await follow_store_account(page):continue
                except PlaywrightError:pass
            if await authenticated_account_page(page):
                self.progress('login',{'authenticated':True,'status':'authenticated','checked_at':datetime.now(timezone.utc).isoformat()})
                return True
            if await visible_login_form(page):
                self.progress('login',{'authenticated':False,'status':'sign_in_required','checked_at':datetime.now(timezone.utc).isoformat(),
                    'reason':'官网显示登录表单；按提前选择的结账策略处理，现有会话保留'})
                return False
            remaining=deadline-asyncio.get_running_loop().time()
            if remaining<=0:
                break
            await self.control.sleep(min(.2,remaining))
        body=await page.locator('body').inner_text()
        loading=bool(re.search(r'正在载入|正在加载|Loading',body,re.I))
        reason='官网登录返回仍在加载，暂时无法确认；现有会话保留' if loading else '官网暂未提供明确登录结果；保留会话，结账时再确认'
        self.progress('login',{'authenticated':None,'status':'unknown','checked_at':datetime.now(timezone.utc).isoformat(),'reason':reason})
        return None

    async def login_guard(self,page):
        if getattr(self,'auth_service_status',None):
            raise UserAction('Apple 认证请求返回 HTTP 503；保留页面，不自动重发密码')
        if not apple_auth_url(page.url):
            await self.guard(page,allow_busy=True)
        else:
            self.check_auth_limit()
        if self.last_document_status>=400 or asyncio.get_running_loop().time()<self.busy_until:
            raise UserAction('官网登录暂未返回成功页面；保留会话，不会提交密码')

    def check_auth_limit(self):
        self.control.check()
        remaining = self.limited_until - asyncio.get_running_loop().time()
        if self.http_status in (403,429,541) and remaining > 0:
            raise RateLimited('Apple 登录请求暂时被拒绝；保留会话，不会重新提交密码',retry_after=max(1,int(remaining)))

    async def auto_sign_in(self,page,config=None,*,wait_for_form=False):
        config=config or self.login_config
        if not config or not config.auto_login or not config.credential_id or not self.credential_provider:
            return False
        # Checkout recovery also needs the GUI-owned Qt view restored before
        # typing/clicking, just like the standalone login action.
        await self.focus_login(page)
        return await self.sign_in.run(page,config,provider=self.credential_provider,
            guard=lambda:self.login_guard(page),control=self.control,progress=self.progress,
            timeout=min(30,config.operation_timeout),wait_for_form=wait_for_form)

    async def focus_login(self,page):
        # Queue the view restoration before the Qt bridge sets input focus.
        self.progress('auth_focus',True)
        if self.page_focus:await self.page_focus(page)
        else:await page.bring_to_front()

    async def confirm_login(self,page=None,*,timeout=15):
        result=await self.login_status(page,timeout=timeout)
        if result is not True:
            body=await (page or self.page).locator('body').inner_text()
            reason=('官网登录返回仍在加载，暂时无法确认；现有会话保留' if re.search(r'正在载入|正在加载|Loading',body,re.I) else '官网暂未提供明确登录结果；请查看当前页面后继续') if result is None else '官网需要重新登录或验证，请在浏览器处理后继续'
            raise UserAction(reason)
        return True

    async def check_login(self, *, auto_recover=True):
        # One normal account-page check before a real scheduled run. No cookie export.
        pending=self.sign_in.pending_page
        account=pending if pending and not pending.is_closed() else await self.new_page()
        keep=bool(pending and account is pending)
        account.on('response',self._response)
        try:
            try:
                target = await account_check_url(self.page) if not keep else None
                response=None if keep else await account.goto(target,wait_until='domcontentloaded',timeout=5000)
                if not keep and storefront_page_url(account.url):
                    await self.guard(account,allow_busy=True)
                    if not await follow_store_account(account):
                        # An unsigned public menu is not login proof. Obtain
                        # the normal protected response before reporting False.
                        response=await account.goto(ACCOUNT_ENTRY,wait_until='domcontentloaded',timeout=5000)
            except PlaywrightError:
                self.progress('login',{'authenticated':None,'checked_at':datetime.now(timezone.utc).isoformat(),'reason':'账户页暂时无法连接；沿用现有会话，结账时再检查'})
                return None
            if not apple_auth_url(account.url):await self.guard(account,allow_busy=True)
            text=await account.locator('body').inner_text()
            if (response and response.status==503) or re.search(r'马上回来|稍后回来|We.?ll be back|商店.{0,8}(维护|暂时关闭)',text,re.I):
                self.progress('login',{'authenticated':None,'checked_at':datetime.now(timezone.utc).isoformat(),'reason':'官网维护，暂时无法复核登录；浏览器会话保留'})
                return None
            status=await self.login_status(account)
            config=self.login_config
            if status is True:
                if config:self.sign_in.authenticated(config.credential_id)
                keep=False
            elif status is False and auto_recover and config and config.auto_login:
                try:
                    if not keep:await self.open_store_signin(account)
                    attempted=await self.auto_sign_in(account,config)
                    if attempted:
                        status=await self.login_status(account)
                        keep=False
                except UserAction:
                    keep=True;self.sign_in.pending_page=account
                    await self.focus_login(account)
                    raise
            return status
        finally:
            account.remove_listener('response',self._response)
            if not keep and account is not self.page:await self.page_closer(account)
