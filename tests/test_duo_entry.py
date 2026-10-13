"""Launch-entry behavior; every browser request is intercepted locally."""
from datetime import datetime

import pytest
from playwright.async_api import async_playwright

from app.adapters.apple import AppleAdapter
from app.automation.control import Control
from app.models.config import Product,Settings,Store
from app.models.flow import UserAction


SKU='https://www.apple.com.cn/shop/buy-iphone/iphone-duo/mk2q4ch/a'


def config(strategy='refresh'):
    return Settings(product=Product(name='iPhone Duo',url=SKU,max_total='17999.00'),
                    stores=[Store('Apple 南京东路',store_id='R359')],entry_strategy=strategy)


@pytest.fixture
async def browser():
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True)
        yield browser
        await browser.close()


@pytest.mark.parametrize('strategy',['refresh','wait'])
@pytest.mark.parametrize('message',[
    '在我们带你进店开始预购前，请勿关闭此页面。',
    'Please stay on this page until we can bring you in for pre-order.',
])
async def test_observed_wait_wording_uses_saved_refresh_preference(browser,strategy,message):
    context=await browser.new_context();page=await context.new_page();requests=[]
    async def respond(route):
        requests.append(route.request.method)
        await route.fulfill(content_type='text/html; charset=utf-8',
            body='<h1>等待进店</h1><p>'+message+'</p>')
    await context.route('**/*',respond)
    adapter=AppleAdapter(page,Control(False))
    try:
        await adapter.prepare(config(strategy))
        assert not await adapter.is_open() and adapter.in_queue
        await adapter.reload()
        assert requests==(['GET','GET'] if strategy=='refresh' else ['GET'])
        assert not await adapter.is_open()
    finally:await context.close()


@pytest.mark.parametrize('message',[
    'We love that early energy. Almost ready for you. Pre-order begins at 5:00 a.m. PT. See you soon.',
    '这是一份从未出现过的开售文案，请稍候进店。',
])
async def test_unknown_launch_wording_without_product_controls_keeps_retrying(browser,message):
    context=await browser.new_context();page=await context.new_page();requests=[]
    async def respond(route):
        requests.append(route.request.method)
        await route.fulfill(content_type='text/html; charset=utf-8',body='<h1>'+message+'</h1>')
    await context.route('**/*',respond)
    adapter=AppleAdapter(page,Control(False))
    try:
        await adapter.prepare(config())
        assert not await adapter.is_open()
        await adapter.reload()
        assert requests==['GET','GET']
    finally:await context.close()


@pytest.mark.parametrize('after_time,enabled,expected',[(False,False,False),(True,False,False),(True,True,True)])
async def test_preconfigured_duo_does_not_open_by_clock_alone(browser,monkeypatch,after_time,enabled,expected):
    import app.adapters.apple as apple_module
    import app.services.catalog as catalog_module
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):
            value=datetime.fromisoformat('2026-10-16T20:00:03+08:00' if after_time else '2026-10-16T19:59:59+08:00')
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)
    monkeypatch.setattr(apple_module,'datetime',Clock);monkeypatch.setattr(catalog_module,'datetime',Clock)
    context=await browser.new_context();page=await context.new_page()
    html='<h1>iPhone Duo</h1><p>10 月 16 日晚 8 点接受预购。10 月 23 日发售。</p>'+\
        '<label><input type="radio" name="dimensionColor" checked>夜空色</label>'+\
        '<label><input type="radio" name="dimensionCapacity" checked>512GB</label>'+\
        '<button '+('' if enabled else 'disabled')+'>继续</button>'
    await context.route('**/*',lambda route:route.fulfill(content_type='text/html; charset=utf-8',body=html))
    adapter=AppleAdapter(page,Control(False))
    try:
        await adapter.prepare(config())
        assert await adapter.is_open() is expected
    finally:await context.close()


async def test_family_configuration_can_open_before_continue_is_enabled(browser):
    context=await browser.new_context();page=await context.new_page()
    html='<h1>购买 iPhone Duo</h1><label><input type="radio" name="dimensionColor">夜空色</label><button disabled>继续</button>'
    await context.route('**/*',lambda route:route.fulfill(content_type='text/html; charset=utf-8',body=html))
    adapter=AppleAdapter(page,Control(False));settings=config();settings.product.url=SKU.rsplit('/',2)[0]
    try:
        await adapter.prepare(settings)
        assert await adapter.is_open()
    finally:await context.close()


@pytest.mark.parametrize('path',['/shop/bag','/shop/checkout','/shop/signIn/account','/shop/account/home'])
async def test_entry_refresh_does_not_refresh_bag_checkout_or_login(browser,path):
    context=await browser.new_context();page=await context.new_page();requests=[]
    async def respond(route):
        requests.append(route.request.method)
        await route.fulfill(content_type='text/html; charset=utf-8',body='<h1>已有流程</h1><button>继续</button>')
    await context.route('**/*',respond)
    adapter=AppleAdapter(page,Control(False));adapter.target_url=SKU
    try:
        await page.goto('https://secure.www.apple.com.cn'+path)
        assert not await adapter.is_open()
        with pytest.raises(UserAction,match='不执行入口刷新'):await adapter.reload()
        assert requests==['GET']
    finally:await context.close()
