from __future__ import annotations
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import struct
from types import SimpleNamespace
import pytest
from playwright.async_api import async_playwright
from app.adapters.apple import AppleAdapter
from app.automation.control import Control, ProcessLock
from app.gui.worker import Worker
from app.models.flow import FlowError, RateLimited, Stopped, UserAction
from app.services.payment import PaymentStore
from app.services.simulation import local_config, SimStore

# Tiny PNG header suffices for persistence validation; actual rendered PNG is
# independently checked in the browser fixture below.
PNG = b'\x89PNG\r\n\x1a\nfixture'

def test_payment_deadline_is_not_reset_by_late_inspection(tmp_path):
    store = PaymentStore(tmp_path)
    submitted = datetime.now(timezone.utc)-timedelta(minutes=31)
    handoff = store.save(PNG, order_id='W1234567890', payment_window_minutes=30, submitted_at=submitted)
    assert handoff.expired()
    assert store.latest().payment_deadline == (submitted+timedelta(minutes=30)).isoformat()
    assert store.image_path(handoff).stat().st_mode & 0o777 == 0o600
    assert store.directory.stat().st_mode & 0o777 == 0o700
    assert 'source_url' not in (store.directory/'latest.json').read_text()

def test_relative_deadline_without_submission_time_remains_unknown(tmp_path):
    handoff = PaymentStore(tmp_path).save(PNG, order_id='W1234567890', payment_window_minutes=30)
    assert handoff.payment_deadline is None

@pytest.mark.parametrize('image_file', ['../../other.png', '/tmp/other.png', 'not-a-reference.png'])
def test_payment_image_cannot_read_outside_private_directory(tmp_path, image_file):
    store = PaymentStore(tmp_path)
    handoff = store.save(PNG, order_id='W1234567890')
    handoff.image_file = image_file
    with pytest.raises(FlowError): store.image_path(handoff)

@pytest.mark.parametrize(('status','kind','blocking'), [(403,'image',False),(403,'document',True),(541,'fetch',True),(429,'image',True)])
async def test_real_rate_limit_is_distinguished_from_asset_failure(status,kind,blocking):
    adapter = AppleAdapter(SimpleNamespace(on=lambda *_:None), Control(False))
    adapter._response(SimpleNamespace(url='https://www.apple.com.cn/shop/product/fixture', status=status,
        request=SimpleNamespace(resource_type=kind), headers={'retry-after':'120'}))
    assert (adapter.http_status != 200) == blocking
    if blocking: assert adapter.limited_until-asyncio.get_running_loop().time() > 119

@pytest.fixture
async def browser():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        yield b
        await b.close()

RECEIPT = '''<main><h1>你的订单正在等待付款，请使用“掌上生活” App 扫描二维码支付。</h1>
<p>请在 30 分钟内完成付款，以免订单被取消。</p>
<p>PRIVATE-CONTACT-MUST-NOT-BE-IN-IMAGE</p>
<img alt="CMB 二维码" width="238" height="238" src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='238' height='238'%3E%3Crect width='238' height='238' fill='white'/%3E%3Crect x='20' y='20' width='60' height='60' fill='black'/%3E%3C/svg%3E">
<p>请使用“掌上生活” App 扫码支付</p><a href="/order/fixture">订单 #W1234567890</a></main>'''

@pytest.mark.e2e
async def test_extracts_only_rendered_qr_from_offline_official_dom_fixture(browser,tmp_path):
    context=await browser.new_context();page=await context.new_page()
    await page.route('**/*',lambda route:route.fulfill(body=RECEIPT,content_type='text/html; charset=utf-8'))
    await page.goto('https://www.apple.com.cn/shop/checkout/thankyou')
    adapter=AppleAdapter(page,Control(False));store=PaymentStore(tmp_path)
    try:
        handoff=await adapter.extract_payment_qr(store,review={'product':'离线控件测试商品','total':'6799','payment':'招商银行','installment_months':24})
        image=store.image_path(handoff).read_bytes()
        # Subpixel DOM coordinates may add one pixel when Chromium clips the image.
        assert all(238<=side<=239 for side in struct.unpack('>II',image[16:24]))
        assert handoff.order_id=='W1234567890' and handoff.payment_app=='掌上生活'
        assert handoff.installment_months==24 and handoff.payment_window_minutes==30
        assert handoff.payment_deadline is None
        assert 'PRIVATE-CONTACT' not in (store.directory/'latest.json').read_text()
    finally:await context.close()

@pytest.mark.e2e
async def test_off_domain_receipt_cannot_extract_a_payment_code(browser,tmp_path):
    context=await browser.new_context();page=await context.new_page()
    await page.route('**/*',lambda route:route.fulfill(body=RECEIPT,content_type='text/html; charset=utf-8'))
    await page.goto('https://apple.com.cn.attacker.invalid/shop/checkout/thankyou')
    try:
        with pytest.raises(UserAction):await AppleAdapter(page,Control(False)).extract_payment_qr(PaymentStore(tmp_path))
        assert not (tmp_path/'payment-qr'/'latest.json').exists()
    finally:await context.close()

@pytest.mark.e2e
async def test_multiple_visible_qrs_are_rejected(browser,tmp_path):
    context=await browser.new_context();page=await context.new_page()
    await page.route('**/*',lambda route:route.fulfill(body=RECEIPT+'<img alt="另一个二维码" src="data:image/svg+xml,%3Csvg xmlns=\'http://www.w3.org/2000/svg\' width=\'100\' height=\'100\'/%3E">',content_type='text/html; charset=utf-8'))
    await page.goto('https://www.apple.com.cn/shop/checkout/thankyou')
    try:
        with pytest.raises(UserAction):await AppleAdapter(page,Control(False)).extract_payment_qr(PaymentStore(tmp_path))
        assert not (tmp_path/'payment-qr'/'latest.json').exists()
    finally:await context.close()

@pytest.mark.e2e
async def test_worker_keeps_same_browser_and_session_cookie_between_commands(tmp_path,monkeypatch):
    cfg=local_config();cfg.keep_awake=False
    worker=Worker('login',cfg,tmp_path,headless=True,interactive=True)
    observed=[];first_ready=asyncio.Event()
    with SimStore(cfg) as server:
        async def action(session,repo):
            if not observed:
                await session.page.goto(server.url)
                await session.page.evaluate("document.cookie='fixture_session=synthetic; path=/'")
            observed.append((id(session.context),id(session.page),await session.page.evaluate("document.cookie.includes('fixture_session=synthetic')")))
            if len(observed)==2:worker.control.stop()
            return {'state':'COMPLETED','message':'offline session fixture'}
        monkeypatch.setattr(worker,'browser_action',action)
        worker.data.connect(lambda kind,_:first_ready.set() if kind=='session' else None)
        task=asyncio.create_task(worker.workflow())
        await asyncio.wait_for(first_ready.wait(),10)
        assert worker.idle
        with pytest.raises(FlowError):
            with ProcessLock(tmp_path/'autobuyer.run.lock'):pass
        assert worker.enqueue('options',cfg)
        assert not worker.enqueue('live',cfg)
        with pytest.raises(Stopped):await asyncio.wait_for(task,10)
    assert len(observed)==2 and observed[0]==observed[1]
    assert observed[0][2] is True

@pytest.mark.e2e
async def test_automatic_submission_refuses_unverified_store_even_with_authorization(browser):
    context=await browser.new_context();page=await context.new_page()
    await page.set_content('<button onclick="window.clicked=true">立即下单</button>')
    adapter=AppleAdapter(page,Control(False));adapter.mode=__import__('app.models.config',fromlist=['Mode']).Mode.LIVE
    adapter.payment='招商银行';adapter.installment_months=24
    cfg=local_config();cfg.auto_submit=True;cfg.installment_months=24
    async def unverified_review(_):raise UserAction('门店尚未可靠确认')
    adapter.review=unverified_review
    try:
        with pytest.raises(UserAction):await adapter.submit(cfg)
        assert not adapter.submit_attempted
        assert await page.evaluate('!!window.clicked') is False
    finally:await context.close()

@pytest.mark.e2e
async def test_offline_submit_uses_one_click_and_reads_real_dom_contract(browser):
    from app.models.config import Mode
    from app.models.flow import Summary, SubmissionUnknown
    from decimal import Decimal
    context=await browser.new_context();page=await context.new_page();transitions=[]
    async def fixture(route):
        if route.request.url.endswith('/thankyou'):
            transitions.append('synthetic-confirmation')
            await route.fulfill(body=RECEIPT,content_type='text/html; charset=utf-8')
        else:
            await route.fulfill(body='<button onclick="location.href=\'/shop/checkout/thankyou\'">立即下单</button>',content_type='text/html; charset=utf-8')
    await page.route('**/*',fixture)
    await page.goto('https://www.apple.com.cn/shop/checkout?_s=Review')
    cfg=local_config();cfg.auto_submit=True;cfg.installment_months=24;cfg.payments=['招商银行']
    adapter=AppleAdapter(page,Control(False),mode=Mode.LIVE);adapter.payment='招商银行';adapter.installment_months=24
    target=cfg.stores[0]
    async def verified_offline_review(_):
        return Summary(cfg.product.name,cfg.product.options,cfg.product.quantity,Decimal('100'),target.name,target.store_id,'招商银行',verified=True,installment_months=24)
    adapter.review=verified_offline_review
    try:
        receipt=await adapter.submit(cfg)
        assert receipt.order_id=='W1234567890' and receipt.status=='payment_pending'
        assert receipt.payment_deadline is not None
        with pytest.raises(SubmissionUnknown):await adapter.submit(cfg)
        assert transitions==['synthetic-confirmation']
    finally:await context.close()


def test_reopen_order_after_a_different_order_keeps_original_expiry(tmp_path):
    store=PaymentStore(tmp_path)
    submitted=datetime.now(timezone.utc)-timedelta(minutes=31)
    first=store.save(PNG,order_id='W1234567890',payment_window_minutes=30,submitted_at=submitted)
    store.save(PNG,order_id='W1234567891')
    reopened=store.for_order(first.order_id)
    assert reopened.expired() and reopened.payment_deadline==first.payment_deadline
    store.set_latest(reopened)
    assert store.latest().expired()


def test_frozen_app_uses_installed_browser_cache_without_overriding_user_setting(monkeypatch):
    import os
    import sys
    from app.utils.browser_runtime import prepare_browser_runtime
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.setattr(sys,'platform','darwin')
    monkeypatch.delenv('PLAYWRIGHT_BROWSERS_PATH',raising=False)
    prepare_browser_runtime()
    assert os.environ['PLAYWRIGHT_BROWSERS_PATH']==str(Path.home()/'Library'/'Caches'/'ms-playwright')
    monkeypatch.setenv('PLAYWRIGHT_BROWSERS_PATH','/tmp/explicit-browser-cache')
    prepare_browser_runtime()
    assert os.environ['PLAYWRIGHT_BROWSERS_PATH']=='/tmp/explicit-browser-cache'

@pytest.mark.e2e
async def test_account_maintenance_does_not_destroy_prelogged_browser_or_claim_login(browser):
    context=await browser.new_context();page=await context.new_page();observed=[]
    await context.route('**/*',lambda route:route.fulfill(status=503,body='<h1>马上回来</h1>',content_type='text/html; charset=utf-8'))
    adapter=AppleAdapter(page,Control(False),progress=lambda kind,data:observed.append((kind,data)))
    try:
        await adapter.check_login()
        assert len(context.pages)==1 and context.pages[0] is page
        assert observed[-1][0]=='login' and observed[-1][1]['authenticated'] is None
    finally:await context.close()


@pytest.mark.e2e
async def test_stopping_task_keeps_browser_cookie_and_process_lock_for_next_command(tmp_path,monkeypatch):
    cfg=local_config();cfg.keep_awake=False
    worker=Worker('website',cfg,tmp_path,headless=True,interactive=True)
    started=asyncio.Event();ready=asyncio.Event();observed=[]
    with SimStore(cfg) as server:
        async def action(session,repo):
            if worker.action=='website':
                await session.page.goto(server.url)
                await session.page.evaluate("document.cookie='task_stop_fixture=synthetic; path=/'")
                observed.append(id(session.context));started.set()
                await worker.control.sleep(60)
            else:
                assert id(session.context)==observed[0]
                assert await session.page.evaluate("document.cookie.includes('task_stop_fixture=synthetic')")
                worker.control.stop()
                return {'state':'COMPLETED','message':'offline preserved-cookie fixture'}
        monkeypatch.setattr(worker,'browser_action',action)
        worker.data.connect(lambda kind,_:ready.set() if kind=='session' else None)
        task=asyncio.create_task(worker.workflow())
        try:
            await asyncio.wait_for(started.wait(),10);worker.stop_task()
            await asyncio.wait_for(ready.wait(),10)
            assert worker.idle and worker.session_open and not worker.control.stop_event.is_set()
            with pytest.raises(FlowError):
                with ProcessLock(tmp_path/'autobuyer.run.lock'):pass
            assert worker.enqueue('options',cfg)
            with pytest.raises(Stopped):await asyncio.wait_for(task,10)
            assert not worker.session_open
        finally:
            worker.control.stop()
            await asyncio.gather(task,return_exceptions=True)


async def test_worker_cooldown_applies_to_new_commands_and_configuration_is_a_snapshot(tmp_path):
    from copy import deepcopy
    cfg=local_config();original=deepcopy(cfg)
    worker=Worker('options',cfg,tmp_path,headless=True)
    cfg.product.name='edited after start'
    assert worker.config.product.name==original.product.name
    worker.progress('http_status',{'status':541,'resource_type':'fetch','blocks_checkout':True,'wait_seconds':60})
    with pytest.raises(RateLimited):
        await worker.browser_action(None, None)
    worker.idle=True
    assert worker.enqueue('login',original)
    original.product.name='edited after enqueue'
    assert worker.commands.get_nowait()[1].product.name!='edited after enqueue'
