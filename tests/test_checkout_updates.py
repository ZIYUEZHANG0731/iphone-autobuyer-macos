from types import SimpleNamespace
import asyncio

import pytest

from app.adapters.checkout_updates import CheckoutUpdates
from app.automation.control import Control
from app.models.flow import UserAction, PendingSave
from app.automation.engine import Engine
from app.models.config import State
from app.services.storage import Repository


class UpdatePage:
    def on(self, *_):
        pass

    def is_closed(self):
        return False


class UpdateRequest:
    def __init__(self, url):
        self.url = url
        self.method = 'POST'


@pytest.mark.parametrize('path', ['/shop/session', '/shop/buy-iphone/iphone-17', '/shop/metrics'])
async def test_aborted_product_post_does_not_poison_later_bag_save(path):
    updates = CheckoutUpdates(UpdatePage())
    request = UpdateRequest('https://www.apple.com.cn' + path)
    updates.started(request)
    updates.response(SimpleNamespace(request=request, status=200))
    updates.failure(request)
    await updates.ready(Control(False), label='购物袋')
    assert not updates.failed and not updates.pending


@pytest.mark.parametrize('path', ['/shop/bag', '/shop/bagx/quantity', '/shop/checkout/fulfillment', '/shop/checkout/billing'])
@pytest.mark.parametrize('failure', ['http', 'connection'])
async def test_failed_purchase_write_still_blocks_continuation(path, failure):
    updates = CheckoutUpdates(UpdatePage())
    request = UpdateRequest('https://secure9.www.apple.com.cn' + path)
    updates.started(request)
    updates.response(SimpleNamespace(request=request, status=500 if failure == 'http' else 200))
    if failure == 'http':
        updates.finished(request)
    else:
        updates.failure(request)
    with pytest.raises(UserAction, match='未完成购物袋的保存'):
        await updates.ready(Control(False), label='购物袋')


async def test_successful_purchase_write_is_not_replaced_by_an_unrelated_failure():
    updates = CheckoutUpdates(UpdatePage())
    request = UpdateRequest('https://www.apple.com.cn/shop/bag')
    unrelated = UpdateRequest('https://www.apple.com.cn/shop/session')
    updates.started(request)
    updates.started(unrelated)
    updates.failure(unrelated)
    updates.response(SimpleNamespace(request=request, status=200))
    updates.finished(request)
    await updates.ready(Control(False))
    assert not updates.failed and not updates.pending


async def test_slow_save_resumes_the_existing_operation_without_another_write(tmp_path):
    updates = CheckoutUpdates(UpdatePage())
    control = Control()
    engine = Engine(Repository(tmp_path/'db.sqlite'), control)
    engine.emit(State.PREPARING, 'Test saved choice')
    request = UpdateRequest('https://secure9.www.apple.com.cn/shop/checkoutx/fulfillment')
    writes = 0
    entered = False
    async def selection():
        nonlocal writes, entered
        if not entered:
            entered = True
            writes += 1
            updates.started(request)
            def finish():
                updates.response(SimpleNamespace(request=request, status=200))
                updates.finished(request)
            asyncio.get_running_loop().call_later(.1, finish)
        await updates.ready(control, timeout=.02)
        return 'saved'
    assert await asyncio.wait_for(engine.operation(selection), 2) == 'saved'
    assert writes == 1
    assert [step['state'] for step in engine.steps] == ['PREPARING','PAUSED','PREPARING']
    assert '保存' in engine.message


@pytest.mark.parametrize('outcome', ['http_failure','connection_failure','changed_page'])
async def test_slow_save_probe_never_accepts_a_failed_or_replaced_page(outcome):
    updates = CheckoutUpdates(UpdatePage())
    request = UpdateRequest('https://secure9.www.apple.com.cn/shop/checkoutx/fulfillment')
    updates.started(request)
    with pytest.raises(PendingSave) as paused:
        await updates.ready(Control(), timeout=.01)
    if outcome == 'http_failure':
        updates.response(SimpleNamespace(request=request, status=500))
        updates.finished(request)
    elif outcome == 'connection_failure':
        updates.failure(request)
    else:
        updates.attach(UpdatePage())
    assert not await paused.value.ready()
