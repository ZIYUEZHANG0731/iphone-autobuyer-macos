"""Real checkout adapter contracts, with every request intercepted locally."""
from types import SimpleNamespace
from contextlib import asynccontextmanager
import json

import pytest
from playwright.async_api import async_playwright

from app.adapters.apple import AppleAdapter
from app.automation.browser import BrowserSession
from app.automation.control import Control
from app.automation.engine import Engine
from app.models.config import Identity, Mode, Product, Settings, Store
from app.models.flow import Availability, FlowError, UserAction
from app.services.storage import Repository
from app.platforms import installed_chrome


REVIEW='''<h1>准备下单了吗？</h1>
<section><h2>取货日期待付款完成后确定。</h2><a href="#pickup">更改零售店</a></section>
<ul><li data-autom="bag-item-fixture"><h2>iPhone 17 256GB 薰衣草紫色</h2><p>数量 1</p></li></ul>
<p data-autom="bagtotalvalue">RMB 6,799</p><img alt="招商银行" src="/bank.svg">
<p>24 个月，每月约 RMB 284</p>
<button onclick="fetch('/shop/checkout/fixture-order',{method:'POST'}).then(()=>location.href='/shop/checkout/thankyou')">立即下单</button>'''

PAYMENT='''<h1>你希望如何付款？</h1>
<label><input type="radio" name="bank" onchange="fetch('/shop/checkout/fixture-bank',{method:'POST'})">招商银行</label>
<label><input type="radio" name="term" onchange="fetch('/shop/checkout/fixture-term',{method:'POST'})">24 期 0% 年化利率</label>
<button onclick="fetch('/shop/checkout/fixture-review',{method:'POST'}).then(()=>document.querySelector('main').innerHTML=window.reviewHTML)">检查订单</button>'''

CONTACT='''<h1>取货联系信息</h1><label>姓氏<input></label><label>名字<input></label>
<label>电子邮件地址<input></label><label>联系人手机号码<input></label>
<label>政府颁发的身份证件号码的后四位<input></label>
<button onclick="fetch('/shop/checkout/fixture-contact',{method:'POST'}).then(()=>document.querySelector('main').innerHTML=window.paymentHTML)">继续选择付款方式</button>'''

PICKUP='''<h1>选择取货零售店</h1>
<label><input type="radio" name="store" value="R359" onchange="fetch('/shop/checkout/fixture-store',{method:'POST'})">Apple 南京东路 今天可取货</label>
<button onclick="fetch('/shop/checkout/fixture-continue',{method:'POST'}).then(()=>document.querySelector('main').innerHTML=window.contactHTML)">继续填写取货详情</button>'''


def config():
    return Settings(product=Product('iPhone 17','https://www.apple.com.cn/shop/buy-iphone/iphone-17/mg704ch/a',
                    {'dimensionColor':'purple','dimensionCapacity':'256GB'},max_total='6799'),
                    stores=[Store('Apple 南京东路',store_id='R359')],payments=['招商银行'],installment_months=24,
                    profile_id='fixture-only-contact',transmit_identity=True,auto_submit=True,
                    keep_awake=False,interval=3,max_wait=3)


@asynccontextmanager
async def checkout_browser(root,kind):
    if kind=='standalone':
        if installed_chrome() is None:
            pytest.skip('Standalone Google Chrome is not installed')
        # Use the app's production, visible, persistent Chrome launch path;
        # this test-only profile never reads the user's real login or bag.
        async with BrowserSession(root,kind='chrome') as session:
            assert session.chrome_executable==installed_chrome()
            assert session.chrome_profile==root/'chrome-profile'
            assert session.label=='Google Chrome（软件独立会话）'
            yield session.context,session.page,session.focus_page
        return
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True)
        try:
            context=await browser.new_context()
            yield context,await context.new_page(),None
        finally:await browser.close()


@pytest.fixture
async def checkout(request,tmp_path):
    async with checkout_browser(tmp_path/'fixture-browser',getattr(request,'param','headless')) as (context,page,focus):
        requests=[]
        async def intercepted(route):
            request=route.request
            if request.method=='POST':
                requests.append(request.url.rsplit('/',1)[-1])
                await route.fulfill(content_type='application/json',body='{}')
            elif request.url.endswith('/thankyou'):
                await route.fulfill(content_type='text/html; charset=utf-8',body='''<h1>你的订单正在等待付款</h1>
                  <p>请在 30 分钟内完成付款</p><a href="#fixture">订单 #W1234567890</a>''')
            elif request.url.endswith('/bank.svg'):
                await route.fulfill(content_type='image/svg+xml',body='<svg xmlns="http://www.w3.org/2000/svg"/>')
            else:
                body='<main>'+PICKUP+'</main><script>window.contactHTML='+json.dumps(CONTACT)+';window.paymentHTML='+json.dumps(PAYMENT)+';window.reviewHTML='+json.dumps(REVIEW)+'</script>'
                await route.fulfill(content_type='text/html; charset=utf-8',body=body)
        await context.route('**/*',intercepted)
        await page.goto('https://secure.www.apple.com.cn/shop/checkout?_s=Fulfillment-init')
        cfg=config();control=Control(False);events=[]
        adapter=AppleAdapter(page,control,mode=Mode.LIVE,progress=lambda k,d:events.append((k,d)),page_focus=focus)
        adapter.selected=Availability(cfg.stores[0],True,'公开库存报价，尚未确认结账门店')
        adapter.options_snapshot=[{'name':'dimensionColor','value':'purple','label':'薰衣草紫色'},
                                  {'name':'dimensionCapacity','value':'256GB','label':'256GB'}]
        identity=Identity('格式测试','居民身份证后四位','0000','00000000000','format@example.com','格','式测试')
        try:yield SimpleNamespace(page=page,context=context,adapter=adapter,control=control,config=cfg,
                                  identity=identity,requests=requests,events=events)
        finally:
            adapter.detach()


@pytest.mark.parametrize('checkout',['headless','standalone'],indirect=True)
@pytest.mark.parametrize('mode',[Mode.LIVE,Mode.WEBSITE_DRY_RUN])
@pytest.mark.parametrize('name_visible',[False,True])
async def test_confirmed_checkout_store_allows_automatic_review_and_one_authorized_submit(checkout,tmp_path,mode,name_visible):
    f=checkout;f.adapter.mode=mode
    if name_visible:
        await f.page.evaluate("()=>window.reviewHTML=window.reviewHTML.replace('<h2>取货日期','<p>Apple 南京东路</p><h2>取货日期')")
    # The fixture starts at checkout; product/stock preparation is outside this
    # contract. Customer, payment, Review and submit use the actual adapter.
    async def prepared(_):pass
    async def open_store():return True
    async def existing_bag(_):return True
    f.adapter.prepare=prepared;f.adapter.select_product=prepared
    f.adapter.is_open=open_store;f.adapter.resume_cart=existing_bag
    repo=Repository(tmp_path/'test.sqlite3')
    result=await Engine(repo,f.control).run(f.config,f.adapter,mode,f.identity)
    assert result.state==('PAYMENT_PENDING' if mode==Mode.LIVE else 'COMPLETED'),result
    assert f.requests.count('fixture-order')==int(mode==Mode.LIVE)
    review=next(data for kind,data in f.events if kind=='review')
    assert review['store_confirmation']=='checkout_saved' and review['store_visible_in_review']==name_visible
    assert 'PAUSED' not in [event['state'] for event in result.steps]
    if mode==Mode.LIVE:
        assert repo.submission(f.config.intent_id)['status']=='CONFIRMED'
        recovered=await Engine(repo,f.control).run(f.config,f.adapter,mode,f.identity)
        assert recovered.order_id==result.order_id and f.requests.count('fixture-order')==1
    else:
        assert repo.submission(f.config.intent_id) is None


@pytest.mark.parametrize('change',[
    'stock_quote_only','continuation_only','changed_selection','changed_page','left_checkout',
    'shipping','different_visible_store','different_product','different_color',
    'different_amount','different_bank','different_term','date_range',
])
async def test_unknown_or_changed_checkout_cannot_submit_even_when_authorized(checkout,change):
    f=checkout
    if change=='continuation_only':
        await f.adapter._select_checkout_pickup(f.config)
    elif change!='stock_quote_only':
        await f.adapter.fill_customer(f.identity,f.config)
    f.adapter.payment='招商银行';f.adapter.installment_months=24
    html=REVIEW
    if change=='changed_selection':
        f.adapter.selected=Availability(Store('Apple 上海环贸 iapm',store_id='R401'),True,'今天可取货')
    elif change=='changed_page':
        f.adapter.bind_page(await f.context.new_page())
        await f.adapter.page.goto('https://secure.www.apple.com.cn/shop/checkout?_s=Review')
    elif change=='left_checkout':
        await f.page.goto('https://www.apple.com.cn/shop/bag')
        await f.page.goto('https://secure.www.apple.com.cn/shop/checkout?_s=Review')
    elif change=='shipping':html=html.replace('更改零售店','更改送货地址')
    elif change=='different_visible_store':html=html.replace('<section>','<section>Apple 上海环贸 iapm')
    elif change=='different_product':html=html.replace('iPhone 17','iPhone 18')
    elif change=='different_color':html=html.replace('薰衣草紫色','白色')
    elif change=='different_amount':html=html.replace('RMB 6,799','RMB 6,999')
    elif change=='different_bank':html=html.replace('alt="招商银行"','alt="支付宝"')
    elif change=='different_term':html=html.replace('24 个月','12 个月')
    elif change=='date_range':f.config.pickup_from='2099-01-01'
    await f.adapter.page.set_content('<main>'+html+'</main>')
    with pytest.raises((UserAction,FlowError)):
        await f.adapter.submit(f.config)
    assert not f.adapter.submit_attempted and 'fixture-order' not in f.requests


async def test_returning_to_fulfillment_invalidates_the_old_confirmation(checkout):
    f=checkout
    await f.adapter.fill_customer(f.identity,f.config)
    assert f.adapter.checkout_pickup_confirmed is not None
    await f.page.set_content('<main>'+PICKUP+'</main>')
    with pytest.raises(UserAction,match='继续已尝试'):
        await f.adapter._select_checkout_pickup(f.config)
    assert f.adapter.checkout_pickup_confirmed is None
