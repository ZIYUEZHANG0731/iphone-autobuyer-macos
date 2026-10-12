import pytest
from playwright.async_api import async_playwright

from app.adapters.apple import AppleAdapter
from app.automation.control import Control
from app.models.config import Settings
from app.services.login import ACCOUNT_ENTRY
from app.services.signin import SignInController


ORDERS_URL = 'https://secure9.www.apple.com.cn/shop/order/list'
DETAIL_URL = 'https://secure9.www.apple.com.cn/shop/order/detail/506738/W1234567890'
ORDERS_HTML = f'<h1>你已订购的产品。</h1><a href="{DETAIL_URL}">iPhone 17 - Order W1234567890</a>'
FORM_HTML = '<h1>登录 Apple Store</h1><input aria-label="电子邮件或电话号码"><input type="password" aria-label="密码">'


@pytest.fixture
async def browser():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        yield browser
        await browser.close()


async def order_fixture(browser, *, expired=False, order_link=ORDERS_URL):
    context = await browser.new_context()
    page = await context.new_page()
    requests = []

    async def respond(route):
        requests.append(route.request.url)
        if route.request.url == ORDERS_URL:
            if expired:
                # Browser routing intercepts the first server redirect only.
                # A fresh document navigation keeps this fixture fully offline.
                await route.fulfill(content_type='text/html; charset=utf-8',
                    body='<script>location.replace("/shop/signIn/account")</script>')
                return
            body = ORDERS_HTML
        elif route.request.url == DETAIL_URL:
            body = f'<h1>订单已过期</h1><a href="{order_link}">查看你的订单 </a>'
        else:
            body = FORM_HTML
        await route.fulfill(content_type='text/html; charset=utf-8', body=body)

    await context.route('**/*', respond)
    return context, page, requests


@pytest.mark.e2e
@pytest.mark.parametrize('entry', [ORDERS_URL, DETAIL_URL])
async def test_selected_order_session_checks_its_actual_protected_endpoint_without_logout_link(browser, entry):
    context, page, requests = await order_fixture(browser)
    events = []
    try:
        await page.goto(entry)
        adapter = AppleAdapter(page, Control(False), progress=lambda kind, data: events.append((kind, data)))
        assert await adapter.check_login() is True
        assert requests == [entry, ORDERS_URL]
        assert page.url == entry and context.pages == [page]
        assert any(kind == 'login' and data.get('authenticated') is True for kind, data in events)
    finally:
        await context.close()


@pytest.mark.e2e
async def test_expired_order_session_is_checked_by_a_new_request_and_never_confirmed(browser):
    context, page, requests = await order_fixture(browser, expired=True)
    events = []
    try:
        await page.goto(DETAIL_URL)
        adapter = AppleAdapter(page, Control(False), progress=lambda kind, data: events.append((kind, data)))
        assert await adapter.check_login() is False
        assert requests == [DETAIL_URL, ORDERS_URL, 'https://secure9.www.apple.com.cn/shop/signIn/account']
        assert page.url == DETAIL_URL and context.pages == [page]
        assert not any(kind == 'login' and data.get('authenticated') is True for kind, data in events)
    finally:
        await context.close()


@pytest.mark.e2e
@pytest.mark.parametrize('order_link', [
    'https://secure9.www.apple.com.cn.attacker.invalid/shop/order/list',
    'https://secure8.www.apple.com.cn/shop/order/list',
    'https://secure9.www.apple.com.cn/shop/order/listOther',
])
async def test_order_detail_rejects_untrusted_or_different_origin_account_destinations(browser, order_link):
    context, page, requests = await order_fixture(browser, order_link=order_link)
    try:
        await page.goto(DETAIL_URL)
        adapter = AppleAdapter(page, Control(False))
        assert await adapter.check_login() is False
        assert requests == [DETAIL_URL, ACCOUNT_ENTRY]
        assert page.url == DETAIL_URL and context.pages == [page]
    finally:
        await context.close()


@pytest.mark.e2e
async def test_order_heading_without_valid_order_data_is_not_login_proof(browser):
    context = await browser.new_context()
    page = await context.new_page()
    await context.route('**/*', lambda route: route.fulfill(
        content_type='text/html; charset=utf-8',
        body='<h1>你已订购的产品。</h1><a href="https://attacker.invalid/shop/order/detail/506738/W1234567890">Order W1234567890</a>'))
    try:
        await page.goto(ORDERS_URL)
        assert await AppleAdapter(page, Control(False)).login_status(timeout=.05) is None
    finally:
        await context.close()


@pytest.mark.e2e
async def test_visible_login_form_overrides_order_data_and_logout_labels(browser):
    context = await browser.new_context()
    page = await context.new_page()
    await context.route('**/*', lambda route: route.fulfill(
        content_type='text/html; charset=utf-8',
        body=FORM_HTML + ORDERS_HTML + '<a href="/shop/signOut">退出登录</a>'))
    try:
        await page.goto(ORDERS_URL)
        assert await AppleAdapter(page, Control(False)).login_status(timeout=.05) is False
        assert not await SignInController()._success(page, Settings(), lambda *_: None)
    finally:
        await context.close()


@pytest.mark.e2e
async def test_saved_signin_recognizes_the_same_authenticated_order_return(browser):
    context, page, _ = await order_fixture(browser)
    controller = SignInController()
    controller.attempted.add('fixture-account')
    try:
        await page.goto(ORDERS_URL)
        assert await controller._success(page, Settings(credential_id='fixture-account'), lambda *_: None)
        assert not controller.attempted
    finally:
        await context.close()
