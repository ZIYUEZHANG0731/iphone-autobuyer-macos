"""Read Apple confirmation pages opened by one submission, without paying."""
from datetime import timedelta
import re
from urllib.parse import urlsplit

from playwright.async_api import Error as PlaywrightError

from app.models.config import apple_url
from app.models.flow import Receipt


def normalized(text):
    return ' '.join(text.split())


async def read_receipt(page, submitted_at=None):
    if page.is_closed() or not apple_url(page.url):
        return None
    if urlsplit(page.url).path not in ('/shop/checkout/thankyou', '/shop/checkout/interstitial'):
        return None
    order = page.get_by_role('link', name=re.compile(r'^订单\s*#[A-Z]\d{8,16}$')).filter(visible=True)
    heading = page.get_by_role('heading', level=1).filter(visible=True)
    if await order.count() != 1 or await heading.count() != 1:
        return None
    title = await heading.inner_text()
    if re.search(r'失败|已取消|已关闭|无法|未能', title):
        return None
    pending = bool(re.search(r'订单.*(?:等待付款|尚未付款|未付款|待付款)', title))
    if not pending and not re.search(r'(?:订单.*(?:已收到|已提交|已确认|正在处理)|(?:感谢|谢谢).*?(?:订单|购买))', title):
        return None
    order_id = re.search(r'[A-Z]\d{8,16}', await order.inner_text())[0]
    text = await page.locator('body').inner_text()
    window = re.search(r'(?:请在|付款务必在)\s*(\d+)\s*分钟内', text)
    deadline = None
    if pending and window and submitted_at:
        deadline = (submitted_at + timedelta(minutes=int(window[1]))).isoformat()
    return Receipt(order_id, 'payment_pending' if pending else 'confirmed', deadline)


class SubmissionReceipts:
    """Observe newly opened tabs; an old receipt cannot confirm a new intent."""
    def __init__(self, source, expected_title):
        self.source = source
        self.context = source.context
        self.expected_title = normalized(expected_title)
        self.existing = set(self.context.pages)
        self.opened = []
        self.context.on('page', self._opened)

    def _opened(self, page):
        if page not in self.existing and page not in self.opened:
            self.opened.append(page)

    def detach(self):
        self.context.remove_listener('page', self._opened)

    async def read(self, submitted_at):
        matches = []
        for page in (self.source, *self.opened):
            try:
                receipt = await read_receipt(page, submitted_at)
                if not receipt:
                    continue
                if page is not self.source:
                    # Wallet tabs can lose their opener. Only a new tab in
                    # this context whose visible product matches Review can
                    # confirm the submission, even when an opener is known.
                    titles = await page.get_by_role('heading', level=2).filter(visible=True).all_text_contents()
                    if not self.expected_title or sum(normalized(t) == self.expected_title for t in titles) != 1:
                        continue
                matches.append((page, receipt))
            except PlaywrightError:
                continue
        if len({receipt.order_id for _, receipt in matches}) != 1:
            return None
        return matches[-1]
