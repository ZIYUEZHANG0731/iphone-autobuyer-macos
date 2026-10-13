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
SIGNIN_LABEL = re.compile(r'^(登录|登入|Sign In|Log In)$', re.I)


def browser_page_label(url):
    """Describe the connected page without exposing URLs or account data."""
    if apple_auth_url(url):
        return 'Apple 认证页'
    if not apple_url(url):
        return '当前连接页不是 Apple 中国商店'
    path=urlsplit(url).path.rstrip('/')
    if shop_signin_url(url):return 'Apple 账户登录页'
    if order_list_url(url):return '订单列表'
    if order_detail_url(url):return '订单详情'
    if account_page_url(url):return 'Apple 账户页'
    if path=='/shop/bag':return '购物袋'
    if path.startswith('/shop/checkout'):return '结账页'
    if path.startswith('/shop/buy-iphone'):return '商品页'
    if path=='/store':return 'Apple 中国商店'
    return 'Apple 中国官网'


def shop_signin_url(url):
    return apple_url(url) and (urlsplit(url).path == '/shop/signIn'
                              or urlsplit(url).path.startswith('/shop/signIn/'))


def store_page_url(url):
    return apple_url(url) and urlsplit(url).path.rstrip('/') == '/store'


def storefront_page_url(url):
    if not apple_url(url):
        return False
    path = urlsplit(url).path.rstrip('/')
    return (path in ('/store', '/shop/bag', '/shop/buy-iphone')
            or path.startswith('/shop/buy-iphone/'))


def session_page_url(url):
    # Store menus can remember a name even when authenticated access expired.
    # Confirm a protected account/order return, not the remembered menu alone.
    return account_page_url(url) or order_list_url(url)


def order_list_url(url):
    return apple_url(url) and urlsplit(url).path.rstrip('/') == '/shop/order/list'


def order_detail_url(url):
    return apple_url(url) and bool(re.fullmatch(
        r'/shop/order/detail/[A-Za-z0-9_-]+/W\d+', urlsplit(url).path.rstrip('/')))


async def account_check_url(page):
    """Use the selected page's normal protected entry, without changing nodes."""
    if session_page_url(page.url):
        return page.url
    if storefront_page_url(page.url):
        # Reload an auxiliary copy, then follow its actual account link. A
        # generic account entry can establish a different Store node/context.
        return page.url
    if order_detail_url(page.url):
        orders = page.get_by_role('link', name=re.compile(r'^查看你的订单')).filter(visible=True)
        if await orders.count() == 1:
            href = await orders.get_attribute('href')
            target = urljoin(page.url, href) if href else ''
            if order_list_url(target) and urlsplit(target).hostname == urlsplit(page.url).hostname:
                return target
    return ACCOUNT_ENTRY


async def authenticated_account_page(page):
    """Require protected account evidence, never a remembered Store menu."""
    if not session_page_url(page.url) or await visible_login_form(page):
        return False
    if await page.get_by_role('link', name=LOGOUT_LABEL).filter(visible=True).count():
        return True
    if not order_list_url(page.url):
        return False
    heading = page.get_by_role('heading', name=re.compile(r'^你已订购的产品[。.]?$')).filter(visible=True)
    if await heading.count() != 1:
        return False
    orders = page.get_by_role('link', name=re.compile(r'\bOrder\s+W\d+\b')).filter(visible=True)
    # Only the normal, same-origin detail links in the actual order list count.
    for href in await orders.evaluate_all('(links) => links.map(link => link.getAttribute("href"))'):
        target = urljoin(page.url, href) if href else ''
        if order_detail_url(target) and urlsplit(target).hostname == urlsplit(page.url).hostname:
            return True
    return False


async def show_store_bag(page, *, timeout=5000):
    """Open the normal visible Store menu, without calling any bag/auth API."""
    if not storefront_page_url(page.url):
        raise UserAction('当前页面不是 Apple 中国商店，请从商店购物袋登录')
    button = page.locator('#globalnav-menubutton-link-bag').filter(visible=True)
    await button.wait_for(state='visible', timeout=timeout)
    if await button.get_attribute('aria-expanded') != 'true':
        await button.click(timeout=timeout)
    menu = page.locator('#globalnav-submenu-bag').filter(visible=True)
    await menu.wait_for(state='visible', timeout=timeout)
    return menu


async def store_signin_link(page, menu):
    """Select the official profile entry rather than the saved-items prompt.

Apple renders two visible sign-in links in an empty bag. Its public globalnav
component identifies the profile link separately. Different SSI contexts must
not be merged just because their accessible labels match.
"""
    links = menu.get_by_role('link', name=SIGNIN_LABEL).filter(visible=True)
    profile = links.and_(menu.locator(
        '[data-ac-autom="gn-bagview-link-signIn"], a.ac-gn-bagview-nav-link-signin'))
    profile_count = await profile.count()
    if profile_count > 1:
        raise UserAction('购物袋的个人资料登录入口不唯一，请在官网自行选择')
    candidates = profile if profile_count == 1 else links
    count = await candidates.count()
    if not count:
        return None
    targets = []
    for href in await candidates.evaluate_all('(links) => links.map(link => link.getAttribute("href"))'):
        target = urljoin(page.url, href) if href else ''
        if not shop_signin_url(target):
            raise UserAction('购物袋登录链接无法确认；请在官网手动打开登录页')
        targets.append(target)
    if len(set(targets)) != 1:
        raise UserAction('购物袋显示不同的登录入口，请在官网自行选择')
    return candidates.first


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
    # click() also waits for its navigation. A valid account response can arrive
    # just after five seconds; allow the normal redirect to finish without a
    # second click, reload or credential submission.
    await account.click(timeout=15000)
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


def visible_password(frame):
    # IDMS keeps a nonzero password input inside its collapsed, aria-hidden
    # first step. Playwright's CSS visibility alone still matches that input.
    return frame.locator('input[type="password"]:not([aria-hidden="true"])'
                         ':not([aria-hidden="true"] input)').filter(visible=True)


async def visible_login_form(page) -> bool:
    # Apple commonly renders its login form in an Apple-hosted iframe. This is
    # read-only evidence, never permission to fill credentials off the CN shop.
    for frame in page.frames:
        if not (apple_url(frame.url) or apple_auth_url(frame.url)):continue
        password = visible_password(frame)
        # Pickup contact fields also contain email/phone labels. Only Apple's
        # auth origin or explicit sign-in path makes those a login signal.
        sign_in = apple_auth_url(frame.url) or shop_signin_url(frame.url)
        pattern = r'Apple\s*(账户|ID)|电子邮件|邮件地址|邮箱|电话号码|Email|phone' if sign_in else r'Apple\s*(账户|ID)'
        account = frame.get_by_role('textbox', name=re.compile(pattern, re.I)).filter(visible=True)
        if await password.count() or await account.count():return True
    return False
