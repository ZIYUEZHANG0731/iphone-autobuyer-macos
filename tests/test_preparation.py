from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.automation.control import Control
from app.automation.engine import Engine
from app.models.config import ConfigError, Mode, Settings, Store
from app.models.flow import Receipt
from app.services.readiness import preparation_items
from app.services.storage import Repository
from app.utils.scheduling import convert_wall_time, future_start, schedule_preview, wall_time


def configured():
    config = Settings()
    config.product.max_total = '7000'
    config.stores = [Store('离线验证门店', store_id='fixture-store')]
    config.profile_id = 'fixture-keychain-reference'
    config.transmit_identity = True
    return config


def test_preparation_finds_all_missing_items_and_manual_dry_run_is_allowed():
    config = Settings()
    live = {i.key: i for i in preparation_items(config)}
    assert {key for key, item in live.items() if item.blocks} == {'product', 'stores', 'identity'}
    dry = {i.key: i for i in preparation_items(config, mode=Mode.WEBSITE_DRY_RUN)}
    assert dry['identity'].status == 'manual' and not dry['identity'].blocks
    assert dry['browser'].status == 'unknown' and dry['login'].status == 'unknown'


@pytest.mark.parametrize(('session_open', 'minutes', 'expected'), [(True, 2, 'ready'), (False, 2, 'unknown'), (True, 11, 'unknown')])
def test_preparation_cannot_claim_closed_or_stale_login(session_open, minutes, expected):
    now = datetime.now(timezone.utc)
    login = {'authenticated': True, 'checked_at': (now - timedelta(minutes=minutes)).isoformat()}
    items = {i.key: i for i in preparation_items(configured(), profile_ids=['fixture-keychain-reference'],
        browser_installed=True, session_open=session_open, login=login, now=now)}
    assert items['login'].status == expected
    assert items['identity'].status == 'ready'


def test_old_order_blocks_new_live_purchase_but_allows_dry_run():
    config = configured()
    order = {'status': 'UNKNOWN', 'order_id': 'W1234567890'}
    live = {i.key: i for i in preparation_items(config, submission=order)}
    dry = {i.key: i for i in preparation_items(config, submission=order, mode=Mode.WEBSITE_DRY_RUN)}
    assert live['order'].blocks and not dry['order'].blocks
    assert 'W1234567890' not in live['order'].detail


@pytest.mark.parametrize(('mode','authorized','expected'),[
    (Mode.LIVE,True,'ready'),(Mode.LIVE,False,'manual'),(Mode.WEBSITE_DRY_RUN,True,'manual'),
])
def test_preparation_distinguishes_automatic_live_submission_from_trial(mode,authorized,expected):
    config=configured();config.auto_submit=authorized
    handoff=next(i for i in preparation_items(config,mode=mode) if i.key=='handoff')
    assert handoff.status==expected
    assert '页面缺门店' not in handoff.detail


def test_legacy_timezone_preserves_exact_instant_and_beijing_only_display():
    berlin = datetime(2026, 10, 16, 14)
    china = convert_wall_time(berlin, 'Europe/Berlin', 'Asia/Shanghai')
    assert china == datetime(2026, 10, 16, 20)
    assert wall_time(china, 'Asia/Shanghai').timestamp() == wall_time(berlin, 'Europe/Berlin').timestamp()
    assert convert_wall_time(china, 'Asia/Shanghai', 'Europe/Berlin') == berlin
    assert '20:00:00' in schedule_preview(berlin, 'Europe/Berlin')
    assert '柏林' not in schedule_preview(berlin, 'Europe/Berlin')


@pytest.mark.parametrize('value', [datetime(2026, 3, 29, 2, 30), datetime(2026, 10, 25, 2, 30)])
def test_ambiguous_or_nonexistent_dst_time_requires_explicit_selection(value):
    with pytest.raises(ConfigError): wall_time(value, 'Europe/Berlin')


def test_past_schedule_blocks_live_preparation_without_accidental_navigation():
    config = configured()
    config.starts_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    items = {i.key: i for i in preparation_items(config, profile_ids=[config.profile_id])}
    assert items['schedule'].blocks
    assert not next(i for i in preparation_items(config, mode=Mode.WEBSITE_DRY_RUN) if i.key == 'schedule').blocks
    with pytest.raises(ConfigError): future_start(config.starts_at)


async def test_engine_blocks_expired_schedule_before_website_request_but_can_reconcile_old_order(tmp_path):
    config = configured()
    config.starts_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    calls = []
    async def prepare(_): calls.append('navigate')
    async def reconcile(_): return Receipt('W1234567890', 'payment_pending')
    adapter = SimpleNamespace(prepare=prepare, reconcile=reconcile)
    repo = Repository(tmp_path / 'db.sqlite')
    result = await Engine(repo, Control(False)).run(config, adapter, Mode.LIVE)
    assert result.state == 'FAILED' and not calls
    repo.claim_submission(config.intent_id, 'offline-old-submit')
    repo.record_submission(config.intent_id, 'UNKNOWN')
    reconciled = await Engine(repo, Control(False)).run(config, adapter, Mode.LIVE)
    assert reconciled.state == 'PAYMENT_PENDING' and not calls


def test_stock_history_records_changes_only_and_separates_product_configurations(tmp_path):
    repo = Repository(tmp_path / 'db.sqlite')
    product = configured().product
    observed = [{'name': '离线验证门店', 'store_id': 'fixture-store', 'available': False, 'quote': '暂无可选自提'}]
    assert len(repo.observe_stores(product, observed)) == 1
    assert not repo.observe_stores(product, observed)
    changed = deepcopy(observed); changed[0].update(available=True, quote='明天可取货')
    assert repo.observe_stores(product, changed)[0]['previous_available'] == 0
    other = deepcopy(product); other.options = {'dimensionColor': 'fixture-other-color'}
    assert repo.observe_stores(other, changed)[0]['previous_available'] is None
    assert len(repo.stock_changes()) == 3
