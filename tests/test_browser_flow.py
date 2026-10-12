from __future__ import annotations
import asyncio
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import pytest
from playwright.async_api import async_playwright
from app.models.config import Mode,State,Identity
from app.adapters.simulation import SimulationAdapter
from app.adapters.apple import AppleAdapter
from app.automation.engine import Engine
from app.automation.control import Control
from app.services.storage import Repository
from app.services.simulation import SimStore,local_config

@pytest.fixture
async def browser():
    async with async_playwright() as p:
        b=await p.chromium.launch(headless=True)
        yield b
        await b.close()

@pytest.mark.e2e
@pytest.mark.parametrize(('scenario','expected','count'),[
 ('first_available','PAYMENT_PENDING',1),('second_available','PAYMENT_PENDING',1),
 ('all_unavailable','FAILED',0),('stock_changes','PAYMENT_PENDING',1),('missing_option','FAILED',0),
 ('payment_unavailable','PAUSED',0),('identity_incomplete','FAILED',0),('maintenance','PAYMENT_PENDING',1),
 ('network_timeout','PAYMENT_PENDING',1),('submit_timeout','ORDER_UNCERTAIN',1),
 ('delayed_success','PAYMENT_PENDING',1),('order_failure','ORDER_UNCERTAIN',1),('rate_limited','PAUSED',0),
])
async def test_scenarios_in_real_browser(browser,tmp_path,scenario,expected,count):
    cfg=local_config(scenario)
    with SimStore(cfg,scenario) as server:
        context=await browser.new_context()
        try:
            page=await context.new_page();page.set_default_timeout(2000)
            control=Control(interactive=False);repo=Repository(tmp_path/'db.sqlite')
            engine=Engine(repo,control)
            identity=None if scenario=='identity_incomplete' else Identity.synthetic()
            result=await engine.run(cfg,SimulationAdapter(page,server,control),Mode.LOCAL_TEST,identity)
            assert result.state==expected,result
            assert server.submit_count==count
            if scenario=='second_available':assert any('模拟二店' in e['message'] for e in result.steps)
            if count:
                state=repo.submission(engine.intent)
                assert state['status']==('CONFIRMED' if result.order_id else 'UNKNOWN')
            assert all(e['mode']=='local_test' for e in result.steps)
        finally:await context.close()

@pytest.mark.e2e
async def test_website_dry_run_cannot_submit_even_if_authorized(browser,tmp_path):
    cfg=local_config();cfg.product.url='https://www.apple.com.cn/shop/product/test';cfg.interval=3;cfg.max_interval=60;cfg.auto_submit=True
    with SimStore(cfg) as server:
        context=await browser.new_context();page=await context.new_page()
        try:
            result=await Engine(Repository(tmp_path/'db.sqlite'),Control(False)).run(cfg,SimulationAdapter(page,server,Control(False)),Mode.WEBSITE_DRY_RUN,Identity.synthetic())
            # The synthetic identity is deliberately rejected for real modes before any browsing.
            assert result.state=='FAILED'
            assert server.submit_count==0
        finally:await context.close()

@pytest.mark.e2e
async def test_dry_run_reaches_review_without_any_submit(browser,tmp_path):
    cfg=local_config();cfg.product.url='https://www.apple.com.cn/shop/product/test';cfg.interval=3;cfg.max_interval=60;cfg.auto_submit=True
    with SimStore(cfg) as server:
        context=await browser.new_context();page=await context.new_page();control=Control(False)
        adapter=SimulationAdapter(page,server,control)
        # Test the engine's real-mode boundary without transmitting a real identity anywhere.
        async def no_sensitive_customer(identity,config):await SimulationAdapter.fill_customer(adapter,Identity.synthetic(),config)
        adapter.fill_customer=no_sensitive_customer
        try:
            result=await Engine(Repository(tmp_path/'db.sqlite'),control).run(cfg,adapter,Mode.WEBSITE_DRY_RUN)
            assert result.state=='COMPLETED',result
            assert server.submit_count==0 and not server.orders
        finally:await context.close()

@pytest.mark.e2e
async def test_stop_while_waiting_is_prompt(browser,tmp_path):
    cfg=local_config('stop')
    with SimStore(cfg,'stop') as server:
        context=await browser.new_context();page=await context.new_page();control=Control(False)
        async def stop():await asyncio.sleep(.4);control.stop()
        stop_task=asyncio.create_task(stop())
        try:
            result=await Engine(Repository(tmp_path/'db.sqlite'),control).run(cfg,SimulationAdapter(page,server,control),Mode.LOCAL_TEST,Identity.synthetic())
            assert result.state=='STOPPED'
            assert server.submit_count==0
        finally:await stop_task;await context.close()


@pytest.mark.e2e
async def test_task_stop_after_submission_preserves_unknown_lock_and_never_retries(browser,tmp_path):
    cfg=local_config('submit_timeout');cfg.product.url='https://www.apple.com.cn/shop/product/offline-fixture'
    cfg.profile_id='offline-keychain-reference';cfg.transmit_identity=True;cfg.auto_submit=True
    with SimStore(cfg,'submit_timeout') as server:
        context=await browser.new_context();page=await context.new_page();control=Control(False)
        adapter=SimulationAdapter(page,server,control)
        # All identity filling remains on this local fixture, never on Apple.
        async def local_customer(_,config):await SimulationAdapter.fill_customer(adapter,Identity.synthetic(),config)
        adapter.fill_customer=local_customer
        repo=Repository(tmp_path/'db.sqlite')
        def stop_after_claim(event):
            if event['state']=='ORDER_SUBMITTING':control.cancel_task()
        try:
            result=await Engine(repo,control,callback=stop_after_claim).run(cfg,adapter,Mode.LIVE)
            assert result.state=='ORDER_UNCERTAIN' and server.submit_count==1,result
            assert repo.submission(cfg.intent_id)['status']=='UNKNOWN'
            assert not control.stop_event.is_set()
            control.reset_task()
            recovered=await Engine(repo,control).run(cfg,adapter,Mode.LIVE)
            assert recovered.state=='ORDER_UNCERTAIN' and server.submit_count==1
        finally:await context.close()

@pytest.mark.e2e
async def test_apple_submit_is_unavailable_and_never_clicks(browser):
    context=await browser.new_context();page=await context.new_page();control=Control(False)
    adapter=AppleAdapter(page,control)
    assert adapter.supports_submit is False
    from app.models.flow import UserAction
    with pytest.raises(UserAction):await adapter.submit(local_config())
    await context.close()

@pytest.mark.e2e
async def test_image_label_bank_and_exact_installment_in_offline_fixture(browser):
    context=await browser.new_context();page=await context.new_page()
    # Offline DOM fixture reproduces the real image-only labels and hidden bank terms.
    await page.set_content('''<main><h1>你希望如何付款？</h1>
      <input id="bank" name="payment" value="installments-fixture" type="radio" aria-labelledby="bank_label">
      <label id="bank_label" for="bank"><img alt="招商银行"></label>
      <input id="pay" name="payment" value="alipay" type="radio"><label for="pay"><img alt="支付宝"></label>
      <input id="term12" name="terms" type="radio"><label for="term12">12 期 0% 年化利率 RMB 567/月 总计 RMB 6,799</label>
      <input id="term24" name="terms" type="radio"><label for="term24">24 期 0% 年化利率 RMB 284/月 总计 RMB 6,799</label>
      <div hidden><input name="other-terms" type="radio" checked><label>24 期 另一银行</label></div></main>''')
    adapter=AppleAdapter(page,Control(False));cfg=local_config();cfg.payments=['招商银行'];cfg.installment_months=24
    async def offline_guard():pass
    adapter.guard=offline_guard
    try:
        assert await adapter.select_payment(cfg)=='招商银行'
        assert await page.locator('#bank').is_checked()
        assert await page.locator('#term24').is_checked()
        assert not await page.locator('#term12').is_checked()
        assert adapter.installment_months==24
    finally:await context.close()


@pytest.mark.e2e
async def test_product_continue_is_clicked_once_when_site_requires_it(browser):
    context=await browser.new_context();page=await context.new_page()
    html='''<main>
      <input id="color" name="dimensionColor" value="white" type="radio" checked><label for="color">白色</label>
      <input id="cap" name="dimensionCapacity" value="256gb" type="radio" checked><label for="cap">256GB RMB 6,799</label>
      <section id="stage"><button onclick="window.continues++;document.querySelector('#stage').replaceChildren(document.querySelector('#final').content.cloneNode(true))">继续</button></section>
      <template id="final"><input id="trade" type="radio"><label for="trade">不折抵换购</label>
        <input id="care" type="radio" onchange="document.querySelector('#bag').disabled=false"><label for="care">不加 AppleCare+ 服务计划</label>
        <button id="bag" disabled>添加到购物袋</button></template>
      <script>window.continues=0;</script></main>'''
    cfg=local_config();cfg.product.url='https://www.apple.com.cn/shop/buy-iphone/iphone-17'
    cfg.product.options={'dimensionColor':'white','dimensionCapacity':'256gb'};cfg.product.max_total='7000'
    await page.route('**/*',lambda route:route.fulfill(content_type='text/html; charset=utf-8',body=html))
    await page.goto(cfg.product.url)
    adapter=AppleAdapter(page,Control(False))
    async def offline_guard():pass
    adapter.guard=offline_guard
    try:
        await adapter.select_product(cfg)
        assert await page.evaluate('window.continues')==1
        assert await page.locator('#bag').is_enabled()
    finally:await context.close()

@pytest.mark.e2e
async def test_styled_radio_and_late_applecare_are_selected_in_offline_fixture(browser):
    context=await browser.new_context();page=await context.new_page()
    await page.set_content('''<main><input id="color" name="dimensionColor" value="white" type="radio"><label for="color">白色</label>
      <input id="cap" name="dimensionCapacity" value="256gb" type="radio"><label for="cap">256GB RMB 6,799</label>
      <input id="trade" type="radio" onchange="setTimeout(()=>{document.querySelector('#caregroup').hidden=false},150)"><label for="trade">不折抵换购</label>
      <div id="caregroup" hidden><input id="care" type="radio" onchange="document.querySelector('button').disabled=false"><label for="care">不加 AppleCare+ 服务计划</label></div>
      <button disabled>添加到购物袋</button></main>''')
    adapter=AppleAdapter(page,Control(False));cfg=local_config();cfg.product.options={'dimensionColor':'white','dimensionCapacity':'256gb'};cfg.product.max_total='7000'
    async def offline_guard():pass
    adapter.guard=offline_guard
    try:
        await adapter.select_product(cfg)
        assert await page.locator('#color').is_checked() and await page.locator('#cap').is_checked()
        assert await page.locator('#care').is_checked()
        assert await page.get_by_role('button',name='添加到购物袋').is_enabled()
    finally:await context.close()


@pytest.mark.e2e
async def test_product_continue_replaced_by_bag_after_capacity_change(browser):
    context=await browser.new_context();page=await context.new_page()
    # The generic Apple model page can change stages after the capacity action.
    # A disabled Continue disappears while the final product controls render.
    html='''<main>
      <input id="color" name="dimensionColor" value="white" type="radio" checked><label for="color">白色</label>
      <input id="cap" name="dimensionCapacity" value="256gb" type="radio"><label for="cap">256GB RMB 6,799</label>
      <section id="stage"><button disabled>继续</button></section>
      <template id="final"><input id="trade" type="radio"><label for="trade">不折抵换购</label>
        <input id="care" type="radio" onchange="document.querySelector('#bag').disabled=false"><label for="care">不加 AppleCare+ 服务计划</label>
        <button id="bag" disabled>添加到购物袋</button></template>
      <script>document.querySelector('#cap').addEventListener('change',()=>setTimeout(()=>{
        document.querySelector('#stage').replaceChildren(document.querySelector('#final').content.cloneNode(true));
      },150));</script></main>'''
    adapter=AppleAdapter(page,Control(False));cfg=local_config()
    cfg.product.url='https://www.apple.com.cn/shop/buy-iphone/iphone-17'
    cfg.product.options={'dimensionColor':'white','dimensionCapacity':'256gb'}
    cfg.product.max_total='7000'
    await page.route('**/*',lambda route:route.fulfill(content_type='text/html; charset=utf-8',body=html))
    await page.goto(cfg.product.url)
    async def offline_guard():pass
    adapter.guard=offline_guard
    try:
        await adapter.select_product(cfg)
        assert await page.locator('#trade').is_checked()
        assert await page.locator('#care').is_checked()
        assert await page.locator('#bag').is_enabled()
    finally:await context.close()
