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
    return Settings(transmit_identity=True,privacy_policy_accepted=True,privacy_transfer_acknowledged=True,
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
