from types import SimpleNamespace
import pytest
from playwright.async_api import async_playwright

from app.adapters.apple import AppleAdapter
from app.automation.control import Control
from app.models.config import Mode,Store
from app.models.flow import Availability,FlowError,UserAction
from app.services.simulation import local_config


@pytest.mark.parametrize('mode,model,bag_model,quantity,adopt',[
    (Mode.WEBSITE_DRY_RUN,'iPhone 17','iPhone 17',1,True),
    (Mode.WEBSITE_DRY_RUN,'iPhone 17','iPhone 17 Pro',1,False),
    (Mode.WEBSITE_DRY_RUN,'iPhone 17','iPhone 17e',1,False),
    (Mode.WEBSITE_DRY_RUN,'iPhone 18 Pro','iPhone 18 Pro Max',1,False),
    (Mode.WEBSITE_DRY_RUN,'iPhone 17','iPhone 17',2,False),
    (Mode.LIVE,'iPhone 17','iPhone 17',1,False),
])
async def test_trial_adopts_only_exact_existing_item_without_bag_mutations(mode,model,bag_model,quantity,adopt):
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
        store=Store('Apple 南京东路','上海','R359');cfg.stores=[store]
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
