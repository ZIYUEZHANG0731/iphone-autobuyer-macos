from __future__ import annotations

import asyncio
import json
import pytest
from playwright.async_api import async_playwright

from app.adapters.apple import AppleAdapter
from app.automation.control import Control
from app.models.config import Store
from app.models.flow import RateLimited, UserAction
from app.services.simulation import local_config


# Roles and labels observed in Apple's 2026-10-11 public product modal.
# Every request is intercepted; this fixture sends nothing to Apple.
STOCK_PAGE = '''<main><h1>购买 iPhone 17</h1>
<button disabled>Apple 南京东路</button>
<button onclick="document.querySelector('[role=dialog]').hidden=false">ENTRY</button>
<section role="dialog" aria-label="iPhone 供货情况" hidden>
<h2>iPhone 供货情况</h2><span>显示此地提供的选项：</span>
<button id="region" aria-expanded="false" onclick="openRegion()">上海</button>
<div role="tablist" id="regionTabs" hidden>
<button role="tab" id="provinceTab" aria-label="上海 省份" aria-selected="true" onclick="document.getElementById('district').hidden=true;document.getElementById('province').hidden=false;this.setAttribute('aria-selected','true')">上海</button>
</div>
<section role="tabpanel" aria-label="省份" id="province" hidden>
<button onclick="document.getElementById('province').hidden=true;document.getElementById('district').hidden=false">上海</button>
</section>
<section role="tabpanel" aria-label="区" id="district" hidden>
<button onclick="readFresh()">黄浦区</button>
</section>
<div role="group" aria-label="门店" id="stores">
<label><input type="radio" name="store-R359" value="R359" disabled><div>Apple 南京东路</div><div>上海, 上海</div><div>暂不可取货</div></label>
</div>
<button onclick="window.checkoutClicks=(window.checkoutClicks||0)+1">继续</button>
<button aria-label="关闭" onclick="document.querySelector('[role=dialog]').hidden=true"></button>
</section></main><script>
function openRegion(){
const closing=document.getElementById('region').getAttribute('aria-expanded')==='true';
if(closing){document.getElementById('region').setAttribute('aria-expanded','false');
document.getElementById('province').hidden=true;document.getElementById('district').hidden=true;document.getElementById('regionTabs').hidden=true;return;}
document.getElementById('region').setAttribute('aria-expanded','true');
document.getElementById('regionTabs').hidden=false;
document.getElementById('province').hidden=!!window.regionHasSelection;
document.getElementById('district').hidden=!window.regionHasSelection;
document.getElementById('provinceTab').setAttribute('aria-selected',window.regionHasSelection?'false':'true');
}
async function readFresh(){
window.regionHasSelection=true;
document.getElementById('regionTabs').hidden=true;
document.getElementById('district').hidden=true;
document.getElementById('region').setAttribute('aria-expanded','false');
document.getElementById('stores').replaceChildren();
const data=await (await fetch('/shop/fixture-stock')).json();
const label=document.createElement('label');
const input=document.createElement('input');input.type='radio';input.name='store-R359';input.value='R359';input.disabled=!data.available;
label.append(input);
for (const text of ['Apple 南京东路','上海, 上海',data.available?'今天可取货':'暂不可取货']){
const row=document.createElement('div');row.textContent=text;label.append(row);
}
document.getElementById('stores').append(label);
}
</script>'''


@pytest.fixture
async def browser():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        yield browser
        await browser.close()


async def stock_fixture(browser, entry='Apple 南京东路', *, status=200):
    context = await browser.new_context()
    page = await context.new_page()
    page.set_default_timeout(1000)
    queries = []
    async def respond(route):
        if route.request.url.endswith('/fixture-stock'):
            queries.append(route.request.method)
            await asyncio.sleep(.1)
            await route.fulfill(status=status, content_type='application/json',
                                body=json.dumps({'available':len(queries)>1}))
        else:
            await route.fulfill(content_type='text/html; charset=utf-8', body=STOCK_PAGE.replace('ENTRY',entry))
    await context.route('**/*',respond)
    await page.goto('https://www.apple.com.cn/shop/buy-iphone/offline-stock')
    config = local_config()
    config.stores = [Store('Apple 南京东路',store_id='R359')]
    return context, page, config, queries


@pytest.mark.e2e
@pytest.mark.parametrize('entry',['Apple 南京东路','查看供货情况'])
async def test_each_stock_check_refreshes_region_before_publishing_a_timestamp(browser,entry):
    context,page,config,queries = await stock_fixture(browser,entry)
    events=[]
    adapter=AppleAdapter(page,Control(False),progress=lambda kind,data:events.append((kind,data)))
    try:
        first=await adapter.availability(config)
        assert queries==['GET'] and not first[0].available
        second=await adapter.availability(config)
        assert queries==['GET','GET'] and second[0].available
        assert first[0].store.store_id==second[0].store.store_id=='R359'
        assert len([e for e in events if e[0]=='stores'])==2
        assert await page.evaluate('window.checkoutClicks||0')==0
    finally:
        await context.close()


@pytest.mark.e2e
async def test_unknown_region_controls_do_not_relabel_cached_stock_as_fresh(browser):
    context,page,config,queries=await stock_fixture(browser)
    events=[]
    await page.locator('#region').evaluate("e=>{e.removeAttribute('aria-expanded');e.textContent='未知区域入口'}")
    adapter=AppleAdapter(page,Control(False),progress=lambda kind,data:events.append((kind,data)))
    try:
        with pytest.raises(UserAction):
            await adapter.availability(config)
        assert not queries and not any(kind=='stores' for kind,_ in events)
    finally:
        await context.close()


@pytest.mark.e2e
async def test_stock_modal_skeleton_waits_for_its_heading_before_querying(browser):
    context,page,config,queries=await stock_fixture(browser,entry='查看供货情况')
    await page.locator('h2').evaluate('e=>e.hidden=true')
    await page.get_by_role('button',name='查看供货情况',exact=True).evaluate('''e=>e.onclick=()=>{
        document.querySelector('[role=dialog]').hidden=false;
        setTimeout(()=>document.querySelector('h2').hidden=false,300);
    }''')
    try:
        states=await AppleAdapter(page,Control(False)).availability(config)
        assert queries==['GET'] and len(states)==1 and not states[0].available
    finally:
        await context.close()


@pytest.mark.e2e
async def test_rate_limited_stock_read_does_not_publish_old_results_or_retry(browser):
    context,page,config,queries=await stock_fixture(browser,status=429)
    events=[]
    adapter=AppleAdapter(page,Control(False),progress=lambda kind,data:events.append((kind,data)))
    try:
        with pytest.raises(RateLimited):
            await adapter.availability(config)
        assert queries==['GET'] and not any(kind=='stores' for kind,_ in events)
        with pytest.raises(RateLimited):
            await adapter.availability(config)
        assert queries==['GET']
    finally:
        await context.close()


@pytest.mark.e2e
async def test_critical_rejection_while_modal_opens_keeps_rate_limit_reason(browser):
    context,page,config,queries=await stock_fixture(browser,status=541)
    await page.get_by_role('button',name='Apple 南京东路',exact=True).last.evaluate(
        "e=>e.onclick=()=>fetch('/shop/fixture-stock')")
    events=[]
    adapter=AppleAdapter(page,Control(False),progress=lambda kind,data:events.append((kind,data)))
    try:
        with pytest.raises(RateLimited,match='541'):
            await adapter.availability(config)
        assert queries==['GET'] and not any(kind=='stores' for kind,_ in events)
    finally:
        await context.close()


@pytest.mark.e2e
async def test_stock_query_after_checkout_does_not_return_a_cached_inventory(browser):
    context,page,config,queries=await stock_fixture(browser)
    adapter=AppleAdapter(page,Control(False));adapter.cart_checked=True
    adapter.cached_availability=[]
    try:
        with pytest.raises(UserAction):
            await adapter.availability(config)
        assert not queries
    finally:
        await context.close()


@pytest.mark.e2e
async def test_missing_pickup_entry_never_clicks_global_apple_watch_navigation(browser):
    context,page,config,queries=await stock_fixture(browser)
    await page.set_content('''<nav><button onclick="window.navigationClicks=(window.navigationClicks||0)+1">Apple Watch 目录</button></nav>
        <main><h1>购买 iPhone 17</h1><p>结账时会显示你所在地区的送货详情。</p></main>''')
    events=[]
    adapter=AppleAdapter(page,Control(False),progress=lambda kind,data:events.append((kind,data)))
    adapter.delivery_preview_failed=True
    try:
        with pytest.raises(UserAction,match='这不是无货结果'):
            await adapter.availability(config)
        assert await page.evaluate('window.navigationClicks||0')==0
        assert not queries and not any(kind in ('stores','stock_query') for kind,_ in events)
    finally:
        await context.close()


async def test_no_stock_checks_back_off_without_entering_checkout_or_changing_session(tmp_path):
    from decimal import Decimal
    from types import SimpleNamespace
    from app.automation.engine import Engine
    from app.models.config import Mode, Settings
    from app.models.flow import Availability, Summary
    from app.services.storage import Repository

    config=Settings();config.product.max_total='7000';config.stores=[Store('离线取货店')]
    config.interval=5;config.max_interval=20
    delays=[];calls=[]
    class FastControl(Control):
        async def sleep(self,seconds):
            self.check()
            if seconds>1:delays.append(seconds)
            await asyncio.sleep(0)
    async def prepare(_):calls.append('prepare')
    async def open_page():return True
    async def product(_):calls.append('product')
    async def availability(_):
        calls.append('stock')
        return [Availability(config.stores[0],calls.count('stock')==5,'离线控件结果')]
    async def pickup(*_):calls.append('pickup')
    async def customer(*_):calls.append('customer')
    async def payment(_):calls.append('payment')
    async def review(_):
        return Summary(config.product.name,config.product.options,1,Decimal('100'),
                       config.stores[0].name,'',config.payments[0],verified=True)
    adapter=SimpleNamespace(prepare=prepare,is_open=open_page,select_product=product,
        availability=availability,select_pickup=pickup,fill_customer=customer,
        select_payment=payment,review=review,supports_submit=False)
    result=await Engine(Repository(tmp_path/'db.sqlite'),FastControl(False)).run(config,adapter,Mode.WEBSITE_DRY_RUN)
    assert result.state=='COMPLETED'
    assert delays==[10,18,20,20]
    assert calls==['prepare','product']+['stock']*5+['pickup','customer','payment']
