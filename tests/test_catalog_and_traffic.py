from copy import deepcopy
from types import SimpleNamespace
import asyncio
from datetime import datetime, timedelta, timezone
import pytest
from playwright.async_api import async_playwright
from app.services.traffic import SiteCooldown
from app.services.catalog import exact_product_link,groups_from_controls
from app.services.pricing import quote_key
from app.automation.control import Control
from app.automation.browser import BrowserSession
from app.adapters.apple import AppleAdapter
from app.gui.worker import Worker
from app.models.config import Settings,Product
from app.models.flow import UserAction
from app.services.simulation import local_config


def test_cooldown_survives_restart_and_counts_one_page_rejection_as_one_episode(tmp_path):
    cooldown=SiteCooldown(tmp_path)
    assert cooldown.block(5,now=1000,episode='first-navigation')==5
    assert cooldown.block(5,now=1002,episode='first-navigation')==5
    restarted=SiteCooldown(tmp_path)
    assert restarted.remaining(now=1003)==4
    # A late rejected asset in this same document remains one episode.
    assert restarted.block(5,now=1008,episode='first-navigation')==5
    assert restarted.load()['failures']==1
    assert restarted.block(5,now=1014,episode='second-navigation')==10
    assert restarted.block(3600,now=1015,episode='second-navigation')==3600
    assert restarted.load()['failures']==2


def test_server_busy_wait_is_separate_from_rejection_cooldown(tmp_path):
    cooldown=SiteCooldown(tmp_path)
    assert cooldown.busy(30,now=1000)==30
    assert SiteCooldown(tmp_path).busy_remaining(now=1010)==20
    assert cooldown.remaining(now=1010)==0
    assert cooldown.load()['failures']==0


def test_product_discovery_requires_unique_observed_apple_link():
    links=[{'name':'购买 iPhone Duo','url':'https://www.apple.com.cn/shop/buy-iphone/iphone-duo'},
           {'name':'iPhone Duo','url':'https://apple.com.cn.attacker.invalid/shop/anything'}]
    assert exact_product_link(links,'iPhone Duo')==links[0]['url']
    links.append({'name':'iPhone Duo','url':'https://www.apple.com.cn/shop/product/different'})
    assert exact_product_link(links,'iPhone Duo') is None


async def test_unquoted_price_needs_click_approval_bound_to_product_quantity_and_amount():
    events=[];control=Control(False)
    adapter=AppleAdapter(SimpleNamespace(on=lambda *_:None),control,progress=lambda k,d:events.append((k,d)))
    config=Settings(product=Product(name='Offline Phone',url='https://www.apple.com.cn/shop/product/fixture',
        max_total='0',price_from_website=True))
    with pytest.raises(UserAction,match='点选'):await adapter.accept_price(config,'123.00')
    key=events[-1][1]['key'];control.approve_quote(key)
    await adapter.accept_price(config,'123.00');assert config.product.max_total=='123.00'
    config.product.quantity=2
    with pytest.raises(UserAction):await adapter.accept_price(config,'246.00')
    assert quote_key(config.product,'246.00')!=key


@pytest.fixture
async def browser():
    async with async_playwright() as playwright:
        browser=await playwright.chromium.launch(headless=True)
        yield browser
        await browser.close()


@pytest.mark.e2e
async def test_503_busy_honors_retry_after_without_becoming_541_or_sending_another_get(browser):
    context=await browser.new_context();page=await context.new_page();gets=[]
    async def respond(route):
        gets.append(route.request.method)
        await route.fulfill(status=503 if len(gets)==1 else 200,headers={'retry-after':'1'} if len(gets)==1 else {},
            content_type='text/html; charset=utf-8',body='<h1>稍后回来</h1>' if len(gets)==1 else '<h1>购买 iPhone 17</h1>')
    await context.route('**/*',respond)
    adapter=AppleAdapter(page,Control(False));config=local_config();config.product.url='https://www.apple.com.cn/shop/product/fixture'
    try:
        await adapter.prepare(config);assert adapter.http_status==200
        assert not await adapter.is_open();await adapter.reload();assert gets==['GET']
        adapter.busy_until=asyncio.get_running_loop().time()-1
        await adapter.reload();assert await adapter.is_open();assert gets==['GET','GET']
    finally:await context.close()


@pytest.mark.e2e
async def test_expired_entry_delay_does_not_make_541_error_heading_a_success(browser):
    context=await browser.new_context();page=await context.new_page();gets=[]
    async def respond(route):
        gets.append(route.request.method)
        index=len(gets)
        await route.fulfill(status=541 if index==2 else 200,content_type='text/html; charset=utf-8',
            body='<h1>稍后回来</h1>' if index==1 else '<h1>很抱歉，你要查找的网页找不到。</h1>' if index==2 else '<h1>购买 iPhone 17</h1>')
    await context.route('**/*',respond);adapter=AppleAdapter(page,Control(False));config=local_config()
    config.product.url='https://www.apple.com.cn/shop/buy-iphone/iphone-17'
    try:
        await adapter.prepare(config);assert not await adapter.is_open()
        await adapter.reload()
        from app.models.flow import RateLimited
        with pytest.raises(RateLimited) as caught:await adapter.is_open()
        assert caught.value.retry_opening
        adapter.limited_until=asyncio.get_running_loop().time()-1
        assert not await adapter.is_open() and gets==['GET','GET']
        await adapter.reload();assert await adapter.is_open() and gets==['GET','GET','GET']
    finally:await context.close()


@pytest.mark.e2e
async def test_slow_opening_document_is_allowed_to_finish_without_a_second_navigation(browser):
    context=await browser.new_context();page=await context.new_page();documents=[];release=asyncio.Event()
    async def respond(route):
        if route.request.url.endswith('/slow.js'):
            await release.wait();await route.fulfill(content_type='application/javascript',body='void 0;')
        else:
            documents.append(route.request.method)
            await route.fulfill(content_type='text/html; charset=utf-8',body='<h1>购买 iPhone 17</h1><script defer src="/slow.js"></script>')
    await context.route('**/*',respond);config=local_config();config.operation_timeout=.08
    config.product.url='https://www.apple.com.cn/shop/product/fixture';adapter=AppleAdapter(page,Control(False))
    try:
        await adapter.prepare(config);assert not await adapter.is_open() and adapter.navigation_pending
        await adapter.reload();assert documents==['GET']
        release.set();await page.wait_for_load_state('domcontentloaded',timeout=2000)
        assert await adapter.is_open() and documents==['GET']
    finally:release.set();await context.close()


@pytest.mark.e2e
async def test_old_account_document_cannot_be_treated_as_recovered_target_product(browser):
    context=await browser.new_context();page=await context.new_page()
    await context.route('**/*',lambda route:route.fulfill(content_type='text/html; charset=utf-8',body='<h1>账户主页</h1>'))
    await page.goto('https://secure.www.apple.com.cn/shop/account/home');adapter=AppleAdapter(page,Control(False))
    adapter.browse_unavailable=True;adapter.target_url='https://www.apple.com.cn/shop/buy-iphone/iphone-duo'
    try:assert not await adapter.is_open()
    finally:await context.close()


@pytest.mark.e2e
async def test_consent_handoff_does_not_check_or_submit_the_apple_agreement(browser):
    context=await browser.new_context();page=await context.new_page();events=[]
    await context.route('**/*',lambda route:route.fulfill(content_type='text/html; charset=utf-8',
        body='<h1>数据隐私知情同意书</h1><label><input type="checkbox">我同意</label><button>同意并继续</button>'))
    await page.goto('https://secure.www.apple.com.cn/shop/checkout');adapter=AppleAdapter(page,Control(False),progress=lambda k,d:events.append((k,d)))
    try:
        with pytest.raises(UserAction,match='同意书'):await adapter.guard()
        with pytest.raises(UserAction):await adapter.guard()
        assert not await page.get_by_role('checkbox').is_checked()
        assert sum(k=='consent_required' for k,d in events)==1
    finally:await context.close()


@pytest.mark.e2e
async def test_real_queue_page_is_observed_without_reloading_and_can_release_itself(browser):
    context=await browser.new_context();page=await context.new_page();gets=[]
    async def respond(route):
        gets.append(route.request.method)
        await route.fulfill(content_type='text/html; charset=utf-8',body='<h1>正在排队，请不要刷新</h1><script>setTimeout(()=>document.body.innerHTML="<h1>购买 iPhone 17</h1>",350)</script>')
    await context.route('**/*',respond);adapter=AppleAdapter(page,Control(False))
    config=local_config();config.product.url='https://www.apple.com.cn/shop/product/fixture'
    try:
        await adapter.prepare(config);assert not await adapter.is_open();assert adapter.in_queue
        await adapter.reload();assert gets==['GET']
        await page.get_by_role('heading',name='购买 iPhone 17').wait_for()
        assert await adapter.is_open() and not adapter.in_queue and gets==['GET']
    finally:await context.close()


@pytest.mark.e2e
async def test_missing_product_url_waits_for_observed_store_entry_instead_of_guessing(browser):
    context=await browser.new_context();page=await context.new_page();gets=[]
    async def respond(route):
        gets.append(route.request.url)
        await route.fulfill(content_type='text/html; charset=utf-8',body='<h1>选购 iPhone</h1><a href="/shop/buy-iphone/iphone-duo">iPhone Duo</a>' if '/iphone-duo' not in route.request.url else '<h1>购买 iPhone Duo</h1>')
    await context.route('**/*',respond);adapter=AppleAdapter(page,Control(False));config=local_config()
    config.product=Product(name='iPhone Duo',url='',price_from_website=True,discover_from_store=True)
    try:
        await adapter.prepare(config);assert await adapter.is_open()
        assert config.product.url=='https://www.apple.com.cn/shop/buy-iphone/iphone-duo'
        assert gets==['https://www.apple.com.cn/shop/buy-iphone',config.product.url]
    finally:await context.close()


@pytest.mark.e2e
async def test_login_return_in_replacement_owned_tab_is_confirmed_without_new_credentials(browser,tmp_path,monkeypatch):
    context=await browser.new_context();page=await context.new_page();events=[];requests=[]
    async def respond(route):
        requests.append(route.request.method)
        if route.request.url=='https://www.apple.com.cn/store':
            body='''<button id="globalnav-menubutton-link-bag" aria-expanded="false" onclick="
            this.setAttribute('aria-expanded','true');document.querySelector('nav').hidden=false">购物袋</button>
            <nav id="globalnav-submenu-bag" hidden><a href="/shop/signIn?ssi=PRIVATE_CONTEXT">登录</a></nav>'''
        elif '/shop/signIn?' in route.request.url:
            body='<h1>登录 Apple Store</h1><input aria-label="Apple 账户">'
        else:body='<a href="/shop/signOut">注销</a>'
        await route.fulfill(content_type='text/html; charset=utf-8',body=body)
    await context.route('**/*',respond)
    session=BrowserSession(tmp_path);session.context=context;session.browser=browser;session.page=page
    worker=Worker('login',local_config(),tmp_path)
    monkeypatch.setattr(worker,'progress',lambda k,d:events.append((k,d)))
    async def finish_login():
        account=await context.new_page();await account.goto('https://secure.www.apple.com.cn/shop/account/home');await page.close()
    monkeypatch.setattr(worker.control,'wait_resume',finish_login)
    try:
        result=await worker.browser_action(session,None)
        assert result['state']=='COMPLETED' and not session.page.is_closed()
        assert any(k=='login' and d.get('authenticated') is True for k,d in events)
        assert requests==['GET','GET','GET']
    finally:await context.close()
