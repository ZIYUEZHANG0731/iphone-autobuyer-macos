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
        if '/fixture-auth?' in route.request.url:
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
