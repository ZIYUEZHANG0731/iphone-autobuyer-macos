from __future__ import annotations

import asyncio
from types import SimpleNamespace
import pytest

from app.adapters.apple import AppleAdapter
from app.automation.control import Control


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
