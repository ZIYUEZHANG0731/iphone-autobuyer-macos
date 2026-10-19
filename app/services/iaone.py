"""IA ONE's documented long poll, isolated from the Apple browser's cookies.

The open request returns as soon as an event arrives. Its 50 second timeout
is a keep-alive limit, not an interval between stock checks.
"""
from __future__ import annotations

import asyncio
from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import urlencode, urlsplit

from cryptography.fernet import Fernet, InvalidToken
from playwright.async_api import Error as PlaywrightError

from app import __version__
from app.models.flow import FlowError
from app.security.local_inputs import LocalInputs
from app.services.sku import family_url
from app.utils.files import write_private_json


class IAOneError(FlowError):
    def __init__(self, message, *, retry_after=3, permanent=False):
        super().__init__(message)
        self.retry_after = retry_after
        self.permanent = permanent


class IAOneTokenStore:
    """Uses the app's existing private encryption key; never asks Keychain."""
    def __init__(self, root):
        self.inputs = LocalInputs(root)
        self.path = self.inputs.root / 'iaone.enc'

    @staticmethod
    def validate(token):
        if not isinstance(token, str) or not re.fullmatch(r'iaone_[A-Za-z0-9_-]{16,200}', token):
            raise FlowError('IA ONE 令牌格式不正确，请从 IA ONE 的集成页面复制')
        return token

    def load(self):
        if not self.path.exists():
            return ''
        try:
            if self.inputs.root.is_symlink():
                raise ValueError
            key = self.inputs._read(self.inputs.key_path, 128)
            return self.validate(Fernet(key).decrypt(self.inputs._read(self.path, 1024)).decode())
        except (OSError, ValueError, InvalidToken, UnicodeError):
            raise FlowError('本机 IA ONE 连接未能读取，请重新保存令牌；原文件已保留') from None

    def save(self, token):
        token = self.validate(token.strip())
        try:
            self.inputs.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            if self.inputs.root.is_symlink():
                raise ValueError
            self.inputs.root.chmod(0o700)
            if not self.inputs.key_path.exists():
                if self.inputs.path.exists():
                    raise ValueError
                self.inputs._atomic_write(self.inputs.key_path, Fernet.generate_key())
            key = self.inputs._read(self.inputs.key_path, 128)
            self.inputs._atomic_write(self.path, Fernet(key).encrypt(token.encode()))
        except (OSError, ValueError):
            raise FlowError('IA ONE 连接保存失败，请检查软件数据目录') from None


class IAOneClient:
    def __init__(self, playwright, token):
        self.playwright = playwright
        self._token = IAOneTokenStore.validate(token)
        self.context = None
        self.requests = deque()

    async def __aenter__(self):
        # A separate API context: no Apple cookies or account details leave the
        # browser, and the integration token never becomes a browser header.
        self.context = await self.playwright.request.new_context(
            base_url='https://iaone.app', user_agent=f'AppleAutoBuyer/{__version__}',
            extra_http_headers={'Authorization': 'Bearer ' + self._token, 'Accept': 'application/json'},
            timeout=65000)
        return self

    async def __aexit__(self, *_):
        if self.context:
            await self.context.dispose()

    async def request(self, path, *, body=None):
        now = time.monotonic()
        while self.requests and self.requests[0] <= now - 3600:
            self.requests.popleft()
        if len(self.requests) >= 1150:
            raise IAOneError('IA ONE 连接频率已接近每小时限额，等待后自动重连',
                             retry_after=max(1, self.requests[0] + 3600 - now))
        self.requests.append(now)
        response = None
        try:
            # Do not forward Authorization through a redirect, including to a
            # different host. The API only uses these fixed documented paths.
            response = await self.context.fetch(path, method='POST' if body is not None else 'GET',
                                                data=body, max_redirects=0, max_retries=0)
            status = response.status
            if not 200 <= status < 300:
                delay = response.headers.get('retry-after', '')
                wait = int(delay) if delay.isdigit() else 3
                raise IAOneError(f'IA ONE 连接返回 HTTP {status}', retry_after=max(3, wait),
                                 permanent=status in (401, 403))
            raw = await response.body()
            if len(raw) > 2 * 1024 * 1024:
                raise IAOneError('IA ONE 返回内容超出读取范围')
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError
            return result
        except (PlaywrightError, OSError):
            # Request errors can contain headers. Only fixed messages cross
            # the service boundary or enter a log/UI.
            raise IAOneError('IA ONE 连接暂时中断，保留事件进度后自动重连') from None
        except (ValueError, UnicodeError):
            raise IAOneError('IA ONE 返回内容无法读取，保留原事件进度') from None
        finally:
            if response:
                await response.dispose()

    async def watchlist(self):
        return await self.request('/api/v1/integration/watchlist')

    async def events(self, after='', *, wait=50):
        query = {'wait': min(50, max(0, wait))}
        if after:
            query['after'] = after
        return await self.request('/api/v1/integration/events?' + urlencode(query))

    async def test_event(self, watch_id):
        return await self.request('/api/v1/integration/test-event', body={'watchId': watch_id})


def sku_from_url(url):
    if not family_url(url):
        return ''
    match = re.search(r'/([a-z0-9]{5,16})/a/?$', urlsplit(url).path, re.I)
    return match[1].upper() + '/A' if match else ''


def product_code(value):
    return value.strip().upper() if isinstance(value, str) else ''


def mainland(value):
    return isinstance(value, str) and value.strip().casefold() in ('cn', 'chn', '中国大陆', 'china mainland')


def store_keys(value):
    if isinstance(value, str):
        key = re.sub(r'^apple\s*|\s+', '', value.strip(), flags=re.I).casefold()
        return {key} if key else set()
    if isinstance(value, dict):
        return {key for name in ('id', 'storeId', 'storeNumber', 'name', 'storeName')
                for key in store_keys(value.get(name, '')) if key}
    return set()


def matching_event(event, config, watch_ids, *, now=None):
    """A signal authorizes a check, never a different SKU or delivery mode."""
    if not isinstance(event, dict) or event.get('type') != 'restocked':
        return False
    if not isinstance(event.get('watchId'), str) or event['watchId'] not in watch_ids or not mainland(event.get('region')):
        return False
    code = sku_from_url(config.product.url)
    if not code or product_code(event.get('productCode')) != code or sku_from_url(event.get('purchaseURL', '')) != code:
        return False
    if family_url(config.product.url) != family_url(event.get('purchaseURL', '')):
        return False
    # Old history is not a new buying signal. An interrupted connection can
    # replay recent events; the real store is still checked before adding.
    try:
        if not isinstance(event.get('occurredAt'), str):
            return False
        occurred = datetime.fromisoformat(event['occurredAt'].replace('Z', '+00:00'))
        age = ((now or datetime.now(timezone.utc)) - occurred).total_seconds()
        if not -30 <= age <= 300:
            return False
    except (KeyError, TypeError, ValueError):
        return False
    stores = event.get('availableStores')
    if event.get('scope') == 'online' or not isinstance(stores, list) or not stores:
        return False
    available = set().union(*(store_keys(s) for s in stores))
    return bool(available) and (config.allow_other_stores or any(
        s.enabled and available & (store_keys(s.store_id) | store_keys(s.name)) for s in config.stores))


class RestockMonitor:
    def __init__(self, client, root, config, *, progress=None):
        self.client = client
        self.config = deepcopy(config)
        self.path = Path(root) / 'iaone-state.json'
        self.progress = progress or (lambda _: None)
        self.state = {'cursor': '', 'seen': [], 'pending': []}
        self.changed = asyncio.Event()
        self.task = None
        self.failure = None
        self.watch_ids = set()
        self.received_at = {}

    async def __aenter__(self):
        if not sku_from_url(self.config.product.url):
            raise FlowError('IA ONE 到货下单需要选好机型、颜色和容量的 SKU 页面，请先保存商品配置')
        data = await self.client.watchlist()
        watches = data.get('watches')
        if not isinstance(watches, list):
            raise FlowError('IA ONE 关注列表无法读取')
        self.watch_ids = {w['watchId'] for w in watches if isinstance(w, dict) and isinstance(w.get('watchId'), str)
                          and mainland(w.get('region'))
                          and product_code(w.get('productCode')) == sku_from_url(self.config.product.url)}
        if not self.watch_ids:
            raise FlowError('IA ONE 关注列表没有当前配置，请先在 IA ONE 关注相同型号、颜色和容量')
        if self.path.exists():
            try:
                if self.path.is_symlink() or self.path.stat().st_size > 2 * 1024 * 1024:
                    raise ValueError
                raw = json.loads(self.path.read_text(encoding='utf-8'))
                if (not isinstance(raw, dict) or not isinstance(raw.get('cursor'), str)
                        or not isinstance(raw.get('seen'), list) or not isinstance(raw.get('pending'), list)
                        or any(not isinstance(i, str) for i in raw['seen'])
                        or any(not isinstance(e, dict) or not isinstance(e.get('id'), str) for e in raw['pending'])):
                    raise ValueError
                self.state = raw
            except (OSError, ValueError):
                raise FlowError('IA ONE 事件进度未能读取，原文件已保留，请检查数据目录') from None
        if not self.state['cursor']:
            # wait=0 without a cursor includes history. Establish the baseline
            # once and deliberately do not buy from that historical batch.
            baseline = await self.client.events(wait=0)
            self.state['cursor'] = self.cursor(baseline)
            self.save()
        self.task = asyncio.create_task(self.listen())
        return self

    async def __aexit__(self, *_):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    @staticmethod
    def cursor(payload):
        value = payload.get('cursor')
        if not isinstance(value, str) or not value:
            raise IAOneError('IA ONE 未返回事件进度，保留上一次进度')
        return value

    def save(self):
        write_private_json(self.path, self.state)

    def ingest(self, payload):
        cursor = self.cursor(payload)
        events = payload.get('events')
        if not isinstance(events, list):
            raise IAOneError('IA ONE 事件列表无法读取，保留上一次进度')
        next_state = deepcopy(self.state)
        known = set(next_state['seen']) | {event['id'] for event in next_state['pending']}
        for event in events:
            if not matching_event(event, self.config, self.watch_ids):
                continue
            # Keep only the public fields used for this purchase, not arbitrary
            # server payloads. Persist before advancing the cursor/waking up.
            item = {key: event[key] for key in ('watchId', 'type', 'occurredAt', 'region', 'productCode',
                                               'purchaseURL', 'availableStores')}
            event_id = event.get('id')
            item['id'] = event_id if isinstance(event_id, str) and event_id else hashlib.sha256(
                json.dumps(item, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            if item['id'] in known:
                continue
            known.add(item['id'])
            next_state['pending'].append(item)
            self.received_at[item['id']] = time.monotonic()
        next_state['pending'] = next_state['pending'][-100:]
        next_state['cursor'] = cursor
        write_private_json(self.path, next_state)
        self.state = next_state
        self.changed.set()

    async def listen(self):
        failures = 0
        try:
            while True:
                started = time.monotonic()
                try:
                    payload = await self.client.events(self.state['cursor'])
                    self.ingest(payload)
                except IAOneError as exc:
                    if exc.permanent:
                        raise
                    failures += 1
                    if failures == 3:
                        self.progress({'state': 'reconnecting', 'message': str(exc) + '；重连中，商品页面保留'})
                    await asyncio.sleep(max(exc.retry_after, min(30, 2 ** min(failures, 5))))
                    continue
                if failures >= 3:
                    self.progress({'state': 'listening', 'message': 'IA ONE 连接已恢复，继续等待到货'})
                failures = 0
                # Normal empty responses take 50s. A broken endpoint returning
                # immediately must not create a tight request loop.
                elapsed = time.monotonic() - started
                if elapsed < 1 and not payload.get('events'):
                    await asyncio.sleep(3 - elapsed)
        except asyncio.CancelledError:
            raise
        except (FlowError, OSError):
            self.failure = FlowError('IA ONE 监听已停止，请检查令牌或本机事件进度；商品页面保留')
            self.changed.set()

    async def wait(self):
        while True:
            self.changed.clear()
            if self.state['pending']:
                batch = self.state['pending']
                valid = [event for event in batch if matching_event(event, self.config, self.watch_ids)]
                next_state = deepcopy(self.state)
                next_state['pending'] = []
                next_state['seen'] = [*next_state['seen'], *(e['id'] for e in batch)][-1000:]
                write_private_json(self.path, next_state)
                self.state = next_state
                # Coalesce a burst into one checkout attempt, rather than
                # refreshing once for every event already in the same batch.
                event = max(valid, key=lambda e: datetime.fromisoformat(e['occurredAt'].replace('Z', '+00:00'))) if valid else None
                received = self.received_at.get(event['id']) if event else None
                for item in batch:
                    self.received_at.pop(item['id'], None)
                if not event:
                    continue
                delay = max(0, time.monotonic() - received) if received is not None else 0
                self.progress({'state': 'triggered', 'message': f'已收到当前配置和门店到货信号，开始官网核验（本机交接 {delay:.3f} 秒）',
                               'handoff_seconds': delay})
                return event
            if self.failure:
                raise self.failure
            await self.changed.wait()
