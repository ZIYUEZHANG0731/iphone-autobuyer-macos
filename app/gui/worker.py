from __future__ import annotations
import asyncio
from dataclasses import asdict
from copy import deepcopy
from datetime import datetime, timezone
import platform
from pathlib import Path
import queue
import re
import sys
import threading
import time
import math
from playwright.async_api import Error as PlaywrightError
from urllib.parse import urlsplit
from PySide6.QtCore import QThread, Signal
from app.models.config import Mode, Identity, ConfigError, apple_url
from app.models.flow import FlowError, UserAction, Stopped, RateLimited
from app.automation.control import Control, ProcessLock
from app.automation.browser import BrowserSession, installed_chrome
from app.automation.engine import Engine
from app.adapters.apple import AppleAdapter
from app.adapters.simulation import SimulationAdapter
from app.services.simulation import SimStore, local_config
from app.services.storage import Repository
from app.services.payment import PaymentStore
from app.security.keychain import IdentityVault, CredentialVault
from app.security.privacy import Redactor
from app.utils.power import KeepAwake
from app.services.catalog_reader import read_catalog
from app.services.traffic import SiteCooldown
from app.services.signin import SignInController
from app.services.login import apple_auth_url, shop_signin_url, session_page_url
from app.services.network_diagnosis import NetworkDiagnosis


class Worker(QThread):
    event = Signal(dict)
    data = Signal(str, object)
    completed = Signal(object)

    def __init__(self, action, config, root, *, headless=False, interactive=True, parent=None, embedded=None):
        super().__init__(parent)
        self.action, self.config, self.root = action, deepcopy(config), Path(root)
        self.control, self.headless = Control(interactive,site_cooldown=SiteCooldown(self.root)), headless
        self.idle = False
        self.commands = queue.SimpleQueue()
        self.command_lock = threading.Lock()
        self.qr_requested = threading.Event()
        self.focus_requested = threading.Event()
        self.review_data = {}
        self.captured_orders = set()
        self.session_open=False
        self.embedded=embedded
        self.session_kind=''
        self.sign_in=SignInController()
        self.limited_until=time.monotonic()+self.control.site_cooldown.remaining()
        self.busy_until=time.monotonic()+self.control.site_cooldown.busy_remaining()

    def enqueue(self, action, config) -> bool:
        with self.command_lock:
            if not self.idle or action in ('local', 'install') or self.control.stop_event.is_set():
                return False
            self.idle = False
            self.commands.put((action, deepcopy(config)))
            return True

    def stop_task(self):
        if self.idle:
            return
        if not self.session_open:
            self.control.stop()
        else:
            self.control.cancel_task()

    def run(self):
        try:
            result = asyncio.run(self.workflow())
            if result is not None:
                self.completed.emit(result)
        except Stopped:
            self.completed.emit({'state': 'STOPPED', 'message': '已关闭浏览器会话；持久配置保留，订单不会自动取消'})
        except (FlowError, ConfigError) as exc:
            self.completed.emit({'state': 'PAUSED', 'message': str(exc)})
        except Exception as exc:
            self.completed.emit({'state': 'FAILED', 'message': f'检查未完成（{type(exc).__name__}），请检查浏览器或环境'})

    def status(self, message, state='PREPARING'):
        self.event.emit({'state': state, 'message': message, 'mode': self.action})

    def progress(self, kind, data):
        if kind == 'review':
            self.review_data = dict(data)
        if kind == 'http_status' and data.get('blocks_checkout'):
            self.limited_until=max(self.limited_until,time.monotonic()+data.get('wait_seconds',60))
        if kind=='http_status' and data.get('affects')=='store_busy':
            self.busy_until=max(self.busy_until,time.monotonic()+data.get('wait_seconds',0))
        if kind == 'stores':
            changes=Repository(self.root/'autobuyer.sqlite3').observe_stores(self.config.product,data)
            self.data.emit('stock_changes',changes)
        self.data.emit(kind, data)

    async def environment(self):
        if self.config.browser_kind=='daily_chrome':
            from app.services.daily_chrome import extension_installed
            installed=extension_installed()
            return {'state':'COMPLETED','message':f'macOS {platform.mac_ver()[0]} · {platform.machine()}\n日常 Chrome · '+('官方连接扩展已安装，请接入后复核官网登录' if installed else '请从官网浏览器页面安装官方 Playwright Extension'),
                    'browser_installed':bool(installed_chrome() and installed)}
        if self.config.browser_kind=='embedded':
            from importlib.util import find_spec
            available=find_spec('PySide6.QtWebEngineWidgets') is not None
            from PySide6.QtCore import qVersion
            return {'state':'COMPLETED','message':f'macOS {platform.mac_ver()[0]} · {platform.machine()}\n内置 Chromium / Qt {qVersion()} · 持久会话\nmacOS Keychain 用于取货人资料','browser_installed':available}
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            installed = Path(pw.chromium.executable_path).exists()
        chrome=installed_chrome()
        from keyring.backends.macOS import Keyring
        return {'state': 'COMPLETED', 'message': f'macOS {platform.mac_ver()[0]} · {platform.machine()} · Python {platform.python_version()}\n浏览器：' + ('Google Chrome（软件独立会话）' if chrome else 'Chromium 已安装' if installed else '未安装，请点击安装浏览器') + '\nmacOS Keychain 后端：' + type(Keyring()).__name__, 'browser_installed': bool(chrome or installed)}

    async def workflow(self):
        repo = Repository(self.root / 'autobuyer.sqlite3', Redactor())
        if self.action == 'environment':
            return await self.environment()
        if self.action == 'install':
            cmd = [sys.executable, '--install-browser'] if getattr(sys, 'frozen', False) else [sys.executable, '-m', 'playwright', 'install', 'chromium']
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            if await proc.wait():
                raise FlowError('浏览器下载未成功，请检查网络后重试')
            return {'state': 'COMPLETED', 'message': 'Chromium 已安装', 'browser_installed': True}
        if self.action == 'local':
            config = local_config(self.config.scenario)
            with SimStore(config, config.scenario) as server:
                async with BrowserSession(self.root, headless=self.headless, isolated=True) as session:
                    engine = Engine(repo, self.control, callback=self.event.emit)
                    identity = None if config.scenario == 'identity_incomplete' else Identity.synthetic()
                    stop_task = None
                    if config.scenario == 'stop':
                        async def stopper():
                            await asyncio.sleep(.8)
                            self.control.stop()
                        stop_task = asyncio.create_task(stopper())
                    result = await engine.run(config, SimulationAdapter(session.page, server, self.control), Mode.LOCAL_TEST, identity)
                    if stop_task:
                        await stop_task
                    self.data.emit('simulation', {'submit_count': server.submit_count, 'synthetic_orders': len(server.orders)})
                    return asdict(result)
        # One process stays open through login, setup and the scheduled run.
        # This retains session-only cookies, which may disappear on a restart.
        with ProcessLock(repo.path.with_suffix('.run.lock')):
            if self.config.browser_kind=='daily_chrome':
                self.status('请在日常 Chrome 的官方扩展连接页选择 Apple 中国商店页面并允许本次连接…')
            async with KeepAwake(self.config.keep_awake), BrowserSession(self.root, headless=self.headless,embedded=self.embedded,
                    kind=self.config.browser_kind,stop_check=self.control.check_session) as session:
                self.session_open=True
                browser_kind='daily_chrome' if self.config.browser_kind=='daily_chrome' else 'embedded' if session.embedded else 'chrome'
                self.session_kind=browser_kind
                self.data.emit('browser',{'name':session.label,'kind':browser_kind})
                diagnosis=NetworkDiagnosis(self.root,browser_kind,progress=self.progress)
                if browser_kind!='daily_chrome' or self.action=='login_diagnosis':
                    await diagnosis.start(session.context,session.page)
                watcher = asyncio.create_task(self.watch_payment(session, repo))
                try:
                    while True:
                        self.control.check_session()
                        self.control.resume_event.clear()
                        try:
                            self.control.check()
                            if self.action=='login_diagnosis' and diagnosis.context is None:
                                await diagnosis.start(session.context,session.page)
                            result = await self.browser_action(session, repo)
                        except Stopped:
                            if self.control.stop_event.is_set():
                                raise
                            result = {'state': 'STOPPED', 'message': '当前任务已停止，官网浏览器会话保持打开；已有订单和提交锁保留'}
                        except (FlowError, ConfigError) as exc:
                            result = {'state': 'PAUSED', 'message': str(exc)}
                        except PlaywrightError as exc:
                            result = {'state': 'PAUSED', 'message': '官网页面已关闭，请重新检查登录状态；软件浏览器会话保留' if type(exc).__name__=='TargetClosedError' else '官网页面尚未完成，请处理浏览器提示后重新检查；软件会话保留'}
                        if not self.control.interactive:
                            return result
                        self.control.reset_task()
                        self.completed.emit(result)
                        with self.command_lock:
                            self.idle = True
                        self.data.emit('session', {'ready': True, 'message': '浏览器会话保持打开，可继续设置或启动抢购；“关闭会话”才会关闭浏览器'})
                        while True:
                            self.control.check()
                            try:
                                self.action, self.config = self.commands.get_nowait()
                                break
                            except queue.Empty:
                                await self.control.sleep(.1)
                finally:
                    watcher.cancel()
                    await asyncio.gather(watcher, return_exceptions=True)
                    await diagnosis.close()
                    self.session_open=False

    async def browser_action(self, session, repo):
        if self.action not in ('environment','receipt') and time.monotonic()<self.limited_until:
            raise RateLimited(f'官网访问限制仍需等待 {math.ceil(self.limited_until-time.monotonic())} 秒；保留当前会话，等待后再检查')
        if self.action not in ('environment','receipt') and time.monotonic()<self.busy_until:
            self.status(f'官网暂时繁忙，等待 {math.ceil(self.busy_until-time.monotonic())} 秒后检查，登录会话保留','STORE_UNAVAILABLE')
            await self.control.sleep(max(0,self.busy_until-time.monotonic()))
        if self.action == 'environment':
            return await self.environment()
        if hasattr(session,'ensure_page'):
            await session.ensure_page()
        def read_credential(credential_id):
            credential=CredentialVault().get(credential_id)
            if repo:repo.redactor.register(credential)
            return credential
        login_options=dict(login_config=self.config,credential_provider=read_credential,
                           sign_in=self.sign_in,page_focus=getattr(session,'focus_page',None))
        if self.action in ('website', 'live'):
            self.review_data = {}
            mode = Mode.WEBSITE_DRY_RUN if self.action == 'website' else Mode.LIVE
            identity = IdentityVault().get(self.config.profile_id) if self.config.profile_id and self.config.transmit_identity else None
            if identity:
                repo.redactor.register(identity)
            adapter = AppleAdapter(session.page, self.control, mode=mode, progress=self.progress,page_factory=getattr(session,'new_page',None),page_closer=getattr(session,'close_page',None),**login_options)
            self.active_adapter = adapter
            try:
                return asdict(await Engine(repo, self.control, callback=self.event.emit, lock_held=True).run(self.config, adapter, mode, identity))
            finally:
                adapter.detach()
        adapter = AppleAdapter(session.page, self.control, progress=self.progress,page_factory=getattr(session,'new_page',None),page_closer=getattr(session,'close_page',None),**login_options)
        self.active_adapter = adapter
        try:
            if self.action == 'catalog':
                result=await read_catalog(adapter,on_progress=lambda count,name:self.status(f'已从官网更新 {count} 个机型：{name}'))
                items = result['products']
                self.data.emit('catalog', items)
                return {'state': 'COMPLETED', 'message': f'已从官网更新 {len(items)} 个机型的配置' + ('；部分页面暂未读到，现有目录保留' if result['unread_pages'] else '')}
            if self.action in ('login','login_diagnosis'):
                diagnostic=self.action=='login_diagnosis'
                pending=self.sign_in.pending_page
                if pending and not pending.is_closed():
                    session.page=pending;adapter.bind_page(pending)
                elif session_page_url(session.page.url) and await adapter.check_login(auto_recover=False) is True:
                    self.sign_in.authenticated(self.config.credential_id)
                    return {'state':'COMPLETED','message':'原有登录仍有效。继续使用同一浏览器会话，无需重新登录'}
                elif not (apple_auth_url(session.page.url) or shop_signin_url(session.page.url)):
                    self.status('正在从商店购物袋打开官网登录入口…')
                    await adapter.open_store_signin(session.page)
                if await adapter.login_status(timeout=1) is True:
                    self.sign_in.authenticated(self.config.credential_id)
                    return {'state':'COMPLETED','message':'原有登录仍有效。继续使用同一浏览器会话，无需重新登录'}
                await adapter.focus_login(session.page)
                message='已从购物袋进入登录诊断，请在官网完成一次登录，遇到错误后可查看控制台的请求记录。完成后点“继续检查”。' if diagnostic else '已从商店购物袋打开登录页，请在软件浏览器完成 Apple 登录和验证，再点“继续检查”。此浏览器会保持打开。'
                if self.config.auto_login and not diagnostic:
                    try:
                        if await adapter.auto_sign_in(session.page):
                            await adapter.confirm_login();return {'state':'COMPLETED','message':'已使用保存的账户登录并确认；浏览器会话持续保留'}
                    except UserAction as exc:
                        if isinstance(exc,RateLimited):raise
                        message=str(exc);self.sign_in.pending_page=session.page
                        await adapter.focus_login(session.page)
                self.status(message, 'PAUSED')
                if not self.control.interactive:
                    return {'state': 'PAUSED', 'message': '等待你在 Apple 官网登录'}
                await self.control.wait_resume()
                if hasattr(session,'ensure_page'):
                    original=adapter.page
                    current=await session.ensure_page(prefer_account=True)
                    if current is not original:
                        adapter.bind_page(current)
                if self.config.auto_login and not diagnostic:await adapter.auto_sign_in(adapter.page)
                await adapter.confirm_login()
                self.sign_in.authenticated(self.config.credential_id)
                return {'state': 'COMPLETED', 'message': '已确认登录。浏览器会话持续保留，可直接设置开售时间并开始等待'}
            if self.action == 'login_check':
                checked=await adapter.check_login()
                return {'state': 'COMPLETED', 'message': '当前浏览器登录已确认；Apple 仍可在结账时要求重新验证' if checked is True else '官网明确要求登录；已保留会话，结账按预选策略处理' if checked is False else '官网暂时无法复核登录；现有浏览器会话保留，结账时再检查'}
            if self.action == 'receipt':
                await self.capture_payment(session, repo, requested=True)
                return {'state': 'COMPLETED', 'message': '已读取官网当前付款二维码'}
            self.config.validate(Mode.WEBSITE_DRY_RUN, require_stores=False)
            await session.page.goto(self.config.product.url, wait_until='domcontentloaded')
            await adapter.guard()
            if self.action == 'options':
                choices = await adapter.options()
                self.data.emit('options', choices)
                return {'state': 'COMPLETED', 'message': f'已读取 {len(choices)} 个官网商品控件；不同商品的必选项可能分步出现'}
            if self.action == 'stores':
                self.config.validate(Mode.WEBSITE_DRY_RUN)
                await adapter.select_product(self.config)
                states = await adapter.availability(self.config)
                return {'state': 'COMPLETED', 'message': '已读取官网自提选项；此结果不代表库存已锁定'}
            raise FlowError('未知检查任务')
        finally:
            adapter.detach()

    async def watch_payment(self, session, repo):
        while not self.control.stop_event.is_set():
            if self.focus_requested.is_set():
                self.focus_requested.clear()
                try:
                    page=await session.ensure_page()
                    if hasattr(session,'focus_page'):await session.focus_page()
                    else:await page.bring_to_front()
                    if self.embedded:self.data.emit('embedded_focus',True)
                except Exception:self.data.emit('browser_focus_error','软件浏览器尚未准备好，请查看当前会话状态')
            requested = self.qr_requested.is_set()
            self.qr_requested.clear()
            at_receipt = urlsplit(session.page.url).path == '/shop/checkout/thankyou'
            if requested or at_receipt:
                try:
                    await self.capture_payment(session, repo, requested=requested)
                except (FlowError, ConfigError):
                    if requested:
                        self.data.emit('payment_error', '请在软件自己的 Chromium 中进入显示付款二维码的 Apple 订单确认页，再点“提取当前付款码”')
                except Exception:
                    if requested:
                        self.data.emit('payment_error', '二维码提取未完成，请核对官网是否已显示完整付款码')
            await asyncio.sleep(1)

    async def capture_payment(self, session, repo, *, requested=False):
        adapter = getattr(self, 'active_adapter', None)
        if not adapter:
            raise UserAction('官网浏览器尚未准备好')
        adapter.origin()
        store = PaymentStore(self.root)
        order = session.page.get_by_role('link', name=re.compile(r'^订单\s*#[A-Z]\d{8,16}$'))
        order_id = None
        if urlsplit(session.page.url).path == '/shop/checkout/thankyou' and await order.count() == 1:
            order_id = re.search(r'[A-Z]\d{8,16}', await order.inner_text())[0]
        if order_id in self.captured_orders:
            if requested:
                handoff = store.for_order(order_id)
                if handoff:
                    store.set_latest(handoff)
                    self.data.emit('payment_qr', asdict(handoff))
            return
        previous = store.for_order(order_id) if order_id else None
        if previous:
            handoff = previous
            store.set_latest(handoff)
        else:
            handoff = await adapter.extract_payment_qr(store, review=self.review_data, submitted_at=adapter.submit_started)
        self.captured_orders.add(handoff.order_id)
        manual_run = 'manual-' + handoff.order_id
        existing = repo.submission(self.config.intent_id)
        if not existing:
            if repo.claim_submission(self.config.intent_id, manual_run):
                repo.record_submission(self.config.intent_id, 'CONFIRMED', handoff.order_id)
        elif existing.get('order_id') and existing['order_id'] != handoff.order_id:
            self.data.emit('payment_error', '此购买计划已有另一个订单；二维码已按当前官网订单单独保存，请人工核对')
        repo.update_run(manual_run, 'live', 'PAYMENT_PENDING', f'官网已生成待付款订单，尾号 {handoff.order_id[-4:]}；二维码已提取，付款结果仍需官网确认')
        self.data.emit('payment_qr', asdict(handoff))
