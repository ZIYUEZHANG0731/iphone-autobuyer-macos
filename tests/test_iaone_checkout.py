"""Count requests in the actual adapter; all Apple URLs are intercepted."""
import asyncio
import json
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

from app.adapters.apple import AppleAdapter
from app.automation.control import Control
from app.models.config import Identity, Product, Settings, Store


async def test_restock_goes_straight_to_bag_then_chooses_current_store_and_slot():
    url='https://www.apple.com.cn/shop/buy-iphone/iphone-duo/mk2m4ch/a'
    config=Settings(product=Product(name='iPhone Duo',url=url,max_total='100',options={
        'dimensionColor':'starwhite','dimensionCapacity':'256gb'}),
        stores=[Store('Apple 南京东路',store_id='R359')],transmit_identity=True)
    contact='''<h1>取货联系信息</h1><label>姓氏<input></label><label>名字<input></label>
      <label>电子邮件地址<input></label><label>联系人手机号码<input></label>
      <label>政府颁发的身份证件号码的后四位<input></label><button>继续选择付款方式</button>'''
    product='''<main><h1>购买 iPhone Duo</h1><button onclick="fetch('/fixture-add',{method:'POST'})
      .then(()=>location.href='/shop/bag')">添加到购物袋</button>
      <button onclick="fetch('/forbidden-stock-query')">查看可取货零售店</button></main>'''
    bag='''<main><h1>购物袋总计 RMB 100.00</h1><h2>iPhone Duo 256GB Star White</h2>
      <label>数量<select><option value="1">1</option></select></label><button>移除 iPhone</button>
      <button onclick="location.href='https://secure9.www.apple.com.cn/shop/checkout?_s=Fulfillment-init'">结账</button></main>'''
    fulfillment='''<main><h1>选择取货零售店</h1>
      <label><input type="radio" name="store" value="R359" onchange="window.stores++;
        fetch('/shop/checkout/fulfillment',{method:'POST'}).then(()=>{
          document.getElementById('times').innerHTML=window.slotHTML;
          setTimeout(()=>document.getElementById('slot').insertAdjacentHTML('beforeend',
            '<option value=morning>10:15 – 10:30</option>'),120)
        })">Apple 南京东路 今天可取货</label><div id="times"></div>
      <button id="next" disabled onclick="window.continued++;
        document.querySelector('main').innerHTML=window.contactHTML">继续填写取货详情</button></main>
      <script>window.stores=0;window.slots=0;window.continued=0;</script>'''
    slot_html='''<label>签到时段<select id="slot" required onchange="window.slots++;
      fetch('/shop/checkout/fulfillment',{method:'POST'}).then(()=>document.getElementById('next').disabled=false)">
      <option value="" disabled selected>可选时段</option></select></label>'''
    fulfillment+='<script>window.contactHTML='+json.dumps(contact)+';window.slotHTML='+json.dumps(slot_html)+';</script>'
    requests=[];added=[]
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True)
        context=await browser.new_context()
        async def serve(route):
            request=route.request;path=urlsplit(request.url).path
            requests.append((request.method,path))
            if path=='/fixture-add':
                added.append(True)
                await route.fulfill(content_type='application/json',body='{}');return
            if request.method=='POST' and path=='/shop/checkout/fulfillment':
                await route.fulfill(content_type='application/json',body='{}');return
            body=product
            if path=='/shop/bag':body=bag if added else '<h1>你的购物袋是空的。</h1>'
            elif path=='/shop/checkout':body=fulfillment
            await route.fulfill(content_type='text/html; charset=utf-8',body=body)
        await context.route('**/*',serve)
        page=await context.new_page()
        await page.goto(url)
        adapter=AppleAdapter(page,Control(False))
        adapter.options_snapshot=[{'name':'dimensionColor','value':'starwhite','label':'Star White'},
                                  {'name':'dimensionCapacity','value':'256gb','label':'256GB'}]
        try:
            await asyncio.wait_for(adapter.begin_checkout(config),4)
            assert adapter.cart_checked and not adapter.selected.available
            # Time options are only supplied after selecting the actual store.
            identity=Identity('格式测试','居民身份证后四位','0000','00000000000','format@example.com','格','式测试')
            await asyncio.wait_for(adapter.fill_customer(identity,config),5)
            assert adapter.identity_filled and adapter.selected.available
            assert adapter.checkout_pickup_confirmed[1].store_id=='R359'
            assert await page.evaluate('window.stores')==1
            assert await page.evaluate('window.slots')==1
            assert await page.evaluate('window.continued')==1
            await adapter.begin_checkout(config)
            assert len(added)==1
            assert requests.count(('GET','/shop/bag'))==2
            assert requests.count(('POST','/shop/checkout/fulfillment'))==2
            assert not any('stock' in path or 'fulfillment-messages' in path for _,path in requests)
            assert len(context.pages)==1
        finally:
            adapter.detach()
            await browser.close()
