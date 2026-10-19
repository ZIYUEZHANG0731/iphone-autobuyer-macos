"""Event-to-checkout contracts. All purchase actions are offline fixtures."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from types import SimpleNamespace

import pytest

from app.automation.control import Control
from app.automation.engine import Engine
from app.models.config import Mode, Product, Settings, Store
from app.models.flow import Availability, FlowError, Receipt, Summary
from app.services.iaone import IAOneClient, IAOneError, IAOneTokenStore, RestockMonitor, matching_event
from app.services.storage import Repository


SKU = 'https://www.apple.com.cn/shop/buy-iphone/iphone-duo/mk2m4ch/a'
TOKEN = 'iaone_synthetic_only_not_a_real_token'
WATCH = 'fixture-watch'


def configured():
    return Settings(product=Product(name='iPhone Duo', url=SKU, max_total='100.00',
                    options={'dimensionColor': 'starwhite', 'dimensionCapacity': '256gb'}),
                    stores=[Store('Apple 南京东路', store_id='R359')], payments=['支付宝'],
                    profile_id='offline-profile', transmit_identity=True, auto_submit=True)


def event(**changes):
    return {'id': 'fixture-event', 'type': 'restocked', 'watchId': WATCH, 'region': 'CN',
            'occurredAt': datetime.now(timezone.utc).isoformat(), 'productCode': 'MK2M4CH/A',
            'scope': 'nearby', 'availableStores': ['Apple 南京东路'], 'purchaseURL': SKU, **changes}


@pytest.mark.parametrize('changes', [
    {'type': 'test'}, {'type': 'sold_out'}, {'watchId': 'other-watch'}, {'region': 'DE'},
    {'productCode': 'MK2N4CH/A'}, {'purchaseURL': SKU.replace('mk2m4', 'mk2n4')},
    {'purchaseURL': SKU.replace('www.apple.com.cn', 'www.apple.com.cn.evil.invalid')},
    {'purchaseURL': 'http://' + SKU.split('://')[1]}, {'availableStores': []},
    {'availableStores': ['Apple 三里屯']}, {'availableStores': [{}]},
    {'scope': 'online'}, {'availableStores': ['']}, {'occurredAt': 'invalid'},
    {'occurredAt': datetime.now(timezone.utc).replace(tzinfo=None).isoformat()},
    {'occurredAt': (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()},
])
def test_only_current_pickup_restock_can_trigger(changes):
    assert not matching_event(event(**changes), configured(), {WATCH})


@pytest.mark.parametrize('stores', [['R359'], ['南京东路'], ['Apple 南京东路'],
    [{'storeId': 'R359', 'storeName': '南京东路'}], [{'id': 'R359', 'name': 'Apple 南京东路'}]])
def test_store_ids_or_names_match_without_confusing_online_delivery(stores):
    assert matching_event(event(availableStores=stores), configured(), {WATCH})


def test_other_stores_only_when_explicitly_enabled():
    config = configured()
    config.allow_other_stores = True
    assert matching_event(event(availableStores=['Apple 三里屯']), config, {WATCH})
    assert not matching_event(event(availableStores=[]), config, {WATCH})
    config.stores[0].enabled = False
    config.allow_other_stores = False
    assert not matching_event(event(), config, {WATCH})


def test_small_server_clock_difference_does_not_drop_fresh_stock():
    assert matching_event(event(occurredAt=(datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat()),
                          configured(), {WATCH})


def test_token_is_encrypted_and_independent_of_purchase_config(tmp_path):
    store = IAOneTokenStore(tmp_path)
    assert not store.load()
    store.save(TOKEN)
    assert IAOneTokenStore(tmp_path).load() == TOKEN
    assert TOKEN.encode() not in store.path.read_bytes()
    assert TOKEN not in json.dumps(configured().to_dict())
    assert store.path.stat().st_mode & 0o777 == 0o600
    key = store.inputs.key_path.read_bytes()
    store.save(TOKEN + '_updated')
    assert store.inputs.key_path.read_bytes() == key
    with pytest.raises(FlowError):
        store.save('wrong')
    assert store.load() == TOKEN + '_updated'


def test_missing_shared_key_never_overwrites_saved_inputs(tmp_path):
    store = IAOneTokenStore(tmp_path)
    store.inputs.root.mkdir()
    store.inputs.path.write_bytes(b'preserve encrypted records')
    with pytest.raises(FlowError):
        store.save(TOKEN)
    assert not store.inputs.key_path.exists()
    assert store.inputs.path.read_bytes() == b'preserve encrypted records'


class Feed:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.calls = []
        self.polling = asyncio.Event()
        self.history = [event(id='historical')]

    async def watchlist(self):
        return {'watches': [{'watchId': WATCH, 'region': 'CN', 'productCode': 'MK2M4CH/A'}]}

    async def events(self, after='', *, wait=50):
        self.calls.append((after, wait))
        if wait == 0:
            return {'cursor': 'baseline', 'events': self.history}
        self.polling.set()
        value = await self.queue.get()
        if isinstance(value, Exception):
            raise value
        return value

    async def send(self, cursor, *events):
        await self.queue.put({'cursor': cursor, 'events': list(events)})


async def test_continuous_listener_ignores_test_and_hands_off_without_timer(tmp_path):
    feed = Feed()
    notices = []
    async with RestockMonitor(feed, tmp_path, configured(), progress=notices.append) as monitor:
        waiting = asyncio.create_task(monitor.wait())
        try:
            await feed.polling.wait()
            await feed.send('after-test', event(type='test'))
            await asyncio.sleep(.02)
            assert not waiting.done()
            assert feed.calls[-1] == ('after-test', 50)
            await feed.send('after-stock', event())
            received = await asyncio.wait_for(waiting, .5)
            assert received['id'] == 'fixture-event'
            state = json.loads(monitor.path.read_text())
            assert state['cursor'] == 'after-stock' and not state['pending']
            assert state['seen'] == ['fixture-event']
            assert notices[-1]['handoff_seconds'] < .5
            # The follow-up long poll starts without an AI scheduler/run gap.
            assert feed.calls[-1] == ('after-stock', 50)
        finally:
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)
    assert monitor.task.done()


async def test_event_burst_is_one_handoff_and_does_not_queue_checkout_refreshes(tmp_path):
    feed=Feed()
    async with RestockMonitor(feed,tmp_path,configured()) as monitor:
        await feed.send('burst',event(id='first'),event(id='second'))
        received=await asyncio.wait_for(monitor.wait(),.5)
        assert received['id']=='second'
        assert monitor.state['pending']==[]
        assert monitor.state['seen']==['first','second']
        waiting=asyncio.create_task(monitor.wait())
        try:
            await asyncio.sleep(.02)
            assert not waiting.done()
            await feed.send('next',event(id='next'))
            assert (await asyncio.wait_for(waiting,.5))['id']=='next'
        finally:
            waiting.cancel()
            await asyncio.gather(waiting,return_exceptions=True)


async def test_persisted_signal_survives_restart_and_duplicate_does_not_trigger_again(tmp_path):
    feed = Feed()
    async with RestockMonitor(feed, tmp_path, configured()) as monitor:
        monitor.ingest({'cursor': 'stock', 'events': [event(), event()]})
        assert len(monitor.state['pending']) == 1
    resumed_feed = Feed()
    async with RestockMonitor(resumed_feed, tmp_path, configured()) as monitor:
        received = await asyncio.wait_for(monitor.wait(), .5)
        assert received['id'] == 'fixture-event'
        monitor.ingest({'cursor': 'repeat', 'events': [event()]})
        assert not monitor.state['pending']
        assert ('', 0) not in resumed_feed.calls


async def test_errors_preserve_cursor_and_notify_only_after_three_failures(tmp_path, monkeypatch):
    import app.services.iaone as module
    original_sleep = asyncio.sleep
    async def short_sleep(_):
        await original_sleep(0)
    monkeypatch.setattr(module.asyncio, 'sleep', short_sleep)
    feed = Feed()
    notices = []
    async with RestockMonitor(feed, tmp_path, configured(), progress=notices.append) as monitor:
        for _ in range(5):
            await feed.queue.put(IAOneError('fixed connection error'))
        await feed.send('recovered', event(type='test'))
        for _ in range(100):
            if monitor.state['cursor'] == 'recovered':
                break
            await original_sleep(.001)
        assert [n['state'] for n in notices] == ['reconnecting', 'listening']
        assert [cursor for cursor, wait in feed.calls if wait == 50][:-1] == ['baseline'] * 6
        assert monitor.state['cursor'] == 'recovered'
        assert not monitor.state['pending']


async def test_missing_cursor_never_discards_previous_progress(tmp_path):
    feed = Feed()
    async with RestockMonitor(feed, tmp_path, configured()) as monitor:
        before = monitor.path.read_bytes()
        with pytest.raises(IAOneError):
            monitor.ingest({'events': [event()]})
        assert monitor.state['cursor'] == 'baseline'
        assert monitor.path.read_bytes() == before


async def test_client_uses_separate_context_and_never_follows_redirects():
    created, calls = [], []
    responses = []
    class Response:
        status = 200
        headers = {}
        async def body(self):
            return b'{"cursor":"123","events":[]}'
        async def dispose(self):
            responses.append('disposed')
    class Context:
        async def fetch(self, path, **kwargs):
            calls.append((path, kwargs))
            return Response()
        async def dispose(self):
            responses.append('context disposed')
    async def context(**kwargs):
        created.append(kwargs)
        return Context()
    playwright = SimpleNamespace(request=SimpleNamespace(new_context=context))
    async with IAOneClient(playwright, TOKEN) as client:
        await client.events('opaque + cursor')
    assert created[0]['base_url'] == 'https://iaone.app'
    assert created[0]['extra_http_headers']['Authorization'] == 'Bearer ' + TOKEN
    assert calls[0][0] == '/api/v1/integration/events?wait=50&after=opaque+%2B+cursor'
    assert calls[0][1]['max_redirects'] == 0 and calls[0][1]['max_retries'] == 0
    assert responses == ['disposed', 'context disposed']


class Checkout:
    supports_submit = True
    def __init__(self):
        self.calls = []
        self.selected = None
        self.available = True
        self.parked = asyncio.Event()
        self.receipt = None

    async def prepare(self, _):
        self.calls.append('navigate')

    async def is_open(self):
        return True

    async def select_product(self, _):
        self.calls.append('configure')
        self.parked.set()

    async def resume_product(self, _):
        self.calls.append('verify configured page')

    async def availability(self, config):
        self.calls.append('stock')
        return [Availability(config.stores[0], self.available, 'offline stock')]

    async def select_pickup(self, selected, _):
        self.calls.append('bag and pickup')
        self.selected = selected

    async def begin_checkout(self, config):
        self.calls.append('bag')
        self.selected = Availability(config.stores[0], False, 'checkout preference only')

    async def refresh_checkout_pickup(self, _):
        self.calls.append('checkout refresh')

    async def fill_customer(self, _, config):
        from app.models.flow import PickupUnavailable
        self.calls.append('customer')
        if not self.available:
            raise PickupUnavailable('offline checkout has no allowed store/time')
        self.selected = Availability(config.stores[0], True, 'confirmed by checkout')

    async def select_payment(self, _):
        self.calls.append('payment')

    async def review(self, config):
        return Summary(config.product.name, config.product.options, 1, Decimal('100.00'),
                       config.stores[0].name, config.stores[0].store_id, '支付宝', verified=True)

    async def submit(self, _):
        self.calls.append('submit')
        self.receipt = Receipt('W1234567890', 'payment_pending')
        return self.receipt

    async def reconcile(self, _):
        return self.receipt


async def test_engine_parks_then_processes_restock_once_and_keeps_submission_lock(tmp_path):
    feed, adapter, config = Feed(), Checkout(), configured()
    repo = Repository(tmp_path / 'orders.sqlite3')
    async with RestockMonitor(feed, tmp_path, config) as monitor:
        task = asyncio.create_task(Engine(repo, Control(False)).run(config, adapter, Mode.LIVE, restock=monitor))
        try:
            await adapter.parked.wait()
            await asyncio.sleep(.02)
            assert adapter.calls == ['navigate', 'configure']
            await feed.send('test', event(type='test'))
            await asyncio.sleep(.02)
            assert adapter.calls == ['navigate', 'configure']
            await feed.send('real', event())
            result = await asyncio.wait_for(task, 1)
            assert result.state == 'PAYMENT_PENDING'
            assert adapter.calls.count('navigate') == 1
            assert adapter.calls.count('submit') == 1
            assert adapter.calls[2:5] == ['verify configured page', 'bag', 'customer']
            assert 'stock' not in adapter.calls and 'bag and pickup' not in adapter.calls
            assert repo.submission(config.intent_id)['status'] == 'CONFIRMED'
            recovered = await Engine(repo, Control(False)).run(config, adapter, Mode.LIVE, restock=monitor)
            assert recovered.state == 'PAYMENT_PENDING'
            assert adapter.calls.count('submit') == 1
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_restock_that_sells_out_waits_for_next_event_without_refreshing(tmp_path):
    feed, adapter, config = Feed(), Checkout(), configured()
    adapter.available = False
    async with RestockMonitor(feed, tmp_path, config) as monitor:
        control = Control(False)
        task = asyncio.create_task(Engine(Repository(tmp_path / 'db.sqlite3'), control).run(
            config, adapter, Mode.LIVE, restock=monitor))
        try:
            await adapter.parked.wait()
            await feed.send('first', event(id='first'))
            for _ in range(100):
                if 'customer' in adapter.calls:
                    break
                await asyncio.sleep(.002)
            await asyncio.sleep(.02)
            assert adapter.calls.count('customer') == 1
            assert adapter.calls.count('bag') == 1 and 'checkout refresh' not in adapter.calls
            assert 'bag and pickup' not in adapter.calls
            adapter.available = True
            await feed.send('second', event(id='second'))
            result = await asyncio.wait_for(task, 1)
            assert result.state == 'PAYMENT_PENDING'
            assert adapter.calls.count('customer') == 2
            assert adapter.calls.count('checkout refresh') == 1
            assert adapter.calls.count('bag') == 1 and 'stock' not in adapter.calls
            assert adapter.calls.count('verify configured page') == 1
            assert adapter.calls.count('navigate') == 1
            assert adapter.calls.count('submit') == 1
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_stop_cancels_wait_without_touching_bag(tmp_path):
    feed, adapter, config = Feed(), Checkout(), configured()
    control = Control(False)
    async with RestockMonitor(feed, tmp_path, config) as monitor:
        task = asyncio.create_task(Engine(Repository(tmp_path / 'db.sqlite3'), control).run(
            config, adapter, Mode.LIVE, restock=monitor))
        await adapter.parked.wait()
        control.cancel_task()
        result = await asyncio.wait_for(task, .5)
        assert result.state == 'STOPPED'
        assert adapter.calls == ['navigate', 'configure']


async def test_stop_while_waiting_at_sold_out_checkout_does_not_reload_or_readd(tmp_path):
    feed, adapter, config = Feed(), Checkout(), configured()
    adapter.available = False
    control = Control(False)
    async with RestockMonitor(feed, tmp_path, config) as monitor:
        task = asyncio.create_task(Engine(Repository(tmp_path / 'db.sqlite3'), control).run(
            config, adapter, Mode.LIVE, restock=monitor))
        await adapter.parked.wait()
        await feed.send('first', event(id='first'))
        for _ in range(100):
            if 'customer' in adapter.calls:
                break
            await asyncio.sleep(.002)
        control.cancel_task()
        result = await asyncio.wait_for(task, .5)
        assert result.state == 'STOPPED'
        assert adapter.calls.count('bag') == 1
        assert not {'stock', 'checkout refresh', 'submit'} & set(adapter.calls)


async def test_site_rejection_is_not_retried_as_a_stock_event(tmp_path):
    from app.models.flow import RateLimited
    feed, adapter, config = Feed(), Checkout(), configured()
    async def rejected(*_):
        adapter.calls.append('site rejected')
        raise RateLimited('fixture HTTP 541', retry_after=60)
    adapter.fill_customer = rejected
    async with RestockMonitor(feed, tmp_path, config) as monitor:
        task = asyncio.create_task(Engine(Repository(tmp_path / 'db.sqlite3'), Control(False)).run(
            config, adapter, Mode.LIVE, restock=monitor))
        await adapter.parked.wait()
        await feed.send('first', event(id='first'))
        result = await asyncio.wait_for(task, 1)
        assert result.state == 'PAUSED'
        assert adapter.calls.count('site rejected') == 1
        assert not {'stock', 'checkout refresh', 'submit'} & set(adapter.calls)
