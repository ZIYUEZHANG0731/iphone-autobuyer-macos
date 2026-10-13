from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from playwright.async_api import async_playwright

from app.adapters.apple import AppleAdapter
from app.adapters.order_receipt import SubmissionReceipts
from app.automation.control import Control
from app.models.config import Mode
from app.models.flow import Summary, SubmissionUnknown
from app.services.simulation import local_config


TITLE = 'Demo Phone 256GB 测试紫色'
RECEIPT_URL = 'https://secure7.www.apple.com.cn/shop/checkout/interstitial'


@pytest.fixture
async def browser():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        yield browser
        await browser.close()


def confirmation(title=TITLE, order='W1234567890'):
    return (f'<h1>你的订单正在等待付款。</h1><h2>{title}</h2>'
            f'<a href="#synthetic-order">订单 #{order}</a>'
            '<p>付款务必在 30 分钟内完成，否则订单将被取消。</p>')


async def open_wallet(context, source, url):
    async with context.expect_page() as opened:
        await source.evaluate("url => window.open(url, '_blank', 'noopener')", url)
    page = await opened.value
    await page.wait_for_url(url)
    return page


async def test_one_submit_adopts_noopener_wallet_confirmation(browser):
    context = await browser.new_context()
    submissions = []

    async def fixture(route):
        if route.request.url.endswith('/fixture-order'):
            submissions.append('synthetic-order')
            await route.fulfill(body='ok')
        elif route.request.url == RECEIPT_URL:
            await route.fulfill(body=confirmation(), content_type='text/html; charset=utf-8')
        else:
            await route.fulfill(body=(
                '<button onclick="fetch(\'/fixture-order\', {method:\'POST\'})'
                '.then(() => window.open(\'' + RECEIPT_URL + '\', \'_blank\', \'noopener\'))">'
                '立即下单</button>'), content_type='text/html; charset=utf-8')

    await context.route('**/*', fixture)
    source = await context.new_page()
    await source.goto('https://www.apple.com.cn/shop/checkout')
    cfg = local_config()
    cfg.auto_submit = True
    cfg.payments = ['支付宝']
    adapter = AppleAdapter(source, Control(False), mode=Mode.LIVE)
    adapter.payment = '支付宝'
    adapter.reviewed_product_title = TITLE

    async def verified_review(_):
        store = cfg.stores[0]
        return Summary(cfg.product.name, cfg.product.options, cfg.product.quantity,
                       Decimal('100'), store.name, store.store_id, '支付宝', verified=True)

    adapter.review = verified_review
    try:
        receipt = await adapter.submit(cfg)
        assert receipt.order_id == 'W1234567890' and receipt.status == 'payment_pending'
        assert receipt.payment_deadline == (adapter.submit_started + timedelta(minutes=30)).isoformat()
        assert adapter.page is not source and await adapter.page.evaluate('window.opener === null')
        assert (await adapter.reconcile(cfg)).order_id == receipt.order_id
        with pytest.raises(SubmissionUnknown):
            await adapter.submit(cfg)
        assert submissions == ['synthetic-order']
        adapter.detach()
        count = len(adapter.submission_receipts.opened)
        await context.new_page()
        assert len(adapter.submission_receipts.opened) == count
    finally:
        adapter.detach()
        await context.close()


@pytest.mark.parametrize(('url', 'title', 'accepted'), [
    (RECEIPT_URL, TITLE, True),
    (RECEIPT_URL, 'Other Phone 128GB', False),
    ('https://wallet-provider.invalid/shop/checkout/interstitial', TITLE, False),
])
async def test_wallet_watch_excludes_old_receipts_and_unrelated_tabs(browser, url, title, accepted):
    context = await browser.new_context()

    async def fixture(route):
        body = confirmation(TITLE, 'W9999999999') if route.request.url.endswith('?old=1') else confirmation(title)
        await route.fulfill(body=body, content_type='text/html; charset=utf-8')

    await context.route('**/*', fixture)
    source = await context.new_page()
    await source.goto('https://www.apple.com.cn/shop/checkout')
    old = await context.new_page()
    await old.goto(RECEIPT_URL + '?old=1')
    watch = SubmissionReceipts(source, TITLE)
    try:
        assert await watch.read(datetime.now(timezone.utc)) is None
        opened = await open_wallet(context, source, url)
        result = await watch.read(datetime.now(timezone.utc))
        if accepted:
            assert result[0] is opened and result[1].order_id == 'W1234567890'
        else:
            assert result is None
    finally:
        watch.detach()
        await context.close()


async def test_multiple_new_order_numbers_leave_submission_uncertain(browser):
    context = await browser.new_context()
    await context.route('**/*', lambda route: route.fulfill(
        body=confirmation(order='W1111111111' if '?other=1' in route.request.url else 'W1234567890'),
        content_type='text/html; charset=utf-8'))
    source = await context.new_page()
    await source.goto('https://www.apple.com.cn/shop/checkout')
    watch = SubmissionReceipts(source, TITLE)
    try:
        await open_wallet(context, source, RECEIPT_URL)
        await open_wallet(context, source, RECEIPT_URL + '?other=1')
        assert await watch.read(datetime.now(timezone.utc)) is None
    finally:
        watch.detach()
        await context.close()
