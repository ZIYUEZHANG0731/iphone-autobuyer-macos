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
from app.services.payment_methods import payment_months


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


async def run_checkout(f,repo,mode):
    f.adapter.mode=mode
    # Product/stock preparation is outside this checkout contract.
    async def prepared(_):pass
    async def open_store():return True
    async def existing_bag(_):return True
    f.adapter.prepare=prepared;f.adapter.select_product=prepared
    f.adapter.is_open=open_store;f.adapter.resume_cart=existing_bag
    return await Engine(repo,f.control).run(f.config,f.adapter,mode,f.identity)


@pytest.mark.parametrize('checkout',['headless','standalone'],indirect=True)
@pytest.mark.parametrize('mode',[Mode.LIVE,Mode.WEBSITE_DRY_RUN])
@pytest.mark.parametrize('name_visible',[False,True])
async def test_confirmed_checkout_store_allows_automatic_review_and_one_authorized_submit(checkout,tmp_path,mode,name_visible):
    f=checkout;f.adapter.mode=mode
    if name_visible:
        await f.page.evaluate("()=>window.reviewHTML=window.reviewHTML.replace('<h2>取货日期','<p>Apple 南京东路</p><h2>取货日期')")
    repo=Repository(tmp_path/'test.sqlite3')
    result=await run_checkout(f,repo,mode)
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


async def configure_payment(f,method,months,*,label=None,fallback=False):
    f.config.payments=(['招商银行',method] if fallback else [method])
    f.config.installment_months=months
    actual=payment_months(method,months)
    label=label or method
    payment='<h1>你希望如何付款？</h1>'
    if fallback:payment+='<label><input type="radio" disabled>招商银行</label>'
    payment+='<label><input type="radio" name="bank" value="'+('installments-fixture' if actual else 'wallet-fixture')+'" onchange="fetch(\'/shop/checkout/fixture-bank\',{method:\'POST\'})"><img alt="'+label+'"></label>'
    if actual:
        payment+='<label><input type="radio" name="term" onchange="fetch(\'/shop/checkout/fixture-term\',{method:\'POST\'})">'+str(actual)+' 期 0% 年化利率</label>'
    payment+='<button onclick="fetch(\'/shop/checkout/fixture-review\',{method:\'POST\'}).then(()=>document.querySelector(\'main\').innerHTML=window.reviewHTML)">检查订单</button>'
    review=REVIEW.replace('alt="招商银行"','alt="'+label+'"').replace('24 个月，每月约 RMB 284',str(actual)+' 个月，每月约 RMB 284' if actual else '')
    await f.page.evaluate('data=>{window.paymentHTML=data.payment;window.reviewHTML=data.review}',{'payment':payment,'review':review})


@pytest.mark.parametrize('mode',[Mode.LIVE,Mode.WEBSITE_DRY_RUN])
@pytest.mark.parametrize(('method','months'),[
    ('支付宝',24),('微信支付',24),('花呗分期',3),('微信分付',6),
    ('招商银行',6),('中国建设银行',12),('中国工商银行',3),
    ('Apple Pay',24),('信用卡',24),('电汇',24),
])
async def test_each_verified_payment_can_finish_without_the_cmb24_gate(checkout,tmp_path,mode,method,months):
    f=checkout;await configure_payment(f,method,months)
    repo=Repository(tmp_path/'multi-payment.sqlite3')
    result=await run_checkout(f,repo,mode)
    assert result.state==('PAYMENT_PENDING' if mode==Mode.LIVE else 'COMPLETED'),result
    actual=payment_months(method,months)
    review=next(data for kind,data in f.events if kind=='review')
    assert review['payment']==method and review['installment_months']==actual
    assert f.requests.count('fixture-term')==int(actual>0)
    assert f.requests.count('fixture-order')==int(mode==Mode.LIVE)
    if mode==Mode.LIVE:
        recovered=await Engine(repo,f.control).run(f.config,f.adapter,mode,f.identity)
        assert recovered.order_id==result.order_id and f.requests.count('fixture-order')==1
    else:assert repo.submission(f.config.intent_id) is None


@pytest.mark.parametrize('checkout',['standalone'],indirect=True)
@pytest.mark.parametrize(('method','months','label'),[
    ('支付宝',24,'Alipay'),('微信支付',24,'WeChat Pay'),
    ('中国建设银行',6,'建设银行'),('花呗分期',12,'花呗分期'),
])
async def test_standalone_chrome_submits_wallets_and_other_terms_once(checkout,tmp_path,method,months,label):
    f=checkout;await configure_payment(f,method,months,label=label)
    result=await run_checkout(f,Repository(tmp_path/'chrome-payment.sqlite3'),Mode.LIVE)
    assert result.state=='PAYMENT_PENDING',result
    assert f.requests.count('fixture-order')==1


@pytest.mark.parametrize('method',['支付宝','微信支付'])
async def test_full_payment_backup_does_not_inherit_bank_installments(checkout,tmp_path,method):
    f=checkout;await configure_payment(f,method,24,fallback=True)
    result=await run_checkout(f,Repository(tmp_path/'backup.sqlite3'),Mode.LIVE)
    assert result.state=='PAYMENT_PENDING' and f.adapter.installment_months==0,result
    assert 'fixture-term' not in f.requests and f.requests.count('fixture-order')==1


@pytest.mark.parametrize(('method','review_label','review_months'),[
    ('支付宝','花呗分期',0),('微信支付','微信分付',0),
    ('支付宝','支付宝',24),('微信支付','微信支付',6),
    ('中国建设银行','中国工商银行',6),('中国建设银行','中国建设银行',12),
])
async def test_changed_wallet_or_bank_scheme_cannot_submit(checkout,method,review_label,review_months):
    f=checkout;f.config.payments=[method];f.config.installment_months=6
    await f.adapter.fill_customer(f.identity,f.config)
    f.adapter.payment=method;f.adapter.installment_months=payment_months(method,6)
    review=REVIEW.replace('alt="招商银行"','alt="'+review_label+'"').replace(
        '24 个月，每月约 RMB 284',str(review_months)+' 个月，每月约 RMB 284' if review_months else '')
    await f.page.set_content('<main>'+review+'</main>')
    with pytest.raises((UserAction,FlowError)):await f.adapter.submit(f.config)
    assert not f.adapter.submit_attempted and 'fixture-order' not in f.requests


@pytest.mark.parametrize('terms',[
    '<label><input type="radio">6 期 0% 年化利率</label>'*2,
    '<label><input type="radio" disabled>6 期 0% 年化利率</label>',
])
async def test_ambiguous_or_disabled_installment_is_not_selected(checkout,terms):
    f=checkout;f.config.payments=['花呗分期'];f.config.installment_months=6
    await f.page.set_content('<main><h1>你希望如何付款？</h1><label><input type="radio" value="installments-fixture">花呗分期</label>'+terms+'</main>')
    with pytest.raises(UserAction):await f.adapter.select_payment(f.config)
    assert not f.adapter.submit_attempted and 'fixture-order' not in f.requests


async def test_slow_review_can_resume_without_clicking_the_transition_twice(checkout):
    f=checkout
    await f.adapter.fill_customer(f.identity,f.config)
    await f.adapter.select_payment(f.config)
    await f.page.get_by_role('button',name='检查订单').evaluate('button=>button.onclick=()=>{window.reviewClicks=(window.reviewClicks||0)+1;document.querySelector("h1").textContent="正在加载";setTimeout(()=>document.querySelector("main").innerHTML=window.reviewHTML,900)}')
    observe=f.adapter._wait_checkout_stage;budgets=[]
    async def bounded_observation(**kwargs):
        budgets.append(kwargs['timeout'])
        return await observe(**{**kwargs,'timeout':.05 if len(budgets)==1 else 2})
    f.adapter._wait_checkout_stage=bounded_observation
    with pytest.raises(UserAction,match='不重复点击检查订单'):
        await f.adapter.review(f.config)
    # The old Billing button remains visible during the delayed transition.
    assert await f.page.get_by_role('button',name='检查订单').is_visible()
    summary=await f.adapter.review(f.config)
    summary.authorize(f.config)
    assert min(budgets)>=45 and await f.page.evaluate('window.reviewClicks')==1
    assert not f.adapter.submit_attempted and 'fixture-order' not in f.requests


@pytest.mark.parametrize('change',[
    'stock_quote_only','continuation_only','changed_selection','changed_page','left_checkout',
    'shipping','different_visible_store','different_product','different_color',
    'different_amount','different_bank','different_term','disabled_submit','date_range',
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
    elif change=='disabled_submit':html=html.replace('<button onclick=','<button disabled onclick=')
    elif change=='date_range':f.config.pickup_from='2099-01-01'
    await f.adapter.page.set_content('<main>'+html+'</main>')
    with pytest.raises((UserAction,FlowError)):
        await f.adapter.submit(f.config)
    assert not f.adapter.submit_attempted and 'fixture-order' not in f.requests


@pytest.mark.parametrize('change',['amount','color','bank','duplicate_total'])
async def test_submission_reads_current_review_again_after_the_previous_summary_was_valid(checkout,change):
    f=checkout
    await f.adapter.fill_customer(f.identity,f.config)
    await f.adapter.select_payment(f.config)
    (await f.adapter.review(f.config)).authorize(f.config)
    if change=='amount':await f.page.locator('[data-autom="bagtotalvalue"]').evaluate("e=>e.textContent='RMB 6,999'")
    elif change=='color':await f.page.locator('li[data-autom^="bag-item-"] h2').evaluate("e=>e.textContent='iPhone 17 256GB 白色'")
    elif change=='bank':await f.page.get_by_role('img',name='招商银行').evaluate("e=>e.alt='支付宝'")
    else:await f.page.locator('[data-autom="bagtotalvalue"]').evaluate('e=>e.after(e.cloneNode(true))')
    with pytest.raises((UserAction,FlowError)):
        await f.adapter.submit(f.config)
    assert not f.adapter.submit_attempted and 'fixture-order' not in f.requests


async def test_hidden_accessibility_content_does_not_become_an_extra_product_or_payment(checkout):
    f=checkout
    await f.adapter.fill_customer(f.identity,f.config)
    await f.adapter.select_payment(f.config)
    await f.page.evaluate('''()=>{window.reviewHTML=window.reviewHTML.replace('</li>',
        '<h2 aria-hidden="true">iPhone 18 1TB</h2></li>')+
        '<img alt="招商银行" aria-hidden="true" src="/bank.svg">'}''')
    summary=await f.adapter.review(f.config)
    summary.authorize(f.config)
    assert not f.adapter.submit_attempted and 'fixture-order' not in f.requests


async def test_returning_to_fulfillment_invalidates_the_old_confirmation(checkout):
    f=checkout
    await f.adapter.fill_customer(f.identity,f.config)
    assert f.adapter.checkout_pickup_confirmed is not None
    await f.page.set_content('<main>'+PICKUP+'</main>')
    with pytest.raises(UserAction,match='继续已尝试'):
        await f.adapter._select_checkout_pickup(f.config)
    assert f.adapter.checkout_pickup_confirmed is None
