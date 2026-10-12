"""Conservative visible login diagnosis, separate from purchase origin checks."""
import re
from urllib.parse import urlsplit, urljoin
from playwright.async_api import TimeoutError as PlaywrightTimeout
from app.models.config import apple_url
from app.models.flow import UserAction


AUTH_HOSTS = frozenset(('idmsa.apple.com', 'idmsa.apple.com.cn', 'appleid.apple.com', 'account.apple.com'))
ACCOUNT_ENTRY = 'https://www.apple.com.cn/shop/browse/account/home'
STORE_ENTRY = 'https://www.apple.com.cn/store'
LOGOUT_LABEL = re.compile(r'注销|退出登录')


def shop_signin_url(url):
    return apple_url(url) and (urlsplit(url).path == '/shop/signIn'
                              or urlsplit(url).path.startswith('/shop/signIn/'))


def store_page_url(url):
    return apple_url(url) and urlsplit(url).path.rstrip('/') == '/store'


def session_page_url(url):
    # Store menus can remember a name even when authenticated access expired.
    # Confirm a protected account/order return, not the remembered menu alone.
    return account_page_url(url) or (
        apple_url(url) and urlsplit(url).path.rstrip('/') == '/shop/order/list')


async def show_store_bag(page, *, timeout=5000):
    """Open the normal visible Store menu, without calling any bag/auth API."""
    if not store_page_url(page.url):
        raise UserAction('当前页面不是 Apple 中国商店，请从商店购物袋登录')
    button = page.locator('#globalnav-menubutton-link-bag').filter(visible=True)
    await button.wait_for(state='visible', timeout=timeout)
    if await button.get_attribute('aria-expanded') != 'true':
        await button.click(timeout=timeout)
    menu = page.locator('#globalnav-submenu-bag').filter(visible=True)
    await menu.wait_for(state='visible', timeout=timeout)
    return menu


async def follow_store_account(page, menu=None):
    """Verify a remembered Store identity through its normal account link."""
    menu=menu if menu is not None else await show_store_bag(page)
    candidates=menu.get_by_role('link',name=re.compile(r'注销|退出登录|^(登录|登入|Sign In|Log In)$',re.I)).filter(visible=True)
    try:await candidates.first.wait_for(state='visible',timeout=1500)
    except PlaywrightTimeout:return False
    if not await menu.get_by_role('link',name=LOGOUT_LABEL).filter(visible=True).count():return False
    account=menu.get_by_role('link',name=re.compile(r'^(账户|Account)$',re.I)).filter(visible=True)
    if await account.count()!=1:
        raise UserAction('购物袋记住了账户，但无法确认有效登录；请打开官网账户页复核')
    href=await account.get_attribute('href')
    if not href or not account_page_url(urljoin(page.url,href)):
        raise UserAction('购物袋的账户入口无法确认，请在官网手动复核登录')
    await account.click(timeout=5000)
    await page.wait_for_load_state('domcontentloaded',timeout=15000)
    return True


def account_page_url(url):
    return apple_url(url) and urlsplit(url).path.startswith(('/shop/account/', '/shop/browse/account/'))


def apple_auth_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == 'https' and parsed.hostname in AUTH_HOSTS
                and parsed.port in (None, 443) and not parsed.username and not parsed.password)
    except ValueError:
        return False


async def visible_login_form(page) -> bool:
    # Apple commonly renders its login form in an Apple-hosted iframe. This is
    # read-only evidence, never permission to fill credentials off the CN shop.
    for frame in page.frames:
        if not (apple_url(frame.url) or apple_auth_url(frame.url)):continue
        password = frame.locator('input[type="password"]').filter(visible=True)
        # Pickup contact fields also contain email/phone labels. Only Apple's
        # auth origin or explicit sign-in path makes those a login signal.
        sign_in = apple_auth_url(frame.url) or shop_signin_url(frame.url)
        pattern = r'Apple\s*(账户|ID)|电子邮件|邮件地址|邮箱|电话号码|Email|phone' if sign_in else r'Apple\s*(账户|ID)'
        account = frame.get_by_role('textbox', name=re.compile(pattern, re.I)).filter(visible=True)
        if await password.count() or await account.count():return True
    return False
