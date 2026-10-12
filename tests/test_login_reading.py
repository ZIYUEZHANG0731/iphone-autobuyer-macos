from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from playwright.async_api import async_playwright

from app.adapters.apple import AppleAdapter
from app.automation.control import Control
from app.gui.worker import Worker
from app.models.flow import RateLimited, Stopped, UserAction
from app.services.simulation import local_config
from app.services.login import visible_login_form
from app.services.login import STORE_ENTRY


# Reproduce the observed loading page and rejected background login request.
# These routes are completely intercepted and contain only synthetic data.
LOADING_LOGIN = '''<main><h1>登录 Apple Store</h1><p>正在载入…</p></main>
<script>fetch('/shop/signIn/idms/fixture-auth?context=PRIVATE_FIXTURE_TOKEN')
.then(response=>response.json()).catch(()=>{});</script>'''


@pytest.fixture
async def browser():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        yield browser
        await browser.close()


async def login_fixture(browser):
    context = await browser.new_context()
    page = await context.new_page()
    requests = []

    async def respond(route):
        if route.request.url == STORE_ENTRY:
            await route.fulfill(content_type='text/html; charset=utf-8',body='''
                <button id="globalnav-menubutton-link-bag" aria-expanded="false"
                    onclick="this.setAttribute('aria-expanded','true');document.querySelector('nav').hidden=false">购物袋</button>
                <nav id="globalnav-submenu-bag" hidden><a href="/shop/signIn?ssi=PRIVATE_FIXTURE_TOKEN">登录</a></nav>''')
        elif '/fixture-auth?' in route.request.url:
            requests.append('login')
            await asyncio.sleep(.05)
            await route.fulfill(status=541, content_type='text/html',
                                body='<!doctype html><p>Access denied</p>')
        else:
            await route.fulfill(content_type='text/html; charset=utf-8', body=LOADING_LOGIN)

    await context.route('**/*', respond)
    return context, page, requests


@pytest.mark.e2e
async def test_resumed_login_preserves_background_http_rejection(browser, tmp_path, monkeypatch):
    context, page, requests = await login_fixture(browser)
    worker = Worker('login', local_config(), tmp_path, interactive=True)
    rejection = asyncio.Event()
    observations = []

    def observe(kind, data):
        observations.append((kind, data))
        if kind == 'http_status' and data['status'] == 541:
            rejection.set()

    async def completed_manual_step():
        await asyncio.wait_for(rejection.wait(), 2)

    monkeypatch.setattr(worker, 'progress', observe)
    monkeypatch.setattr(worker.control, 'wait_resume', completed_manual_step)
    try:
        with pytest.raises(RateLimited, match='541'):
            await worker.browser_action(SimpleNamespace(page=page), None)
        assert requests == ['login']
        assert not page.is_closed()
        assert 'PRIVATE_FIXTURE_TOKEN' not in json.dumps(observations)
        assert not any(data.get('authenticated') is True for kind, data in observations if kind == 'login')
    finally:
        await context.close()


@pytest.mark.e2e
async def test_account_check_observes_background_login_rejection_and_keeps_session(browser):
    context, page, requests = await login_fixture(browser)
    await context.add_cookies([{'name': 'fixture_session', 'value': 'synthetic',
                               'domain': '.apple.com.cn', 'path': '/'}])
    observations = []
    adapter = AppleAdapter(page, Control(False),
                           progress=lambda kind, data: observations.append((kind, data)))
    try:
        with pytest.raises(RateLimited, match='541'):
            await asyncio.wait_for(adapter.check_login(), 1.5)
        assert requests == ['login']
        assert context.pages == [page]
        assert any(cookie['name'] == 'fixture_session' and cookie['value'] == 'synthetic'
                   for cookie in await context.cookies())
        assert 'PRIVATE_FIXTURE_TOKEN' not in json.dumps(observations)
        assert not any(data.get('authenticated') is True for kind, data in observations if kind == 'login')
    finally:
        await context.close()


@pytest.mark.e2e
async def test_login_can_finish_late_without_another_credential_submission(browser):
    context=await browser.new_context();page=await context.new_page();requests=[];observations=[]
    body='''<main>正在载入…</main><script>setTimeout(()=>{
    document.querySelector('main').innerHTML='<a href="/shop/signOut">注销</a>';
    },100);</script>'''

    async def respond(route):
        requests.append(route.request.method)
        await route.fulfill(content_type='text/html; charset=utf-8',body=body)

    await context.route('**/*',respond)
    adapter=AppleAdapter(page,Control(False),progress=lambda kind,data:observations.append((kind,data)))
    try:
        await page.goto('https://secure.www.apple.com.cn/shop/account/home')
        assert await adapter.confirm_login(timeout=1)
        assert observations[-1][1]['authenticated'] is True
        assert requests==['GET']
        assert not page.is_closed()
    finally:await context.close()


@pytest.mark.e2e
async def test_loading_return_stays_unknown_without_new_requests_or_closing_browser(browser):
    context=await browser.new_context();page=await context.new_page();observations=[];requests=[]

    async def respond(route):
        requests.append(route.request.method)
        await route.fulfill(content_type='text/html; charset=utf-8',body='<main>正在载入…</main>')

    await context.route('**/*',respond)
    adapter=AppleAdapter(page,Control(False),progress=lambda kind,data:observations.append((kind,data)))
    try:
        await page.goto('https://secure.www.apple.com.cn/shop/signIn/account')
        with pytest.raises(UserAction,match='仍在加载'):
            await adapter.confirm_login(timeout=.12)
        assert observations[-1][1]['authenticated'] is None
        assert requests==['GET'] and not page.is_closed()
    finally:await context.close()


@pytest.mark.e2e
async def test_logout_label_on_another_domain_cannot_confirm_apple_login(browser):
    context=await browser.new_context();page=await context.new_page();observations=[]
    await context.route('**/*',lambda route:route.fulfill(content_type='text/html; charset=utf-8',
                                                       body='<a href="/shop/signOut">注销</a>'))
    adapter=AppleAdapter(page,Control(False),progress=lambda kind,data:observations.append((kind,data)))
    try:
        await page.goto('https://apple.com.cn.attacker.invalid/shop/account/home')
        with pytest.raises(UserAction,match='离开 Apple'):
            await adapter.confirm_login(timeout=.1)
        assert not any(data.get('authenticated') is True for kind,data in observations if kind=='login')
    finally:await context.close()


@pytest.mark.e2e
async def test_stop_interrupts_login_confirmation_while_keeping_browser_open(browser):
    context=await browser.new_context();page=await context.new_page();control=Control()
    await context.route('**/*',lambda route:route.fulfill(content_type='text/html; charset=utf-8',
                                                       body='<main>正在载入…</main>'))
    await page.goto('https://secure.www.apple.com.cn/shop/signIn/account')
    adapter=AppleAdapter(page,control)

    async def stopping():
        await asyncio.sleep(.03);control.cancel_task()

    stopper=asyncio.create_task(stopping())
    try:
        with pytest.raises(Stopped):
            await asyncio.wait_for(adapter.confirm_login(),.6)
        assert not page.is_closed() and not control.stop_event.is_set()
    finally:
        await stopper
        await context.close()


@pytest.mark.e2e
async def test_pickup_contact_email_and_phone_are_not_a_login_form(browser):
    context=await browser.new_context();page=await context.new_page()
    await context.route('**/*',lambda route:route.fulfill(content_type='text/html; charset=utf-8',body='''
        <h1>取货联系信息</h1><input aria-label="电子邮件地址"><input aria-label="联系人手机号码">
        <button>继续选择付款方式</button>'''))
    try:
        await page.goto('https://secure.www.apple.com.cn/shop/checkout?_s=PickupContact-init')
        assert not await visible_login_form(page)
        adapter=AppleAdapter(page,Control(False))
        with pytest.raises(UserAction,match='取货人表单'):
            await adapter.fill_customer(None,local_config())
    finally:await context.close()


@pytest.mark.e2e
async def test_apple_auth_iframe_is_explicit_sign_in_evidence_and_keeps_session(browser):
    context=await browser.new_context();page=await context.new_page();requests=[];observations=[]
    async def respond(route):
        requests.append(route.request.method)
        body='<input aria-label="电子邮件地址或电话号码">' if 'idmsa.apple.com' in route.request.url else '''
            <h1>登录 Apple Store</h1><iframe src="https://idmsa.apple.com/appleauth/auth/signin"></iframe>'''
        await route.fulfill(content_type='text/html; charset=utf-8',body=body)
    await context.route('**/*',respond)
    await context.add_cookies([{'name':'fixture_session','value':'synthetic','domain':'.apple.com.cn','path':'/'}])
    try:
        await page.goto('https://secure.www.apple.com.cn/shop/signIn/account')
        adapter=AppleAdapter(page,Control(False),progress=lambda kind,data:observations.append((kind,data)))
        assert await adapter.login_status(timeout=.2) is False
        assert observations[-1][1]['status']=='sign_in_required'
        assert not page.is_closed() and requests==['GET','GET']
        assert any(c['name']=='fixture_session' for c in await context.cookies())
    finally:await context.close()


@pytest.mark.e2e
async def test_maintenance_account_response_is_unknown_and_not_logout(browser):
    context=await browser.new_context();page=await context.new_page();requests=[];observations=[]
    async def respond(route):
        requests.append(route.request.method)
        await route.fulfill(status=503,content_type='text/html; charset=utf-8',body='<h1>马上回来</h1>')
    await context.route('**/*',respond)
    try:
        adapter=AppleAdapter(page,Control(False),progress=lambda kind,data:observations.append((kind,data)))
        assert await adapter.check_login() is None
        assert context.pages==[page] and requests==['GET']
        assert observations[-1][1]['authenticated'] is None
    finally:await context.close()


@pytest.mark.e2e
@pytest.mark.parametrize('strategy',['account_then_guest','guest','account_only'])
async def test_checkout_uses_only_the_preselected_guest_fallback_and_keeps_the_bag(browser,strategy):
    context=await browser.new_context();page=await context.new_page();observations=[]
    body='''<h1>登录 Apple Store</h1><input aria-label="Apple 账户">
        <button onclick="window.loginClicks=(window.loginClicks||0)+1">登录</button>
        <button onclick="window.guestClicks=(window.guestClicks||0)+1;
            document.body.innerHTML='<h1>取货联系信息</h1><input aria-label=&quot;电子邮件地址&quot;><button>继续选择付款方式</button>'">以游客身份继续</button>'''
    await context.route('**/*',lambda route:route.fulfill(content_type='text/html; charset=utf-8',body=body))
    config=local_config();config.checkout_strategy=strategy
    try:
        await page.goto('https://secure.www.apple.com.cn/shop/checkout?_s=SignIn')
        adapter=AppleAdapter(page,Control(False),progress=lambda kind,data:observations.append((kind,data)))
        with pytest.raises(UserAction,match='只用 Apple 账户' if strategy=='account_only' else '取货人表单'):
            await adapter.fill_customer(None,config)
        assert await page.evaluate('window.guestClicks||0')==(0 if strategy=='account_only' else 1)
        assert await page.evaluate('window.loginClicks||0')==0
        if strategy!='account_only':
            with pytest.raises(UserAction,match='取货人表单'):
                await adapter.fill_customer(None,config)
            assert await page.evaluate('window.guestClicks')==1
        assert not page.is_closed()
    finally:await context.close()


@pytest.mark.e2e
async def test_visible_sms_verification_remains_paused_when_customer_fields_were_already_filled(browser):
    context=await browser.new_context();page=await context.new_page();events=[]
    await context.route('**/*',lambda route:route.fulfill(content_type='text/html; charset=utf-8',body='''
        <h1>取货联系信息</h1><input aria-label="电子邮件地址"><input autocomplete="one-time-code" aria-label="验证码">
        <button onclick="window.verifyClicks=(window.verifyClicks||0)+1">验证</button><button>继续选择付款方式</button>'''))
    try:
        await page.goto('https://secure.www.apple.com.cn/shop/checkout?_s=PickupContact-init')
        adapter=AppleAdapter(page,Control(False),progress=lambda kind,data:events.append((kind,data)))
        adapter.identity_filled=True
        for _ in range(2):
            with pytest.raises(UserAction,match='手机验证'):
                await adapter.fill_customer(None,local_config())
        assert await page.evaluate('window.verifyClicks||0')==0
        assert any(kind=='verification_required' for kind,_ in events)
    finally:await context.close()


@pytest.mark.e2e
async def test_existing_confirmed_login_does_not_require_a_manual_continue(browser,tmp_path,monkeypatch):
    context=await browser.new_context();page=await context.new_page();requests=[]
    async def respond(route):
        requests.append(route.request.method)
        body='''<button id="globalnav-menubutton-link-bag" aria-expanded="false" onclick="
            this.setAttribute('aria-expanded','true');document.querySelector('nav').hidden=false">购物袋</button>
            <nav id="globalnav-submenu-bag" hidden><a href="/shop/signOut">注销</a>
            <a href="https://secure.www.apple.com.cn/shop/account/home">账户</a></nav>''' if route.request.url==STORE_ENTRY else '<a href="/shop/signOut">注销</a>'
        await route.fulfill(content_type='text/html; charset=utf-8',body=body)
    await context.route('**/*',respond)
    worker=Worker('login',local_config(),tmp_path)
    async def unexpected_wait():raise AssertionError('An existing login must not ask for another manual sign-in')
    monkeypatch.setattr(worker.control,'wait_resume',unexpected_wait)
    try:
        result=await worker.browser_action(SimpleNamespace(page=page),None)
        assert result['state']=='COMPLETED' and '无需重新登录' in result['message']
        assert requests==['GET','GET'] and not page.is_closed()
    finally:await context.close()
