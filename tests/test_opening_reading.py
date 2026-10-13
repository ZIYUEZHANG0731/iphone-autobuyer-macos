from __future__ import annotations

import asyncio
import pytest
from playwright.async_api import async_playwright

from app.adapters.apple import AppleAdapter
from app.automation.control import Control
from app.models.flow import RateLimited
from app.services.simulation import local_config


@pytest.fixture
async def browser():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        yield browser
        await browser.close()


@pytest.mark.e2e
async def test_opening_navigation_is_bounded_even_when_a_deferred_script_stalls(browser):
    context = await browser.new_context()
    page = await context.new_page()
    page.set_default_navigation_timeout(30000)
    document_gets = []

    async def respond(route):
        if route.request.url.endswith('/slow-fixture.js'):
            await asyncio.sleep(.8)
            try:
                await route.fulfill(content_type='application/javascript', body='void 0;')
            except Exception:
                pass  # The test closes its own context with this resource still pending.
        else:
            document_gets.append(route.request.method)
            await route.fulfill(content_type='text/html; charset=utf-8',
                                body='<h1>稍后回来</h1><script defer src="/slow-fixture.js"></script>')

    await context.route('**/*', respond)
    config = local_config()
    config.product.url = 'https://www.apple.com.cn/shop/product/offline-fixture'
    config.operation_timeout = .08
    adapter = AppleAdapter(page, Control(False))
    try:
        await asyncio.wait_for(adapter.prepare(config), .5)
        assert adapter.browse_unavailable
        assert not await adapter.is_open()
        assert document_gets == ['GET'] and not page.is_closed()
    finally:
        await context.close()


@pytest.mark.e2e
async def test_first_connection_failure_can_retry_the_validated_target_from_blank_page(browser):
    context=await browser.new_context();page=await context.new_page();gets=[]

    async def respond(route):
        gets.append(route.request.method)
        if len(gets)==1:
            await route.abort('connectionfailed')
        else:
            await route.fulfill(content_type='text/html; charset=utf-8',body='<h1>购买 iPhone 17</h1><button>继续</button>')

    await context.route('**/*',respond)
    config=local_config();config.product.url='https://www.apple.com.cn/shop/product/offline-fixture'
    adapter=AppleAdapter(page,Control(False))
    try:
        await adapter.prepare(config)
        assert not await adapter.is_open()
        await adapter.reload()
        assert await adapter.is_open()
        assert gets==['GET','GET'] and not page.is_closed()
    finally:await context.close()


@pytest.mark.e2e
@pytest.mark.parametrize('operation', ['is_open', 'reload'])
async def test_timed_out_navigation_cannot_skip_an_existing_541_wait(browser, operation):
    context = await browser.new_context()
    page = await context.new_page()
    gets = []

    async def respond(route):
        gets.append(route.request.method)
        await route.fulfill(content_type='text/html; charset=utf-8', body='<h1>稍后回来</h1>')

    await context.route('**/*', respond)
    await page.goto('https://www.apple.com.cn/shop/product/offline-fixture')
    adapter = AppleAdapter(page, Control(False))
    adapter.browse_unavailable = True
    adapter.http_status = 541
    adapter.limited_until = asyncio.get_running_loop().time() + 60
    try:
        with pytest.raises(RateLimited, match='541'):
            await getattr(adapter, operation)()
        assert gets == ['GET'] and not page.is_closed()
    finally:
        await context.close()
