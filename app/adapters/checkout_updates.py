"""Wait for normal checkout writes already initiated by the page."""
import asyncio
from urllib.parse import urlsplit

from app.models.config import apple_url
from app.models.flow import UserAction


class CheckoutUpdates:
    def __init__(self, page):
        self.page = None
        self.pending = {}
        self.failed = False
        self.changed = asyncio.Event()
        self.attach(page)

    def attach(self, page):
        if page is not self.page:
            self.pending.clear()
            self.failed = False
            self.changed.set()
        self.page = page
        page.on('request', self.started)
        page.on('response', self.response)
        page.on('requestfinished', self.finished)
        page.on('requestfailed', self.failure)

    def detach(self):
        for event, callback in (('request', self.started), ('response', self.response),
                                ('requestfinished', self.finished), ('requestfailed', self.failure)):
            self.page.remove_listener(event, callback)

    def started(self, request):
        path = urlsplit(request.url).path
        if (apple_url(request.url) and (path == '/shop/checkout' or path.startswith('/shop/checkout/'))
                and request.method not in {'GET', 'HEAD', 'OPTIONS'}):
            if not self.pending:
                self.failed = False
            # Only transient request identity and response success are retained.
            # No URLs, bodies, headers or contact values enter diagnostics.
            self.pending[request] = False
            self.changed.set()

    def response(self, response):
        if response.request in self.pending:
            self.pending[response.request] = response.status < 400

    def finished(self, request):
        if request in self.pending:
            self.failed |= not self.pending.pop(request)
            self.changed.set()

    def failure(self, request):
        if request in self.pending:
            self.pending.pop(request)
            self.failed = True
            self.changed.set()

    async def ready(self, control, timeout=8):
        deadline = asyncio.get_running_loop().time() + timeout
        while self.pending:
            control.check()
            if self.page.is_closed():
                raise UserAction('官网购买页面已关闭，未继续提交取货门店')
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise UserAction('官网仍在保存取货选择，当前购物袋保留；请求完成后可继续检查')
            self.changed.clear()
            try:
                await asyncio.wait_for(self.changed.wait(), min(.1, remaining))
            except TimeoutError:
                pass
        control.check()
        if self.failed:
            raise UserAction('官网未完成取货选择的保存，当前购物袋保留，请查看官网提示后继续')
