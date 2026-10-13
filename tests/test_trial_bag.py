from types import SimpleNamespace
import pytest
from playwright.async_api import async_playwright

from app.adapters.apple import AppleAdapter
from app.automation.control import Control
from app.models.config import Mode,Store
from app.models.flow import Availability,FlowError,UserAction
from app.services.simulation import local_config
from app.automation.engine import Engine
from app.models.flow import Summary
from app.services.storage import Repository
from decimal import Decimal


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


async def test_bag_diagnostic_records_public_mismatch_without_contact_fields_or_url_tokens():
    from app.adapters.bag_state import bag_details
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True);page=await browser.new_page()
        html='''<h1>购物袋总计 RMB 6,799.00</h1>
          <ul><li data-autom="bag-item-fixture"><h2><a href="https://www.apple.com.cn/shop/product/mg704ch/a?secret=private-token">
          iPhone 17 256GB 薰衣草紫色</a></h2><label>数量<select><option value="1">1</option></select></label>
          <button>移除 iPhone</button></li></ul><h2 hidden>Hidden item</h2>
          <a href="https://www.apple.com.cn/shop/product/mg6x4ch/a">推荐白色</a>
          <input name="email" value="private@example.com"><input type="password" value="private-password">
          <p>私人联系电话 12345678900</p><a href="https://example.org/private-token">账户资料</a>'''
        await page.route('**/*',lambda route:route.fulfill(content_type='text/html; charset=utf-8',body=html))
        await page.goto('https://www.apple.com.cn/shop/bag')
        cfg=local_config();cfg.product.name='iPhone 17';cfg.product.options={'dimensionColor':'white','dimensionCapacity':'256gb'}
        events=[];adapter=AppleAdapter(page,Control(False),progress=lambda k,d:events.append((k,d)))
        adapter.options_snapshot=[{'name':'dimensionColor','value':'white','label':'白色'},
                                  {'name':'dimensionCapacity','value':'256gb','label':'256GB RMB 6,799'}]
        try:
            details=await bag_details(page)
            assert details=={'titles':['iPhone 17 256GB 薰衣草紫色'],'skus':['mg704ch/a'],
                'quantities':['1'],'remove_count':1,'total':'购物袋总计 RMB 6,799.00'}
            with pytest.raises(FlowError,match='配置与选中配置不一致'):
                await adapter._validate_bag(cfg)
            diagnostic=next(d for k,d in events if k=='bag_diagnostic')
            assert diagnostic['expected']['labels']==['白色','256GB']
            assert diagnostic['actual']==details
            assert not any(value in str(diagnostic) for value in ('private-token','private@example.com','private-password','12345678900'))
        finally:await browser.close()


@pytest.mark.parametrize('owned,sku,quantity,supported,changed,prepared',[
    (True,'mg704ch',1,True,False,True),
    (False,'mg704ch',1,True,False,False),
    (True,'mg6x4ch',1,True,False,False),
    (True,'',1,True,False,False),
    (True,'mg704ch',2,True,False,False),
    (True,'mg704ch',1,False,False,False),
    (True,'mg704ch',1,True,True,False),
])
async def test_preparing_owned_bag_preserves_old_configuration_and_checks_fresh_merchandise(
        owned,sku,quantity,supported,changed,prepared):
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True);context=await browser.new_context();page=await context.new_page()
        requests=[];saved=[];bag_reads=[]
        product='''<main><h1>购买 iPhone 17</h1>
          <label><input type="radio" id="white" name="dimensionColor" value="white" checked>白色</label>
          <label><input type="radio" id="256" name="dimensionCapacity" value="256gb" checked>256GB</label>
          <button>添加到购物袋</button></main>'''
        async def serve(route):
            r=route.request;requests.append((r.method,r.url))
            if r.url.endswith('/fixture-save'):
                assert r.method=='POST';saved.append(sku)
                await route.fulfill(content_type='application/json',body='{}');return
            if r.url.endswith('/shop/bag'):
                bag_reads.append(r.url)
                actual_sku='mg714ch' if changed and len(bag_reads)>1 else sku
                link=f'<a href="https://www.apple.com.cn/shop/product/{actual_sku}/a">iPhone 17 256GB 薰衣草紫色</a>' if actual_sku else 'iPhone 17 256GB 薰衣草紫色'
                item=f'''<ul><li data-autom="bag-item-fixture"><h2>{link}</h2>
                  <label>数量<select><option value="{quantity}">{quantity}</option></select></label><button>移除 iPhone</button>'''
                if supported:item+='''<button onclick="fetch('/fixture-save',{method:'POST'}).then(()=>document.querySelector('main').innerHTML='<h1>你的购物袋是空的。</h1><p>已移入收藏</p>')">移入收藏 iPhone</button>'''
                item+='</li></ul>'
                html='<main><h1>购物袋总计 RMB 6,799.00</h1>'+item+'</main>' if not saved else '<h1>你的购物袋是空的。</h1>'
            else:html=product
            await route.fulfill(content_type='text/html; charset=utf-8',body=html)
        await context.route('**/*',serve)
        cfg=local_config();cfg.product.name='iPhone 17'
        cfg.product.url='https://www.apple.com.cn/shop/buy-iphone/iphone-17/mg6x4ch/a'
        cfg.product.options={'dimensionColor':'white','dimensionCapacity':'256gb'};cfg.product.max_total='6799.00'
        await page.goto(cfg.product.url)
        events=[];adapter=AppleAdapter(page,Control(False),mode=Mode.LIVE,prepare_bag=owned,progress=lambda k,d:events.append((k,d)))
        await adapter.options()
        try:
            if prepared:
                assert not await adapter.resume_cart(cfg)
                assert saved==[sku] and page.url==cfg.product.url
                assert not adapter.cart_checked and not adapter.add_attempted
                assert sum(k=='bag_prepared' for k,d in events)==1
                # A second observation of the now-empty bag cannot replay Save.
                assert not await adapter.resume_cart(cfg)
                assert saved==[sku]
            else:
                with pytest.raises((FlowError,UserAction)):await adapter.resume_cart(cfg)
                assert not saved and not adapter.add_attempted
            assert [url for method,url in requests if method!='GET']==(
                ['https://www.apple.com.cn/fixture-save'] if prepared else [])
            assert len(context.pages)==1
        finally:await browser.close()


@pytest.mark.parametrize('attachment',['delayed','direct'])
async def test_add_waits_for_late_attachment_page_without_repeating_add(attachment):
    import json
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True);context=await browser.new_context();page=await context.new_page()
        added=[];requests=[]
        bag='''<h1>购物袋总计 RMB 6,799.00</h1><h2>iPhone 17 256GB 白色</h2>
          <label>数量<select><option value="1">1</option></select></label><button>移除 iPhone</button>'''
        async def serve(route):
            r=route.request;requests.append(r.method)
            if r.url.endswith('/fixture-add'):
                added.append(r.method);await route.fulfill(content_type='application/json',body='{}');return
            if r.url.endswith('/shop/bag'):
                body=bag if added else '<h1>你的购物袋是空的。</h1>'
            elif r.url.endswith('/shop/fixture-attachment'):
                button='<button onclick="location.href=\'/shop/bag\'">查看购物袋</button>'
                body='<h1>配件页面正在加载</h1><script>setTimeout(()=>document.body.innerHTML='+json.dumps(button)+',8500)</script>'
            else:
                target='/shop/fixture-attachment' if attachment=='delayed' else '/shop/bag'
                body=f'''<h1>购买 iPhone 17</h1><button onclick="fetch('/fixture-add',{{method:'POST'}}).then(()=>location.href='{target}')">添加到购物袋</button>'''
            await route.fulfill(content_type='text/html; charset=utf-8',body=body)
        await context.route('**/*',serve)
        cfg=local_config();cfg.product.name='iPhone 17';cfg.product.quantity=1;cfg.product.max_total='6799.00'
        cfg.product.url='https://www.apple.com.cn/shop/buy-iphone/iphone-17/mg6x4ch/a'
        cfg.product.options={'dimensionColor':'white','dimensionCapacity':'256gb'}
        store=Store('Apple 南京东路',store_id='R359');cfg.stores=[store]
        await page.goto(cfg.product.url)
        adapter=AppleAdapter(page,Control(False));adapter.options_snapshot=[
            {'name':'dimensionColor','value':'white','label':'白色'},
            {'name':'dimensionCapacity','value':'256gb','label':'256GB'}]
        try:
            await adapter.select_pickup(Availability(store,True,'今天可取货'),cfg)
            assert adapter.cart_checked and page.url.endswith('/shop/bag')
            await adapter.select_pickup(Availability(store,True,'今天可取货'),cfg)
            assert added==['POST'] and requests.count('POST')==1 and len(context.pages)==1
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


@pytest.mark.parametrize('contents',['delayed','empty','current_empty','unknown'])
async def test_loading_bag_is_not_mistaken_for_an_empty_bag(contents):
    import json
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True);context=await browser.new_context();page=await context.new_page()
        requests=[]
        item='<h2>iPhone 17 256GB 白色</h2><label>数量<select><option value="1">1</option></select></label><button>移除 iPhone</button>'
        html={'delayed':'<h1>购物袋总计 RMB 6,799.00</h1><div id="items"></div><script>setTimeout(()=>document.getElementById("items").innerHTML='+json.dumps(item)+',150)</script>',
              'empty':'<h1>你的购物袋是空的。</h1>',
              'current_empty':'<h1>你的购物袋中没有商品。</h1>',
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
                assert await adapter.resume_cart(config)==(contents=='delayed')
                assert adapter.cart_checked==(contents=='delayed')
            assert all(method=='GET' for method in requests) and len(context.pages)==1
        finally:await browser.close()
