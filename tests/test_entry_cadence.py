"""Exercise the real opening loop without sending requests to Apple."""
import asyncio
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

import pytest

from app.automation.control import Control
from app.automation.engine import Engine
from app.models.config import Mode
from app.models.flow import UserAction, RateLimited
from app.services.simulation import local_config
from app.services.storage import Repository


@pytest.mark.parametrize('mode,missing_link', [(Mode.WEBSITE_DRY_RUN,False), (Mode.LIVE,False), (Mode.WEBSITE_DRY_RUN,True)])
async def test_entry_keeps_five_second_cadence_beyond_two_minutes(tmp_path, monkeypatch, mode, missing_link):
    import app.automation.engine as module
    clock = [1000.]
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    base = datetime.now(timezone.utc) + timedelta(days=1)
    class ClockDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = base + timedelta(seconds=clock[0]-1000.)
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)
    monkeypatch.setattr(module, 'datetime', ClockDatetime)
    control = Control(False)

    async def sleep(seconds):
        clock[0] += seconds
        await asyncio.sleep(0)

    control.sleep = sleep
    requests = []

    class Store:
        waiting_for_link=missing_link
        async def prepare(self, config):
            requests.append(clock[0])
            # Two seconds of normal loading must count towards the five seconds.
            clock[0] += 2

        async def is_open(self):
            return len(requests) >= 35

        async def reload(self):
            requests.append(clock[0])
            clock[0] += 2

        async def select_product(self, config):
            raise UserAction('offline: stop before product selection')

    config = local_config()
    config.product.url = 'https://www.apple.com.cn/shop/buy-iphone/iphone-17'
    config.interval = 10
    config.max_interval = 60
    config.max_wait = 1800
    if missing_link:
        config.product.url=''
        config.product.discover_from_store=True
    # No appointment is also a valid immediate launch. A saved appointment
    # must not restore the old two-minute cutoff in a website trial.
    if mode == Mode.LIVE:
        config.starts_at = (base + timedelta(seconds=5)).isoformat()
        config.profile_id = 'offline-reference'
        config.transmit_identity = True
    result = await Engine(Repository(tmp_path/'db.sqlite'), control).run(config, Store(), mode)
    assert result.state == 'PAUSED'
    gaps = [b-a for a, b in zip(requests, requests[1:])]
    assert all(4.999999 <= gap <= 5.25 for gap in gaps), gaps
    assert requests[-1] - requests[0] > 120


@pytest.mark.parametrize('retry_opening,server_wait,expected,calls', [
    (True,5,'PAUSED',2), (True,120,'FAILED',1), (False,5,'PAUSED',1),
])
async def test_only_public_entry_retries_and_never_before_server_wait(tmp_path, monkeypatch, retry_opening, server_wait, expected, calls):
    import app.automation.engine as module
    clock=[1000.]
    monkeypatch.setattr(module,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    control=Control(False)
    async def sleep(seconds):
        clock[0]+=seconds
        await asyncio.sleep(0)
    control.sleep=sleep
    attempts=[]
    class Store:
        async def prepare(self,config):
            attempts.append(clock[0])
            if len(attempts)==1:
                raise RateLimited('offline HTTP response',retry_after=server_wait,retry_opening=retry_opening)
        async def is_open(self):return True
        async def select_product(self,config):raise UserAction('offline: stop before checkout')
    config=local_config();config.product.url='https://www.apple.com.cn/shop/buy-iphone/iphone-17';config.max_wait=30
    result=await Engine(Repository(tmp_path/'db.sqlite'),control).run(config,Store(),Mode.WEBSITE_DRY_RUN)
    assert result.state==expected and len(attempts)==calls
    if calls==2:assert attempts[1]-attempts[0]>=server_wait


@pytest.mark.parametrize('strategy,pending', [('refresh',False),('wait',False),('refresh',True)])
async def test_wait_page_follows_preference_and_keeps_loading_request(tmp_path,monkeypatch,strategy,pending):
    import app.automation.engine as module
    clock=[1000.];monkeypatch.setattr(module,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    control=Control(False);requests=[]
    async def sleep(seconds):
        clock[0]+=seconds
        await asyncio.sleep(0)
    control.sleep=sleep
    class Entry:
        in_queue=True
        navigation_pending=pending
        async def prepare(self,config):requests.append(clock[0])
        async def is_open(self):
            return len(requests)>=3 or ((strategy=='wait' or pending) and clock[0]>=1012)
        async def reload(self):requests.append(clock[0])
        async def select_product(self,config):raise UserAction('offline: stop before selection')
    settings=local_config();settings.product.url='https://www.apple.com.cn/shop/buy-iphone/iphone-duo'
    settings.entry_strategy=strategy;settings.max_wait=100
    result=await Engine(Repository(tmp_path/'db.sqlite'),control).run(settings,Entry(),Mode.WEBSITE_DRY_RUN)
    assert result.state=='PAUSED'
    if strategy=='refresh' and not pending:
        assert len(requests)==3
        assert all(4.99<=b-a<=5.25 for a,b in zip(requests,requests[1:]))
        assert any('按所选策略刷新' in step['message'] for step in result.steps)
    else:assert len(requests)==1
