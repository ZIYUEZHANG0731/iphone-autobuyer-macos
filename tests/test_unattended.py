"""Recover reads and completed verification without repeating a write."""
import asyncio
from types import SimpleNamespace

import pytest
from playwright.async_api import async_playwright

from app.adapters.apple import AppleAdapter
from app.automation.browser import BrowserSession
from app.automation.control import Control
from app.automation.engine import Engine
from app.models.flow import RetryableRead, Stopped, UserAction
from app.services.storage import Repository


@pytest.mark.e2e
@pytest.mark.parametrize('status',[500,502,503,504])
async def test_failed_checkout_get_recovers_without_another_bag_or_checkout_write(tmp_path,status):
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True)
        page=await browser.new_page();requests=[];waits=[]
        async def route(r):
            requests.append(r.request.method)
            failed=len(requests)==1
            await r.fulfill(status=status if failed else 200,
                headers={'retry-after':'7'} if failed and status==503 else {},
                content_type='text/html',body='<h1>Temporarily unavailable</h1>' if failed else '<h1>Pickup contact</h1>')
        await page.route('**/*',route)
        control=Control(False)
        original_sleep=control.sleep
        async def fast_wait(seconds):
            waits.append(seconds)
            await original_sleep(seconds)
        control.sleep=fast_wait
        adapter=AppleAdapter(page,control)
        adapter.add_attempted=adapter.cart_checked=True
        saved=object();adapter.checkout_pickup_confirmed=saved
        try:
            await page.goto('https://secure7.www.apple.com.cn/shop/checkout')
            await Engine(Repository(tmp_path/'db.sqlite'),control).operation(adapter.guard)
            assert requests==['GET','GET']
            assert adapter.add_attempted and adapter.cart_checked
            assert adapter.checkout_pickup_confirmed is saved
            assert not adapter.submit_attempted and adapter.last_document_status==200
            assert any(s>=6 for s in waits) if status==503 else any(s==5 for s in waits)
        finally:adapter.detach();await browser.close()


@pytest.mark.parametrize('method,submit,path,status,blocked',[
    ('POST',False,'/shop/checkout',500,False),
    ('GET',True,'/shop/checkout',500,False),
    ('GET',False,'/shop/signIn',500,False),
    ('GET',False,'/shop/checkout',403,False),
    ('GET',False,'/shop/checkout',500,True),
])
async def test_retry_never_replays_a_write_authentication_submit_or_blocked_page(method,submit,path,status,blocked):
    page=SimpleNamespace(url='https://secure7.www.apple.com.cn'+path,on=lambda *_:None)
    adapter=AppleAdapter(page,Control(False))
    adapter.last_document_status=status;adapter.last_document_method=method;adapter.submit_attempted=submit
    if blocked:adapter.http_status=541;adapter.limited_until=asyncio.get_running_loop().time()+60
    async def no_consent(*_):pass
    adapter.handle_consent=no_consent
    try:await adapter.guard(text='Temporarily unavailable')
    except RetryableRead:pytest.fail('Unsafe automatic recovery')
    except UserAction:pass


async def test_stalled_cdp_tab_creation_can_be_stopped_without_closing_browser(tmp_path):
    control=Control();entered=asyncio.Event();cancelled=asyncio.Event()
    async def new_page():
        entered.set()
        try:await asyncio.Future()
        finally:cancelled.set()
    session=BrowserSession(tmp_path,stop_check=control.check)
    session.context=SimpleNamespace(new_page=new_page)
    task=asyncio.create_task(session.new_page())
    await asyncio.wait_for(entered.wait(),1);control.cancel_task()
    with pytest.raises(Stopped):await asyncio.wait_for(task,1)
    assert cancelled.is_set() and not control.stop_event.is_set()
