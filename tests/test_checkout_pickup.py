"""Observed checkout variants, entirely intercepted with format-only contacts."""
import asyncio
import json
from time import monotonic
import pytest
from playwright.async_api import async_playwright
from app.adapters.apple import AppleAdapter
from app.adapters.checkout_state import checkout_state
from app.automation.control import Control
from app.models.config import Identity,Settings,Store
from app.models.flow import Availability,UserAction,Stopped
from app.services.consent import NOTICE_TITLE,NOTICE_INTRO,NOTICE_CHOICES,NOTICE_MORE,NOTICE_VERSION


CONTACT='''<h1>取货联系信息</h1>
<label>姓氏<input></label><label>名字<input></label><label>电子邮件地址<input></label>
<label>联系人手机号码<input></label><label>政府颁发的身份证件号码的后四位<input></label>
<button>继续选择付款方式</button>'''


def pickup_html(*,toggle=False,delayed_store=False,disabled=False,numbered=False):
    store=f'''<label><input type="radio" name="store" value="R359" {'disabled' if disabled else ''}
        onchange="document.getElementById('next').disabled=false">{'2 Apple ' if numbered else ''}南京东路 今天可取货</label>'''
    opening='<label><input type="radio" name="fulfillment" checked>我要取货</label>' if toggle else ''
    if delayed_store:
        opening+='''<div id="stores"></div><button onclick="window.moreClicks++;
          setTimeout(()=>document.querySelector('#stores').innerHTML=window.storeHTML,250)">显示更多取货地点</button>'''
    else:opening+=store
    return '<main><h1>选择取货零售店</h1>'+opening+'''
      <button id="next" disabled onclick="window.pickupClicks++;document.querySelector('main').innerHTML=window.contactHTML">继续填写取货详情</button>
      </main><script>window.moreClicks=0;window.pickupClicks=0;window.storeHTML='''+json.dumps(store)+''';window.contactHTML='''+json.dumps(CONTACT)+''';</script>'''


@pytest.fixture
async def checkout_page():
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True);page=await browser.new_page()
        await page.route('**/*',lambda r:r.fulfill(content_type='text/html; charset=utf-8',body=CONTACT))
        await page.goto('https://secure.www.apple.com.cn/shop/checkout?_s=Fulfillment-init')
        try:yield page
        finally:await browser.close()


def settings():
    return Settings(stores=[Store('Apple 南京东路',store_id='R359')],transmit_identity=True,privacy_policy_accepted=True,privacy_transfer_acknowledged=True,
                    privacy_notice_version=NOTICE_VERSION)


def contact():
    return Identity('格式测试','居民身份证后四位','0000','00000000000','format@example.com','格','式测试')


def adapter_for(page,*,control=None,events=None):
    adapter=AppleAdapter(page,control or Control(False),progress=lambda k,d:events.append((k,d)) if events is not None else None)
    adapter.selected=Availability(Store('Apple 南京东路',store_id='R359'),True,'今天可取货')
    adapter.privacy_config=settings()
    return adapter


@pytest.mark.parametrize('toggle,delayed,numbered',[(False,False,False),(True,False,False),(False,True,False),(True,False,True)])
async def test_pickup_mode_without_fixed_toggle_and_delayed_store_list_continue_once(checkout_page,toggle,delayed,numbered):
    page=checkout_page;await page.set_content(pickup_html(toggle=toggle,delayed_store=delayed,numbered=numbered));events=[]
    adapter=adapter_for(page,events=events)
    await adapter.fill_customer(contact(),settings())
    await adapter.fill_customer(contact(),settings())
    assert await page.evaluate('window.pickupClicks')==1
    assert await page.evaluate('window.moreClicks')==int(delayed)
    assert await page.get_by_role('textbox',name='姓氏',exact=True).input_value()=='格'
    assert await page.get_by_role('textbox',name='政府颁发的身份证件号码的后四位',exact=True).input_value()=='0000'
    assert any(k=='checkout_pickup' for k,d in events)
    assert not any('format@example.com' in json.dumps(d) for k,d in events)


def missing_region_html(*,expanded=False,province='上海',city='上海',district='黄浦区'):
    return '''<main><h1>选择取货零售店</h1>
      <label><input id="shipping" type="radio" name="mode" checked>为我送货</label>
      <label><input type="radio" name="mode">我要取货</label>
      <button id="region" aria-label="false" aria-expanded="false" onclick="openRegion()">选择地区</button>
      <div id="tabs" hidden><button role="tab" aria-label="省/市/自治区" aria-selected="true"
        onclick="showPanel('province');this.setAttribute('aria-selected','true')">省/市/自治区</button></div>
      <section id="province" role="tabpanel" hidden><button onclick="window.choices.push(this.textContent);showPanel(window.isMunicipality?'district':'city')">'''+province+'''</button></section>
      <section id="city" role="tabpanel" hidden><button onclick="window.choices.push(this.textContent);showPanel('district')">'''+city+'''</button></section>
      <section id="district" role="tabpanel" hidden><button onclick="window.choices.push(this.textContent);saveRegion()">'''+district+'''</button></section>
      <div id="stores"></div><button id="next" disabled onclick="window.pickupClicks++;
        document.querySelector('main').innerHTML=window.contactHTML">继续填写取货详情</button>
      </main><script>
      window.choices=[];window.regionClicks=0;window.pickupClicks=0;
      window.isMunicipality='''+json.dumps(province==city)+''';
      function showPanel(id){for(const p of document.querySelectorAll('[role=tabpanel]'))p.hidden=p.id!==id;}
      function openRegion(){window.regionClicks++;document.querySelector('#region').setAttribute('aria-expanded','true');
        document.querySelector('#tabs').hidden=false;showPanel('province');}
      async function saveRegion(){document.querySelector('#region').textContent='上海 黄浦区';
        document.querySelector('#region').setAttribute('aria-expanded','false');document.querySelector('#tabs').hidden=true;
        for(const p of document.querySelectorAll('[role=tabpanel]'))p.hidden=true;
        const r=await fetch('/shop/checkout/region-fixture',{method:'POST'});
        if(r.ok)document.querySelector('#stores').innerHTML=window.storeHTML;}
      '''+('openRegion();' if expanded else '')+'''</script>'''


@pytest.mark.parametrize('expanded,geography',[(False,('上海','上海','黄浦区')),
    (True,('上海','上海','黄浦区')),(False,('江苏','苏州','工业园区'))])
async def test_reused_bag_sets_missing_region_before_selecting_allowed_store(checkout_page,expanded,geography):
    page=checkout_page;queries=[];events=[]
    async def save(route):
        queries.append(route.request.method);await asyncio.sleep(.15)
        await route.fulfill(content_type='application/json',body='{}')
    await page.route('**/shop/checkout/region-fixture',save)
    await page.set_content(missing_region_html(expanded=expanded,province=geography[0],city=geography[1],district=geography[2]))
    await page.evaluate('''html=>{window.contactHTML=html;window.storeHTML=`<label><input type="radio" name="store" value="R359"
      onchange="document.querySelector('#next').disabled=false">1 Apple 南京东路 今天可取货</label>`}''',CONTACT)
    config=settings();adapter=adapter_for(page,events=events)
    # Substitute only fixture geography, leaving normal ID/name validation intact.
    enrich=adapter.store_directory.enrich
    def fixture_store(store):
        row=enrich(store);row.province,row.city,row.district=geography;return row
    adapter.store_directory.enrich=fixture_store
    await adapter.fill_customer(contact(),config)
    await adapter.fill_customer(contact(),config)
    assert queries==['POST'] and await page.evaluate('window.regionClicks')==1
    assert await page.evaluate('window.choices')==list(dict.fromkeys(geography))
    assert await page.evaluate('window.pickupClicks')==1
    assert adapter.checkout_pickup_confirmed[1].store_id=='R359'
    assert any(kind=='checkout_region' for kind,data in events)
    assert not adapter.add_attempted


async def test_failed_region_save_preserves_bag_without_continuing(checkout_page):
    page=checkout_page
    await page.route('**/shop/checkout/region-fixture',lambda r:r.fulfill(status=503,body='{}'))
    await page.set_content(missing_region_html())
    adapter=adapter_for(page)
    with pytest.raises(UserAction,match='取货地区的保存'):
        await adapter.fill_customer(contact(),settings())
    assert await page.evaluate('window.pickupClicks')==0
    assert not adapter.add_attempted and not adapter.pickup_continue_attempted


async def test_unknown_region_picker_pauses_without_choosing_shipping(checkout_page):
    page=checkout_page;await page.set_content(missing_region_html())
    await page.get_by_role('tab',name='省/市/自治区',include_hidden=True).evaluate("e=>e.setAttribute('aria-label','未知标签')")
    adapter=adapter_for(page)
    with pytest.raises(UserAction,match='取货省市标签'):
        await adapter.fill_customer(contact(),settings())
    assert await page.evaluate('window.choices')==[]
    assert not await page.locator('#shipping').is_checked()
    assert await page.evaluate('window.pickupClicks')==0


async def test_consent_disappearing_before_fulfillment_loads_is_not_a_pause(checkout_page):
    page=checkout_page
    pickup=pickup_html()
    notice=f'''<main><article><h1>{NOTICE_TITLE}</h1><p>{NOTICE_INTRO[0]}</p><p>{NOTICE_INTRO[1]}</p>
      <label><input type="checkbox">{NOTICE_CHOICES[0]} (在新窗口中打开)</label>
      <label><input type="checkbox">{NOTICE_CHOICES[1]}</label><p>{NOTICE_MORE}</p>
      <button onclick="window.accepts++;document.querySelector('article').remove();
        setTimeout(()=>document.body.innerHTML=window.pickupHTML,650)">同意并继续</button>
      </article></main><script>window.accepts=0;</script>'''
    # innerHTML does not execute script elements, so install the page's
    # synthetic handlers beforehand without any real site requests.
    await page.set_content(notice)
    await page.evaluate('''data=>{window.contactHTML=data.contact;window.pickupHTML=data.pickup;
        window.pickupClicks=0;window.moreClicks=0}''',{'contact':CONTACT,'pickup':pickup})
    events=[];adapter=adapter_for(page,events=events)
    await adapter.fill_customer(contact(),settings())
    assert await page.evaluate('window.accepts')==1
    assert await page.evaluate('window.pickupClicks')==1
    assert adapter.identity_filled
    assert not any(k=='consent_required' for k,d in events)


async def test_continue_activates_the_observed_button_when_page_keyboard_handlers_prevent_enter(checkout_page):
    page=checkout_page
    await page.set_content(pickup_html()+'''<script>
      document.addEventListener('keydown',event=>{if(event.key==='Enter')event.preventDefault()})
      </script>''')
    adapter=adapter_for(page)
    await asyncio.wait_for(adapter.fill_customer(contact(),settings()),3)
    assert await page.evaluate('window.pickupClicks')==1 and adapter.identity_filled


async def test_disabled_target_store_does_not_continue_or_choose_shipping(checkout_page):
    page=checkout_page;await page.set_content(pickup_html(disabled=True))
    with pytest.raises(UserAction,match='目标门店已不可用'):
        await adapter_for(page).fill_customer(contact(),settings())
    assert await page.evaluate('window.pickupClicks')==0


async def test_matching_store_name_with_a_different_store_number_does_not_continue(checkout_page):
    page=checkout_page;await page.set_content(pickup_html(numbered=True).replace('value="R359"','value="R390"'))
    with pytest.raises(UserAction,match='门店编号与目标门店不一致'):
        await adapter_for(page).fill_customer(contact(),settings())
    assert await page.evaluate('window.pickupClicks')==0


def saving_pickup_html():
    return '''<main><h1>选择取货零售店</h1>
      <label><input type="radio" name="store" value="R359" onchange="window.busy=true;
        fetch('/shop/checkout/fulfillment',{method:'POST',body:'synthetic-store-update'})
          .then(r=>r.json()).catch(()=>{}).finally(()=>window.busy=false)">2 Apple 南京东路 今天可取货</label>
      <button onclick="if(window.busy){window.ignored++;return;}window.pickupClicks++;
        document.querySelector('main').innerHTML=window.contactHTML">继续填写取货详情</button>
      </main><script>window.busy=false;window.ignored=0;window.pickupClicks=0;</script>'''


async def test_pickup_continue_waits_for_the_store_update_even_when_the_button_stays_enabled(checkout_page):
    page=checkout_page
    async def update(route):
        await asyncio.sleep(.4)
        await route.fulfill(content_type='application/json',body='{"saved":true}')
    await page.route('**/shop/checkout/fulfillment',update)
    await page.set_content(saving_pickup_html())
    await page.evaluate('html=>window.contactHTML=html',CONTACT)
    adapter=adapter_for(page)
    await asyncio.wait_for(adapter.fill_customer(contact(),settings()),3)
    assert await page.evaluate('window.ignored')==0
    assert await page.evaluate('window.pickupClicks')==1 and adapter.identity_filled


@pytest.mark.parametrize('failure',['server','connection'])
async def test_failed_store_update_preserves_the_bag_and_does_not_click_continue(checkout_page,failure):
    page=checkout_page
    async def update(route):
        await asyncio.sleep(.2)
        if failure=='server':
            await route.fulfill(status=500,content_type='application/json',body='{}')
        else:
            await route.abort('connectionfailed')
    await page.route('**/shop/checkout/fulfillment',update)
    await page.set_content(saving_pickup_html())
    adapter=adapter_for(page)
    with pytest.raises(UserAction,match='取货选择的保存'):
        await asyncio.wait_for(adapter.fill_customer(contact(),settings()),3)
    assert await page.evaluate('window.pickupClicks')==0
    assert not adapter.pickup_continue_attempted and not adapter.add_attempted
    assert adapter.checkout_pickup_confirmed is None
    assert page.url.endswith('Fulfillment-init')


async def test_store_save_wait_does_not_wait_for_a_slow_get_resource(checkout_page):
    page=checkout_page;blocked=asyncio.Event()
    async def resource(route):
        blocked.set()
        await asyncio.Event().wait()
    await page.route('**/noncritical-resource',resource)
    await page.set_content(pickup_html())
    adapter=adapter_for(page)
    await page.evaluate("()=>{fetch('/noncritical-resource').catch(()=>{})}")
    await blocked.wait()
    await asyncio.wait_for(adapter.fill_customer(contact(),settings()),3)
    assert await page.evaluate('window.pickupClicks')==1 and adapter.identity_filled


@pytest.mark.parametrize('interruption',['timeout','stop'])
async def test_waiting_for_a_store_save_remains_interruptible_without_continuing(checkout_page,interruption):
    page=checkout_page;started=asyncio.Event();release=asyncio.Event()
    async def update(route):
        started.set()
        await release.wait()
        await route.fulfill(content_type='application/json',body='{}')
    await page.route('**/shop/checkout/fulfillment',update)
    await page.set_content(saving_pickup_html())
    adapter=adapter_for(page)
    await page.get_by_role('radio',name='2 Apple 南京东路 今天可取货').check()
    await started.wait()
    before=monotonic()
    try:
        if interruption=='stop':
            asyncio.get_running_loop().call_later(.05,adapter.control.cancel_task)
            with pytest.raises(Stopped):await adapter.checkout_updates.ready(adapter.control)
        else:
            with pytest.raises(UserAction,match='仍在保存'):await adapter.checkout_updates.ready(adapter.control,timeout=.05)
        assert monotonic()-before<.4
        assert await page.evaluate('window.pickupClicks')==0
    finally:
        release.set()


def fallback_settings():
    config=settings()
    config.stores.append(Store('Apple 上海环贸 iapm',store_id='R401'))
    return config


def fulfillment_html(*,primary_disabled=False,delayed_backup=False,fail_primary=False,keep_page=False,backup_quote='今天可取货'):
    backup=f'''<label><input type="radio" name="store" value="R401"
        onchange="document.getElementById('next').disabled=false">3 Apple 上海环贸 iapm {backup_quote}</label>'''
    more='''<div id="more-stores"></div><button onclick="window.moreClicks++;
        setTimeout(()=>document.getElementById('more-stores').innerHTML=window.backupHTML,200)">显示更多取货地点</button>''' if delayed_backup else backup
    action='''window.continued.push(document.querySelector('input[name=store]:checked').value);
        if(window.failPrimary && window.continued.at(-1)==='R359') {
          document.querySelector('input[value=R359]').disabled=true;this.disabled=true;
        } else if(!window.keepPage) document.querySelector('main').innerHTML=window.contactHTML;'''
    return f'''<main><h1>选择取货零售店</h1>
        <label><input type="radio" name="store" value="R390" onchange="document.getElementById('next').disabled=false">1 Apple 香港广场 今天可取货</label>
        <label><input type="radio" name="store" value="R359" {'disabled checked' if primary_disabled else ''}
          onchange="document.getElementById('next').disabled=false">2 Apple 南京东路 今天可取货</label>
        {more}<button id="next" disabled onclick="{action}">继续填写取货详情</button></main>
        <script>window.moreClicks=0;window.continued=[];window.failPrimary={json.dumps(fail_primary)};
        window.keepPage={json.dumps(keep_page)};window.contactHTML={json.dumps(CONTACT)};window.backupHTML={json.dumps(backup)};</script>'''


@pytest.mark.parametrize('delayed',[False,True])
async def test_checkout_uses_an_allowed_backup_without_removing_or_readding_the_item(checkout_page,delayed):
    page=checkout_page
    await page.set_content(fulfillment_html(primary_disabled=True,delayed_backup=delayed))
    config=fallback_settings();events=[];adapter=adapter_for(page,events=events)
    await adapter.fill_customer(contact(),config)
    assert adapter.selected.store.store_id=='R401'
    assert await page.evaluate('window.continued')==['R401']
    assert await page.evaluate('window.moreClicks')==int(delayed)
    assert page.url.endswith('Fulfillment-init') and not adapter.add_attempted
    assert any(k=='pickup_changed' and d['store_id']=='R401' for k,d in events)


async def test_checkout_fallback_honors_the_saved_priority_instead_of_dom_order(checkout_page):
    page=checkout_page;await page.set_content(fulfillment_html())
    config=fallback_settings();config.stores.reverse()
    adapter=adapter_for(page)
    await adapter.fill_customer(contact(),config)
    assert adapter.selected.store.store_id=='R401'
    assert await page.evaluate('window.continued')==['R401']


async def test_disabled_preferences_and_unlisted_available_stores_are_not_chosen(checkout_page):
    page=checkout_page;await page.set_content(fulfillment_html(primary_disabled=True))
    config=fallback_settings();config.stores[1].enabled=False
    with pytest.raises(UserAction,match='购物袋保留'):
        await adapter_for(page).fill_customer(contact(),config)
    assert await page.evaluate('window.continued')==[]
    assert (await checkout_state(page))['stage']=='pickup_unavailable'


async def test_stock_rejection_after_fulfillment_continue_switches_stores_in_the_same_checkout(checkout_page):
    page=checkout_page;await page.set_content(fulfillment_html(fail_primary=True))
    adapter=adapter_for(page)
    await asyncio.wait_for(adapter.fill_customer(contact(),fallback_settings()),5)
    assert await page.evaluate('window.continued')==['R359','R401']
    assert adapter.checkout_continued_stores=={'R359','R401'}
    assert adapter.selected.store.store_id=='R401' and adapter.identity_filled


async def test_uncertain_fulfillment_navigation_does_not_repeat_or_switch_an_available_store(checkout_page):
    page=checkout_page;await page.set_content(fulfillment_html(keep_page=True))
    adapter=adapter_for(page);config=fallback_settings()
    await adapter._select_checkout_pickup(config)
    with pytest.raises(UserAction,match='继续已尝试'):
        await adapter._select_checkout_pickup(config)
    assert await page.evaluate('window.continued')==['R359']


async def test_backup_must_meet_the_actual_pickup_date_constraint(checkout_page):
    from datetime import datetime,timedelta
    from zoneinfo import ZoneInfo
    page=checkout_page;await page.set_content(fulfillment_html(backup_quote='明天可取货'))
    config=fallback_settings();config.pickup_from=(datetime.now(ZoneInfo('Asia/Shanghai')).date()+timedelta(days=1)).isoformat()
    adapter=adapter_for(page)
    await adapter.fill_customer(contact(),config)
    assert adapter.selected.store.store_id=='R401' and adapter.selected.pickup_date==config.pickup_from
    assert await page.evaluate('window.continued')==['R401']


@pytest.mark.parametrize('route',['/shop/checkout','/shop/signIn/checkout'])
async def test_guest_privacy_overlay_is_accepted_while_the_background_button_stays_visible(checkout_page,route):
    page=checkout_page
    await page.goto('https://secure9.www.apple.com.cn'+route)
    notice=f'''<article><h1>{NOTICE_TITLE}</h1><p>{NOTICE_INTRO[0]}</p><p>{NOTICE_INTRO[1]}</p>
      <label><input type="checkbox">{NOTICE_CHOICES[0]} (在新窗口中打开)</label>
      <label><input type="checkbox">{NOTICE_CHOICES[1]}</label>
      <p>我们提供包括 Apple 隐私政策在内的更多信息。进一步了解Apple 隐私政策(在新窗口中打开)。</p>
      <button onclick="window.accepts++;document.querySelector('main').innerHTML=window.pickupHTML">同意并继续</button></article>'''
    await page.set_content('''<main><button onclick="window.guestClicks++;
      setTimeout(()=>document.querySelector('main').insertAdjacentHTML('beforeend',window.noticeHTML),250)">以游客身份继续</button></main>''')
    await page.evaluate('''data=>{window.noticeHTML=data.notice;window.pickupHTML=data.pickup;
      window.contactHTML=data.contact;window.guestClicks=0;window.accepts=0;window.pickupClicks=0;window.moreClicks=0}''',
      {'notice':notice,'pickup':pickup_html(numbered=True),'contact':CONTACT})
    events=[];adapter=adapter_for(page,events=events)
    adapter.cart_checked=True
    await asyncio.wait_for(adapter.fill_customer(contact(),settings()),5)
    assert await page.evaluate('window.guestClicks')==1
    assert await page.evaluate('window.accepts')==1
    assert await page.evaluate('window.pickupClicks')==1
    assert adapter.identity_filled
    assert not any(k=='consent_required' for k,d in events)


async def test_dom_observer_wakes_on_the_form_change_without_a_poll_interval(checkout_page):
    page=checkout_page;await page.set_content('<main id="loading">正在载入</main>')
    started=monotonic()
    task=asyncio.create_task(checkout_state(page,wait=1500,wanted=('contact',)))
    await asyncio.sleep(.05)
    await page.evaluate('html=>document.querySelector("main").innerHTML=html',CONTACT)
    snapshot=await task
    assert snapshot['stage']=='contact' and monotonic()-started<.8


async def test_contact_fields_identify_the_stage_without_a_fixed_button_caption_or_background_pickup(checkout_page):
    page=checkout_page
    await page.set_content('''<main><label><input type="radio" checked>我要取货</label>
        <button>以游客身份继续</button>'''+CONTACT.replace('继续选择付款方式','继续')+'</main>')
    snapshot=await checkout_state(page)
    assert snapshot['stage']=='contact' and snapshot['contact_fields']==[
        '姓氏','名字','电子邮件地址','联系人手机号码','政府颁发的身份证件号码的后四位']
    adapter=adapter_for(page)
    with pytest.raises(UserAction,match='已到官网取货人表单'):
        await adapter.fill_customer(None,Settings(transmit_identity=False))


async def test_contact_detection_ignores_hidden_forms_and_never_returns_input_values(checkout_page):
    page=checkout_page
    await page.set_content('<main><section hidden>'+CONTACT+'</section><label>电子邮件地址<input value="Private-Value"></label><button>继续</button></main>')
    snapshot=await checkout_state(page)
    assert snapshot['stage']=='loading' and snapshot['contact_fields']==['电子邮件地址']
    assert 'Private-Value' not in json.dumps({k:v for k,v in snapshot.items() if k!='text'})


async def test_payment_heading_wins_over_retained_fulfillment_controls(checkout_page):
    page=checkout_page
    await page.set_content('<main><h1>你希望如何付款？</h1><label><input type="radio" checked>我要取货</label>'+CONTACT+'</main>')
    assert (await checkout_state(page))['stage']=='payment'


async def test_generic_contact_continue_advances_once_without_relying_on_url_change(checkout_page):
    page=checkout_page
    payment='''<h1>你希望如何付款？</h1><label><input type="radio" name="payment" value="alipay">支付宝</label>'''
    await page.set_content('<main>'+CONTACT.replace('<button>继续选择付款方式</button>',
        '''<button onclick="window.continues++;document.querySelector('main').innerHTML=window.paymentHTML">继续</button>''')+'</main>')
    await page.evaluate('html=>{window.paymentHTML=html;window.continues=0}',payment)
    adapter=adapter_for(page)
    config=Settings(payments=['支付宝'])
    assert await adapter.select_payment(config)=='支付宝'
    assert await adapter.select_payment(config)=='支付宝'
    assert await page.evaluate('window.continues')==1


@pytest.mark.parametrize('failed',[False,True])
async def test_bank_terms_wait_for_checkout_save_instead_of_using_early_or_failed_terms(checkout_page,failed):
    page=checkout_page
    async def save(route):
        await asyncio.sleep(.45)
        await route.fulfill(status=503 if failed else 200,content_type='application/json',body='{}')
    await page.route('**/shop/checkout/billing',save)
    await page.set_content('''<main><h1>你希望如何付款？</h1>
      <label><input type="radio" name="payment" value="installments-fixture" onchange="window.busy=true;
        fetch('/shop/checkout/billing',{method:'POST'}).then(()=>window.busy=false)">招商银行</label>
      <label><input type="radio" name="payment" value="alipay">支付宝</label>
      <label><input id="term" type="radio" name="term" onchange="if(window.busy)window.earlyTerms++">24 期 0% 年化利率</label>
      </main><script>window.busy=false;window.earlyTerms=0</script>''')
    adapter=adapter_for(page)
    config=Settings(payments=['招商银行','支付宝'],installment_months=24)
    if failed:
        with pytest.raises(UserAction,match='付款方式的保存'):
            await adapter.select_payment(config)
        assert not await page.locator('#term').is_checked()
    else:
        assert await adapter.select_payment(config)=='招商银行'
        assert await page.locator('#term').is_checked()
    assert await page.evaluate('window.earlyTerms')==0
    assert not await page.get_by_role('radio',name='支付宝',exact=True).is_checked()


async def test_stop_during_checkout_loading_is_responsive(checkout_page):
    page=checkout_page;await page.set_content('<main>正在载入</main>');control=Control(False)
    adapter=adapter_for(page,control=control)
    task=asyncio.create_task(adapter._wait_checkout_stage(timeout=30))
    await asyncio.sleep(.1);control.cancel_task()
    with pytest.raises(Stopped):await asyncio.wait_for(task,1.5)


async def test_normal_document_navigation_during_observation_waits_for_the_new_form(checkout_page):
    page=checkout_page
    await page.set_content('''<main>正在载入</main><script>
      setTimeout(()=>location.href='/shop/checkout?_s=PickupContact-init',80)</script>''')
    assert await adapter_for(page)._wait_checkout_stage(timeout=3)=='contact'


async def test_closing_the_page_during_observation_pauses_without_sending_a_request(checkout_page):
    page=checkout_page;await page.set_content('<main>正在载入</main>')
    task=asyncio.create_task(adapter_for(page)._wait_checkout_stage(timeout=30))
    await asyncio.sleep(.05);await page.close()
    with pytest.raises(UserAction,match='购买页面已关闭'):await task
