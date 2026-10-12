from __future__ import annotations

import asyncio
from types import SimpleNamespace
import pytest

from app.adapters.apple import AppleAdapter
from app.automation.control import Control
from app.services.traffic import SiteCooldown


@pytest.mark.parametrize('url,kind,busy',[
    ('https://secure7.www.apple.com.cn/shop/signIn/account','document',True),
    ('https://idmsa.apple.com/appleauth/auth/signin/init','xhr',True),
    ('https://metrics.apple.com.cn/report','xhr',False),
])
async def test_503_from_auxiliary_resource_does_not_masquerade_as_store_maintenance(tmp_path,url,kind,busy):
    events=[]
    control=Control(False,site_cooldown=SiteCooldown(tmp_path))
    adapter=AppleAdapter(SimpleNamespace(on=lambda *_:None),control,progress=lambda k,d:events.append((k,d)))
    adapter._response(SimpleNamespace(url=url,status=503,request=SimpleNamespace(resource_type=kind,method='POST'),headers={'retry-after':'120'}))
    event=next(d for k,d in events if k=='http_status')
    assert event['affects']==('login_service' if busy else 'resource')
    assert event['wait_seconds']==(120 if busy else 0)
    assert bool(adapter.busy_until)==busy


@pytest.mark.parametrize('status',[403,541])
async def test_public_delivery_preview_failure_does_not_block_independent_pickup_read(status):
    events=[]
    adapter=AppleAdapter(SimpleNamespace(on=lambda *_:None),Control(False),
                         progress=lambda kind,data:events.append((kind,data)))
    response=SimpleNamespace(url='https://www.apple.com.cn/shop/fulfillment-messages?parts.0=PUBLIC_FIXTURE',
                             status=status,request=SimpleNamespace(resource_type='fetch',method='GET'),headers={})
    adapter._response(response)
    assert adapter.http_status==200
    assert events[-1][1]['blocks_checkout'] is False
    assert events[-1][1]['affects']=='delivery_preview'


@pytest.mark.parametrize('changes',[
    {'path':'/shop/xhr/fetch'}, {'path':'/shop/signIn/idms/fixture'},
    {'method':'POST'}, {'kind':'document'}, {'status':429},
    {'headers':{'retry-after':'120'}}, {'added':True}, {'cart':True},
])
async def test_delivery_scope_exception_never_relaxes_stock_login_rate_or_checkout_rejections(changes):
    adapter=AppleAdapter(SimpleNamespace(on=lambda *_:None),Control(False))
    adapter.add_attempted=changes.get('added',False);adapter.cart_checked=changes.get('cart',False)
    response=SimpleNamespace(url='https://www.apple.com.cn'+changes.get('path','/shop/fulfillment-messages'),
                             status=changes.get('status',541),request=SimpleNamespace(resource_type=changes.get('kind','fetch'),method=changes.get('method','GET')),
                             headers=changes.get('headers',{}))
    adapter._response(response)
    assert adapter.http_status==response.status
    assert adapter.limited_until>asyncio.get_running_loop().time()


@pytest.mark.parametrize('path,method,kind,can_retry',[
    ('/shop/buy-iphone/iphone-17','GET','document',True),
    ('/shop/buy-iphone','GET','document',True),
    ('/shop/signIn/idms/fixture','POST','fetch',False),
    ('/shop/checkout','POST','document',False),
    ('/shop/bag','GET','document',False),
    ('/shop/buy-iphone/iphone-17','POST','document',False),
    ('/shop/buy-iphone/iphone-17','GET','script',False),
])
async def test_only_public_entry_document_gets_can_retry_without_manual_resume(tmp_path,path,method,kind,can_retry):
    events=[];frame=object()
    adapter=AppleAdapter(SimpleNamespace(on=lambda *_:None,main_frame=frame),Control(False,site_cooldown=SiteCooldown(tmp_path)),
                         progress=lambda k,d:events.append((k,d)))
    response=SimpleNamespace(url='https://www.apple.com.cn'+path,status=541,
        request=SimpleNamespace(resource_type=kind,method=method,frame=frame),headers={})
    adapter._response(response)
    assert adapter.opening_retry is can_retry
    assert next(d for k,d in events if k=='http_status')['wait_seconds']==5
    assert SiteCooldown(tmp_path).remaining()<=5
