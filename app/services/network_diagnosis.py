"""Passive, bounded metadata from the browser's own normal Apple requests.

No additional requests, HAR, request bodies, credentials, cookie values, raw
response bodies, URLs with query strings, or authentication headers are saved.
The observer remains attached while a person uses the app's login page.
"""
from __future__ import annotations
from app.platforms.files import protect_path

import asyncio
from collections import deque
from datetime import datetime, timezone
import json
import hashlib
import ipaddress
from pathlib import Path
import re
from urllib.parse import urlsplit

from app.models.config import apple_url
from app.services.login import apple_auth_url
from app.services.network_state import read_local_network
from app.utils.files import write_private_json


FAMILY_LABELS = {
    'shop_signin': '商店登录页面', 'shop_idms': '商店登录回调',
    'auth_init': 'Apple 认证初始化', 'auth_complete': 'Apple 认证提交',
    'auth_signin': 'Apple 登录认证', 'auth_verify': 'Apple 认证验证',
    'auth_other': 'Apple 认证资源', 'account': '商店账户页面',
    'inventory': '自提库存查询', 'delivery': '配送预估',
    'product': '商品页面', 'checkout': '结账', 'bag': '购物袋',
    'shop_other': '商店其他请求', 'resource': '页面资源', 'icloud': 'iCloud 登录对照',
    'browser_context': '页面环境初始化',
}


def browser_capabilities(value: dict) -> dict:
    result = {}
    agent = value.get('user_agent', '')
    if isinstance(agent, str):
        result['engine_versions'] = dict(re.findall(r'\b(Chrome|QtWebEngine|AppleWebKit)/([0-9.]{1,24})\b', agent))
    result['platform'] = value.get('platform') if value.get('platform') in ('MacIntel', 'Win32', 'Linux x86_64', 'Linux aarch64') else 'other'
    languages = value.get('languages', [])
    result['languages'] = [v for v in languages[:8] if isinstance(v, str) and re.fullmatch(r'[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*', v)] if isinstance(languages, list) else []
    for key in ('webdriver', 'cookies_enabled', 'secure_context', 'webcrypto'):
        result[key] = value.get(key) if type(value.get(key)) is bool else None
    return result


def request_family(url: str) -> tuple[str, str] | None:
    """Return a public host and a fixed family, never a dynamic route or query."""
    try:
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.port not in (None,443) or parsed.username or parsed.password:
            return None
    except (ValueError, TypeError):
        return None
    host, path = parsed.hostname or '', parsed.path
    if host in ('www.icloud.com','icloud.com','www.icloud.com.cn','icloud.com.cn','setup.icloud.com','setup.icloud.com.cn'):
        return host, 'icloud'
    # Observation covers Apple's own auxiliary origins as well as the strict
    # credential allowlist. This does not widen permission to fill a password.
    if not (apple_url(url) or apple_auth_url(url) or host == 'apple.com' or host.endswith('.apple.com')):
        return None
    if host not in ('apple.com.cn', 'www.apple.com.cn', 'idmsa.apple.com', 'appleid.apple.com',
                    'account.apple.com', 'idmsa.apple.com.cn', 'appleid.apple.com.cn', 'account.apple.com.cn') and not re.fullmatch(r'secure\d{0,2}\.www\.apple\.com\.cn', host):
        host = 'other.apple.com.cn' if host.endswith('.apple.com.cn') else 'other.apple.com'
    if apple_auth_url(url) or path.startswith('/appleauth/auth/'):
        if path.endswith('/signin/init'):
            family = 'auth_init'
        elif path.endswith('/signin/complete'):
            family = 'auth_complete'
        elif path.rstrip('/').endswith('/signin'):
            family = 'auth_signin'
        elif '/verify/' in path or '/auth/verify' in path:
            family = 'auth_verify'
        else:
            family = 'auth_other'
        return host, family
    if path == '/jsc_context':
        family = 'browser_context'
    elif path.startswith('/shop/signIn/idms/'):
        family = 'shop_idms'
    elif path == '/shop/signIn' or path.startswith('/shop/signIn/'):
        family = 'shop_signin'
    elif path.startswith(('/shop/account/', '/shop/browse/account/')):
        family = 'account'
    elif path == '/shop/fulfillment-messages':
        family = 'delivery'
    elif path.startswith('/shop/fulfillment-messages') or '/pickup-message' in path:
        family = 'inventory'
    elif path.startswith('/shop/buy-iphone'):
        family = 'product'
    elif path.startswith('/shop/checkout'):
        family = 'checkout'
    elif path.startswith('/shop/bag'):
        family = 'bag'
    else:
        family = 'shop_other' if path.startswith('/shop/') else 'resource'
    return host, family


def response_shape(text: str, content_type: str) -> dict:
    """Classify a failed response in memory, without retaining private text."""
    if len(text) > 1024 * 1024:
        return {'body_kind': 'too_large'}
    result = {'body_kind': 'other'}
    if content_type == 'application/json' or text.lstrip().startswith(('{', '[')):
        try:
            value = json.loads(text)
        except (ValueError, RecursionError):
            return {'body_kind': 'invalid_json'}
        result['body_kind'] = 'json'
        if isinstance(value, dict):
            errors = value.get('serviceErrors')
            if isinstance(errors, list):
                codes = []
                for item in errors[:20]:
                    if not isinstance(item, dict):
                        continue
                    code = str(item.get('code', ''))
                    if re.fullmatch(r'-?\d{1,8}', code):
                        codes.append(code)
                    message = item.get('message', '')
                    if isinstance(message, str):
                        if re.search(r'unable to verify|cannot verify|无法验证', message, re.I):
                            result['error_class'] = 'identity_unverified'
                        elif re.search(r'password.*incorrect|incorrect.*password|密码.*(不正确|错误)', message, re.I):
                            result['error_class'] = 'credentials_rejected'
                if codes:
                    result['service_error_codes'] = codes
            auth = value.get('authType')
            if auth in ('hsa2', 'hsa', 'sa'):
                result['auth_state'] = auth
        return result
    if content_type == 'text/html' or re.search(r'<!doctype html|<html\b', text[:1000], re.I):
        result['body_kind'] = 'html'
        if re.search(r'<title[^>]*>\s*Page Not Found\s*[-–]\s*Apple\s*</title>', text, re.I):
            result['error_class'] = 'apple_not_found_html'
        elif re.search(r'<title[^>]*>\s*Access Denied\s*</title>|<h1[^>]*>\s*Access Denied', text, re.I):
            result['error_class'] = 'access_denied_html'
        elif re.search(r'<title[^>]*>[^<]*(captcha|security verification|安全验证)', text, re.I):
            result['error_class'] = 'challenge_html'
    return result


class NetworkDiagnosis:
    def __init__(self, root: Path, kind: str, *, progress=None, network_reader=None):
        self.kind = kind if kind in ('embedded','daily_chrome') else 'chrome'
        self.path = Path(root) / f'login-diagnostic-{self.kind}.json'
        self.previous_path = Path(root) / f'login-diagnostic-{self.kind}-previous.json'
        self.progress = progress or (lambda *_: None)
        self.context = None
        self.started = datetime.now(timezone.utc).isoformat()
        self.runtime = {}
        self.page_samples = []
        self.health = {'request_events': 0, 'response_events': 0, 'capture_errors': 0}
        self.events = deque(maxlen=160)
        self.tasks = set()
        self.writer = None
        self.sequence = 0
        self.sampler = None
        self.pages = set()
        self.network_reader = network_reader or read_local_network
        self.network_samples = deque(maxlen=32)
        self.network_revision = 0
        self.network_lock = asyncio.Lock()
        self.network_checked_at = float('-inf')
        self.network_pending = False
        self.closed = False

    async def start(self, context, page):
        # Retain the immediately preceding session so a later failed restart
        # does not erase a successful VPN/login comparison. Only two files per
        # browser kind are kept; no public upload or telemetry is involved.
        try:
            if self.path.is_file() and not self.path.is_symlink():
                self.path.replace(self.previous_path)
                protect_path(self.previous_path)
        except OSError:
            pass
        self.context = context
        context.on('request', self.observe_request)
        context.on('response', self.observe_response)
        context.on('requestfailed', self.observe_failure)
        context.on('page', self.observe_page)
        for opened in context.pages:
            self.observe_page(opened)
        # This reads browser capabilities only; no page fields or storage.
        try:
            self.runtime = browser_capabilities(await asyncio.wait_for(page.evaluate('''() => ({
                user_agent:navigator.userAgent, languages:Array.from(navigator.languages),
                platform:navigator.platform, webdriver:navigator.webdriver,
                cookies_enabled:navigator.cookieEnabled,
                secure_context:isSecureContext, webcrypto:!!globalThis.crypto?.subtle
            })'''), 2))
            self.runtime['sampled_at'] = 'browser_start'
        except Exception:
            self.runtime = {'unavailable': True}
        self.save()
        self.schedule_network('browser_start')
        self.sampler = asyncio.create_task(self.sample_loop())

    def track_task(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def schedule_network(self, reason, event=None):
        if self.closed or len(self.tasks) >= 64:
            return
        if event is None:
            if self.network_pending:
                return
            if reason != 'browser_start' and asyncio.get_running_loop().time()-self.network_checked_at < 5:
                return
            self.network_pending = True
        self.track_task(self.sample_network(reason, event))

    async def sample_network(self, reason, event=None):
        try:
            async with self.network_lock:
                now = asyncio.get_running_loop().time()
                if now-self.network_checked_at >= 1 or not self.network_samples:
                    try:
                        state = await asyncio.wait_for(self.network_reader(), 2)
                    except (OSError, asyncio.TimeoutError):
                        state = {'available': False}
                    observed_at = datetime.now(timezone.utc).isoformat()
                    self.network_checked_at = asyncio.get_running_loop().time()
                    changed = not self.network_samples or self.network_samples[-1]['state'] != state
                    if changed:
                        self.network_revision += 1
                        self.network_samples.append({'revision': self.network_revision,
                            'first_seen': observed_at, 'last_seen': observed_at, 'reason': reason,
                            'state': state})
                        self.progress('network_state', {'changed': self.network_revision > 1, **state})
                    else:
                        self.network_samples[-1]['last_seen'] = observed_at
                if event is not None and self.network_samples:
                    event['network_revision'] = self.network_samples[-1]['revision']
                    event['network_observed_at'] = self.network_samples[-1]['last_seen']
                self.save()
        except asyncio.CancelledError:
            raise
        except Exception:
            self.health['network_sample_errors'] = self.health.get('network_sample_errors', 0)+1
        finally:
            if event is None:
                self.network_pending = False

    def observe_page(self, page):
        if page in self.pages:
            return
        self.pages.add(page)
        page.on('console', self.observe_console)

    def observe_console(self, message):
        family = request_family(message.location.get('url', ''))
        if not family or message.type != 'error':
            return
        # Browser messages can embed field values, URLs and tokens. Only fixed
        # classifications are allowed out of memory.
        text = message.text
        error = ('json_parse_html' if re.search(r'Unexpected token\s*[\'\"]?<|not valid JSON', text, re.I)
                 else 'resource_load_error' if re.search(r'Failed to load resource', text, re.I)
                 else 'script_error')
        self.sequence += 1
        self.append({'sequence': self.sequence, 'time': datetime.now(timezone.utc).isoformat(),
                     'host': family[0], 'family': family[1], 'console_error': error})

    def observe_request(self, request):
        self.health['request_events'] += 1
        family = request_family(request.url)
        if not family or request.method not in ('POST', 'PUT', 'PATCH', 'DELETE'):
            return
        self.sequence += 1
        event = {'sequence': self.sequence, 'time': datetime.now(timezone.utc).isoformat(),
                 'host': family[0], 'family': family[1], 'method': request.method, 'phase': 'request_started'}
        self.append(event)
        if family[1] in ('auth_init', 'auth_complete', 'auth_signin', 'auth_verify', 'shop_idms'):
            self.schedule_network('auth_request', event)

    async def sample_loop(self):
        while True:
            await self.sample_pages()
            if any(sample['family'] in ('shop_signin', 'auth_init', 'auth_complete', 'auth_other')
                   for sample in self.page_samples):
                self.schedule_network('login_visible')
            await asyncio.sleep(2)

    async def sample_pages(self):
        samples = []
        for page in self.context.pages[:12]:
            if page.is_closed() or not request_family(page.url):
                continue
            for frame in page.frames[:24]:
                family = request_family(frame.url)
                if not family:
                    continue
                sample = {'host': family[0], 'family': family[1], 'main_frame': frame is page.main_frame}
                try:
                    value = await asyncio.wait_for(frame.evaluate('''() => ({
                        user_agent:navigator.userAgent, languages:Array.from(navigator.languages),
                        platform:navigator.platform, webdriver:navigator.webdriver,
                        cookies_enabled:navigator.cookieEnabled, secure_context:isSecureContext,
                        webcrypto:!!globalThis.crypto?.subtle,
                        identity_unverified_visible:/无法验证.{0,8}身份|unable to verify|could not verify/i.test(document.body?.innerText || ''),
                        password_field_present:!!document.querySelector('input[type="password"]')
                    })'''), 2)
                    sample.update(browser_capabilities(value))
                    for key in ('identity_unverified_visible', 'password_field_present'):
                        sample[key] = value.get(key) is True
                except Exception:
                    sample['unavailable'] = True
                samples.append(sample)
        if samples != self.page_samples:
            self.page_samples = samples
            self.save()

    def observe_response(self, response):
        self.health['response_events'] += 1
        family = request_family(response.url)
        if not family:
            return
        # Successful static resources add volume without explaining a login.
        if (response.status < 400 and family[1] in ('resource', 'shop_other')
                and response.request.method in ('GET', 'HEAD')):
            return
        self.sequence += 1
        if len(self.tasks) < 64:
            self.track_task(self.capture_response_bounded(response, family, self.sequence))
        else:
            self.health['dropped_response_events'] = self.health.get('dropped_response_events', 0)+1

    async def capture_response_bounded(self, response, family, sequence):
        try:
            await asyncio.wait_for(self.capture_response(response, family, sequence), 6)
        except asyncio.TimeoutError:
            self.health['capture_errors'] += 1
            self.health['last_capture_error'] = 'TimeoutError'
            self.save()

    async def transport_metadata(self, response):
        result = {}
        async def read(method):
            try:
                return await asyncio.wait_for(getattr(response, method)(), 1)
            except Exception:
                return None
        peer, security, http = await asyncio.gather(
            read('server_addr'), read('security_details'), read('http_version'))
        if isinstance(peer, dict):
            try:
                address = ipaddress.ip_address(peer.get('ipAddress', ''))
                result['peer_ip_version'] = address.version
                result['peer_scope'] = 'public' if address.is_global else 'local'
                result['peer_fingerprint'] = hashlib.sha256(str(address).encode()).hexdigest()[:16]
            except ValueError:
                pass
        if isinstance(security, dict) and security.get('protocol') in ('TLS 1.2', 'TLS 1.3'):
            result['tls_protocol'] = security['protocol']
        if http in ('HTTP/1.0', 'HTTP/1.1', 'HTTP/2', 'HTTP/3', 'h2', 'h3'):
            result['http_version'] = http
        return result or {'available': False}

    async def capture_response(self, response, family, sequence):
        try:
            content_type = (await response.header_value('content-type') or '').split(';')[0].strip().lower()
            if content_type not in ('application/json', 'text/html', 'text/javascript', 'application/javascript', 'text/css'):
                content_type = 'other'
            request = response.request
            method = request.method if request.method in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS') else 'OTHER'
            resource = request.resource_type if request.resource_type in ('document', 'xhr', 'fetch', 'script', 'stylesheet', 'image', 'font', 'other') else 'other'
            event = {'sequence': sequence, 'time': datetime.now(timezone.utc).isoformat(), 'host': family[0],
                     'family': family[1], 'method': method, 'resource_type': resource,
                     'status': response.status, 'content_type': content_type}
            auth_event = family[1] in ('auth_init', 'auth_complete', 'auth_signin', 'auth_verify', 'shop_idms')
            if auth_event or response.status in (403, 429, 503, 541):
                self.schedule_network('auth_response' if auth_event else 'request_failure', event)
                event['transport'] = await self.transport_metadata(response)
            # Status alone misses Apple's JSON service error behind HTTP 200.
            inspect = response.status >= 400 or (family[1].startswith('auth_') and content_type == 'application/json')
            if inspect and content_type in ('application/json', 'text/html'):
                length = await response.header_value('content-length')
                if length and length.isdigit() and int(length) > 1024 * 1024:
                    event['body_kind'] = 'too_large'
                else:
                    try:
                        text = await asyncio.wait_for(response.text(), 3)
                        event.update(response_shape(text, content_type))
                    except Exception:
                        event['body_kind'] = 'unavailable'
            self.append(event)
        except Exception as exc:
            # Diagnostics must never interrupt authentication or purchasing.
            self.health['capture_errors'] += 1
            self.health['last_capture_error'] = type(exc).__name__[:40]
            self.save()

    def observe_failure(self, request):
        family = request_family(request.url)
        if not family:
            return
        failure = request.failure or ''
        match = re.search(r'\bnet::ERR_[A-Z_]+\b', failure)
        self.sequence += 1
        event = {'sequence': self.sequence, 'time': datetime.now(timezone.utc).isoformat(), 'host': family[0],
                 'family': family[1], 'network_error': match[0] if match else 'unknown'}
        self.append(event)

        self.schedule_network('connection_failure', event)

    def append(self, event):
        self.events.append(event)
        if event.get('status', 0) >= 400 or event.get('service_error_codes') or event.get('network_error'):
            self.progress('network_diagnosis', event)
        if not self.writer or self.writer.done():
            self.writer = asyncio.create_task(self.write_later())

    async def write_later(self):
        await asyncio.sleep(.25)
        self.save()

    def save(self):
        try:
            write_private_json(self.path, {'started_at': self.started, 'browser': self.kind, 'runtime': self.runtime,
                'page_samples': self.page_samples, 'health': self.health,
                'network_samples': list(self.network_samples), 'events': list(self.events)})
        except OSError:
            pass

    async def close(self):
        if self.context is None:
            return
        self.closed = True
        if self.sampler:
            self.sampler.cancel()
            await asyncio.gather(self.sampler, return_exceptions=True)
        if self.context:
            self.context.remove_listener('request', self.observe_request)
            self.context.remove_listener('response', self.observe_response)
            self.context.remove_listener('requestfailed', self.observe_failure)
            self.context.remove_listener('page', self.observe_page)
        for page in self.pages:
            page.remove_listener('console', self.observe_console)
        if self.tasks:
            pending = tuple(self.tasks)
            try:
                await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), 5)
            except asyncio.TimeoutError:
                await asyncio.gather(*pending, return_exceptions=True)
        if self.writer and not self.writer.done():
            self.writer.cancel()
            await asyncio.gather(self.writer, return_exceptions=True)
        self.save()
