from types import SimpleNamespace
import pytest
from playwright.async_api import async_playwright, Locator, TimeoutError as PlaywrightTimeout

from app.adapters.apple import AppleAdapter
from app.automation.control import Control
from app.models.config import Mode,Store
from app.models.flow import Availability,FlowError,UserAction
from app.services.simulation import local_config
from app.automation.engine import Engine
from app.models.flow import Summary
from app.services.storage import Repository
from decimal import Decimal


async def test_add_click_timeout_recovers_the_written_bag_without_readding(monkeypatch):
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True);context=await browser.new_context();page=await context.new_page()
        adds=[]
        async def serve(route):
            path=route.request.url.split('?')[0]
            if path.endswith('/shop/bag/add-fixture'):
                adds.append(route.request.method)
                await route.fulfill(content_type='application/json',body='{}');return
            if path.endswith('/shop/bag'):
                html=('''<h1>购物袋总计 RMB 6,799</h1><h2>iPhone 17 256GB 白色</h2>
                    <label>数量<select><option value="1" selected>1</option></select></label>
                    <button>移除 iPhone 17 256GB 白色</button><button>结账</button>'''
                    if adds else '<h1>你的购物袋中没有商品。</h1>')
            elif path.endswith('/shop/buy-iphone/accessories'):
                html='<button onclick="location.href=\'/shop/bag\'">查看购物袋</button>'
            else:
                html='''<h1>购买 iPhone 17</h1><button onclick="fetch('/shop/bag/add-fixture',{method:'POST'})
                    .then(r=>r.json()).then(()=>location.href='/shop/buy-iphone/accessories')">添加到购物袋</button>'''
            await route.fulfill(content_type='text/html; charset=utf-8',body=html)
        await context.route('**/*',serve)
        config=local_config();config.product.name='iPhone 17';config.product.max_total='6799'
        config.product.url='https://www.apple.com.cn/shop/buy-iphone/iphone-17'
        config.product.options={}
        await page.goto(config.product.url)
        original_click=Locator.click
        async def acknowledged_late(locator,*args,**kwargs):
            is_add=await locator.count()==1 and await locator.inner_text()=='添加到购物袋'
            await original_click(locator,*args,**kwargs)
            if is_add:raise PlaywrightTimeout('Navigation acknowledgment timed out after one click')
        monkeypatch.setattr(Locator,'click',acknowledged_late)
        adapter=AppleAdapter(page,Control(False))
        try:
            await adapter.select_pickup(Availability(Store('Apple 南京东路',store_id='R359'),True,'今天可取货'),config)
            assert adds==['POST']
            assert page.url.endswith('/shop/bag') and adapter.cart_checked
            assert await page.get_by_role('combobox',name='数量').input_value()=='1'
        finally:
            adapter.detach();await browser.close()


@pytest.mark.parametrize('mode,model,bag_model,quantity,adopt',[
    (Mode.WEBSITE_DRY_RUN,'iPhone 17','iPhone 17',1,True),
    (Mode.WEBSITE_DRY_RUN,'iPhone 17','iPhone 17 Pro',1,False),
    (Mode.WEBSITE_DRY_RUN,'iPhone 17','iPhone 17e',1,False),
    (Mode.WEBSITE_DRY_RUN,'iPhone 18 Pro','iPhone 18 Pro Max',1,False),
    (Mode.WEBSITE_DRY_RUN,'iPhone 17','iPhone 17',2,False),
    (Mode.LIVE,'iPhone 17','iPhone 17',1,True),
    (Mode.LIVE,'iPhone 17','iPhone 17 Pro',1,False),
    (Mode.LIVE,'iPhone 17','iPhone 17',2,False),
])
async def test_trial_and_live_adopt_only_exact_existing_item_without_bag_mutations(mode,model,bag_model,quantity,adopt):
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True);context=await browser.new_context();page=await context.new_page()
        requests=[]
        async def serve(route):
            requests.append((route.request.method,route.request.url))
            if route.request.url.endswith('/shop/bag'):
                html=f'''<h1>购物袋总计 RMB 6,799.00</h1><h2>{bag_model} 256GB 白色</h2>
                  <label>数量<select onchange="fetch('/unexpected-quantity',{{method:'POST'}})">
                  <option value="1" {'selected' if quantity==1 else ''}>1</option>
                  <option value="2" {'selected' if quantity==2 else ''}>2</option></select></label>
                  <button onclick="fetch('/unexpected-remove',{{method:'POST'}})">移除 iPhone</button><button>结账</button>'''
            else:
                html='<h1>购买 iPhone</h1><button onclick="fetch(\'/unexpected-add\',{method:\'POST\'})">添加到购物袋</button>'
            await route.fulfill(content_type='text/html; charset=utf-8',body=html)
        await context.route('**/*',serve)
        cfg=local_config();cfg.product.name=model;cfg.product.url='https://www.apple.com.cn/shop/buy-iphone/iphone-17'
        cfg.product.options={'dimensionColor':'white','dimensionCapacity':'256gb'};cfg.product.max_total='6799.00'
        store=Store('Apple 南京东路','上海',store_id='R359');cfg.stores=[store]
        await page.goto(cfg.product.url)
        adapter=AppleAdapter(page,Control(False),mode=mode)
        adapter.options_snapshot=[{'name':'dimensionColor','value':'white','label':'白色'},
                                  {'name':'dimensionCapacity','value':'256gb','label':'256GB RMB 6,799'}]
        try:
            if adopt:
                await adapter.select_pickup(Availability(store,True,'今天可取货'),cfg)
                assert adapter.cart_checked and page.url.endswith('/shop/bag')
                assert await page.get_by_role('combobox',name='数量').input_value()=='1'
                previous=len(requests)
                await adapter.select_pickup(Availability(store,True,'今天可取货'),cfg)
                assert len(requests)==previous
            else:
                with pytest.raises((FlowError,UserAction)):
                    await adapter.select_pickup(Availability(store,True,'今天可取货'),cfg)
                assert not adapter.cart_checked
            assert all(method=='GET' for method,url in requests)
            assert len(context.pages)==1
        finally:await browser.close()


async def test_resuming_a_bag_skips_product_stock_and_emits_the_actual_checkout_store(tmp_path):
    config=local_config();config.auto_submit=False
    config.product.url='https://www.apple.com.cn/shop/buy-iphone/iphone-17'
    primary=Store('Apple 南京东路','上海',store_id='R359');backup=Store('Apple 上海环贸 iapm','上海',store_id='R401')
    config.stores=[primary,backup]
    calls=[]
    async def record(name):calls.append(name)
    async def resume(config):calls.append('resume_cart');return True
    async def forbidden(*args):raise AssertionError('Existing bag must not go through stock/add again')
    async def customer(identity,config):
        calls.append('customer');adapter.selected=Availability(backup,True,'今天可取货')
    async def review(config):
        calls.append('review')
        return Summary(config.product.name,config.product.options,config.product.quantity,Decimal(config.product.max_total),
            backup.name,backup.store_id,config.payments[0],verified=True,installment_months=config.installment_months)
    async def opened():return True
    adapter=SimpleNamespace(prepare=lambda c:record('prepare'),is_open=opened,select_product=lambda c:record('select_product'),
        resume_cart=resume,availability=forbidden,select_pickup=forbidden,fill_customer=customer,
        select_payment=lambda c:record('payment'),review=review,selected=None)
    result=await Engine(Repository(tmp_path/'db.sqlite'),Control(False)).run(config,adapter,Mode.WEBSITE_DRY_RUN)
    assert result.state=='COMPLETED',result.message
    assert calls==['prepare','select_product','resume_cart','customer','payment','review']
    pickup=next(step for step in result.steps if step['state']=='PICKUP_SELECTED')
    assert backup.name in pickup['message'] and primary.name not in pickup['message']


@pytest.mark.parametrize('contents',['delayed','empty','actual_empty','partial','unknown'])
async def test_loading_bag_is_not_mistaken_for_an_empty_bag(contents):
    import json
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True);context=await browser.new_context();page=await context.new_page()
        requests=[]
        item='<h2>iPhone 17 256GB 白色</h2><label>数量<select><option value="1">1</option></select></label><button>移除 iPhone</button>'
        html={'delayed':'<h1>购物袋总计 RMB 6,799.00</h1><div id="items"></div><script>setTimeout(()=>document.getElementById("items").innerHTML='+json.dumps(item)+',150)</script>',
              'empty':'<h1>你的购物袋是空的。</h1>',
              'actual_empty':'<h1>你的购物袋中没有商品。</h1>',
              'partial':'<h1>购物袋总计 RMB 6,799.00</h1><button>移除 iPhone</button><div id="items"></div><script>setTimeout(()=>{document.querySelector("button").remove();document.getElementById("items").innerHTML='+json.dumps(item)+'},600)</script>',
              'unknown':'<h1>购物袋正在加载</h1>'}[contents]
        async def serve(route):
            requests.append(route.request.method)
            await route.fulfill(content_type='text/html; charset=utf-8',body=html)
        await context.route('**/*',serve)
        await page.goto('https://www.apple.com.cn/shop/bag')
        config=local_config();config.product.name='iPhone 17';config.product.max_total='6799.00'
        config.product.options={'dimensionColor':'white','dimensionCapacity':'256gb'}
        config.stores=[Store('Apple 南京东路',store_id='R359')]
        adapter=AppleAdapter(page,Control(False),mode=Mode.LIVE)
        adapter.options_snapshot=[{'name':'dimensionColor','value':'white','label':'白色'},
                                  {'name':'dimensionCapacity','value':'256gb','label':'256GB'}]
        try:
            if contents=='unknown':
                with pytest.raises(UserAction,match='不能判定为空'):
                    await adapter.resume_cart(config,timeout=.25)
                assert not adapter.add_attempted
            else:
                assert await adapter.resume_cart(config)==(contents in ('delayed','partial'))
                assert adapter.cart_checked==(contents in ('delayed','partial'))
            assert all(method=='GET' for method in requests) and len(context.pages)==1
        finally:await browser.close()


async def test_post_add_transient_empty_state_is_not_a_failed_add():
    import asyncio,json
    from app.adapters.bag_state import bag_has_items
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True);page=await browser.new_page()
        item='<h1>购物袋总计 RMB 6,799.00</h1><h2>iPhone 17 256GB 白色</h2><label>数量<select><option value="1">1</option></select></label><button>移除 iPhone</button>'
        await page.set_content('<h1>你的购物袋中没有商品。</h1><script>setTimeout(()=>document.body.innerHTML='+json.dumps(item)+',1200)</script>')
        async def guard(page):pass
        started=asyncio.get_running_loop().time()
        try:
            assert await bag_has_items(page,Control(False),guard,timeout=4,expect_items=True)
            assert asyncio.get_running_loop().time()-started>=2
        finally:await browser.close()


@pytest.mark.parametrize('quantity',[1,2])
async def test_add_waits_for_quantity_save_and_does_not_rewrite_unchanged_quantity(quantity):
    import asyncio,json
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True);context=await browser.new_context();page=await context.new_page()
        saved=asyncio.Event();quantity_posts=[]
        bag='''<h1 id="total">购物袋总计 RMB 6,799.00</h1><h2>iPhone 17 256GB 白色</h2>
          <label>数量<select onchange="window.quantityChanges++;fetch('/shop/bag/quantity',{method:'POST'}).then(()=>document.getElementById('total').innerText='购物袋总计 RMB 13,598.00')"><option value="1">1</option><option value="2">2</option></select></label>
          <button>移除 iPhone</button><button>结账</button><script>window.quantityChanges=0</script>'''
        async def serve(route):
            path=route.request.url
            if path.endswith('/shop/bag/quantity'):
                quantity_posts.append(route.request.method)
                await asyncio.sleep(.5)
                await route.fulfill(content_type='application/json',body='{}');saved.set()
            elif path.endswith('/shop/bag'):
                await route.fulfill(content_type='text/html; charset=utf-8',body=bag)
            else:
                await route.fulfill(content_type='text/html; charset=utf-8',body='<button onclick="location.href=\'/shop/bag\'">查看购物袋</button>')
        await context.route('**/*',serve)
        await page.goto('https://www.apple.com.cn/shop/buy-iphone/iphone-17')
        cfg=local_config();cfg.product.name='iPhone 17';cfg.product.quantity=quantity;cfg.product.max_total=str(6799*quantity)
        cfg.product.options={'dimensionColor':'white','dimensionCapacity':'256gb'}
        adapter=AppleAdapter(page,Control(False));adapter.add_attempted=True
        adapter.options_snapshot=[{'name':'dimensionColor','value':'white','label':'白色'},{'name':'dimensionCapacity','value':'256gb','label':'256GB'}]
        try:
            await adapter.select_pickup(Availability(Store('Apple 南京东路',store_id='R359'),True,'今天可取货'),cfg)
            assert adapter.cart_checked and adapter.bag_total==Decimal(6799*quantity)
            assert quantity_posts==(['POST'] if quantity==2 else [])
            assert await page.evaluate('window.quantityChanges')==(1 if quantity==2 else 0)
            if quantity==2:assert saved.is_set()
        finally:adapter.detach();await browser.close()
