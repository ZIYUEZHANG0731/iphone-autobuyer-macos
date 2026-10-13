from __future__ import annotations
from app.platforms.files import protect_path
import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit
from pathlib import Path
from uuid import uuid4
from playwright.async_api import Page, Locator, expect, TimeoutError as PlaywrightTimeout, Error as PlaywrightError
from app.models.config import Store, Mode, apple_url, money
from app.models.flow import FlowError, UserAction, RateLimited, Availability, Summary, Receipt, SubmissionUnknown
from app.automation.control import Control
from app.services.payment import PaymentStore
from app.services.payment_methods import payment_matches, payment_months, payment_app
from app.services.pricing import quote_key
from app.services.catalog import exact_product_link,preorder_time
from app.services.catalog_reader import public_links
from app.services.sku import family_url
from app.services.login import (account_page_url, apple_auth_url, shop_signin_url,
                               visible_login_form, session_page_url)
from app.adapters.apple_login import AppleLogin
from app.adapters.apple_consent import AppleConsent
from app.adapters.controls import ensure_checked
from app.adapters.checkout_state import checkout_state
from app.adapters.checkout_updates import CheckoutUpdates
from app.adapters.apple_pickup import ApplePickup, pickup_date
from app.adapters.bag_state import bag_has_items
from app.services.signin import SignInController
from app.services.stores import StoreDirectory
from copy import deepcopy

# Extract observable DOM controls and verify choices, including SKU landing pages.
CONTROL_SCRIPT = '''() => {
 const root = document.querySelector('main,[role="main"]') || document.body;
 const readable = n => n ? (n.innerText?.trim() || n.getAttribute('aria-label') || n.getAttribute('alt') || Array.from(n.querySelectorAll('img[alt]')).map(i=>i.alt).join(' ')) : '';
 const label = e => e.getAttribute('aria-label') || Array.from(e.labels || []).map(readable).join(' ').trim() ||
   (e.getAttribute('aria-labelledby') || '').split(' ').map(id=>readable(document.getElementById(id))).join(' ');
 return Array.from(root.querySelectorAll('input[type="radio"],select')).map(e=>({
   name:e.name,id:e.id,value:e.value,label:label(e).replace(/\\s+/g,' ').trim(),checked:e.checked || false,
   disabled:e.disabled || e.getAttribute('aria-disabled')==='true',
   visible:!!e.getClientRects().length && getComputedStyle(e).visibility!=='hidden',
   group:e.closest('[role="group"],fieldset')?.getAttribute('aria-label') || '',
   kind:e.tagName==='SELECT'?'select':'radio',
   options:e.tagName==='SELECT'?Array.from(e.options).map(o=>({value:o.value,label:o.textContent.trim(),disabled:o.disabled})):[]
 }));
}'''

class AppleAdapter(AppleLogin, AppleConsent, ApplePickup):
    # The selected, saved payment is verified again at Review before submission.
    @property
    def supports_submit(self):
        return bool(self.payment)
    def __init__(self, page: Page, control: Control, *, mode=Mode.WEBSITE_DRY_RUN, progress=None, public_screenshot: Path|None=None, page_factory=None, page_closer=None, login_config=None, credential_provider=None, sign_in=None, page_focus=None):
        self.page = page
        self.control = control
        self.mode = mode
        self.progress = progress or (lambda *_: None)
        self.http_status = 200
        self.opening_retry = False
        self.response_episode = uuid4().hex
        self.last_document_status = 200
        self.limited_until = 0.0
        self.selected = None
        self.privacy_seen = False
        self.privacy_config = None
        self.preconsent_used = False
        self.identity_filled = False
        self.payment = ""
        self.installment_months = 0
        self.cart_checked = False
        self.add_attempted = False
        self.public_screenshot = public_screenshot
        self.options_snapshot = []
        self.browse_unavailable = False
        self.browse_connection_failed = False
        self.target_url = ''
        self.delivery_preview_failed = False
        self.submit_attempted = False
        self.review_advance_attempted = False
        self.submit_started = None
        self.waiting_for_link=False
        self.busy_until=0.;self.in_queue=False;self.navigation_pending=False;self.navigation_started=0.
        self.dom_ready=None
        self.page_factory=page_factory
        self.page_closer=page_closer or (lambda page:page.close())
        self.store_directory=StoreDirectory()
        self.stock_region=None
        self.guest_attempted=False
        self.pickup_continue_attempted=False
        self.checkout_last_continued=''
        self.checkout_pickup_confirmed=None
        self.checkout_continued_stores=set()
        self.checkout_more_attempted=False
        self.checkout_observed_stage=None
        self.login_config=login_config
        self.credential_provider=credential_provider
        self.sign_in=sign_in or SignInController()
        self.page_focus=page_focus
        self.checkout_updates=CheckoutUpdates(page)
        self.page.on("response", self._response)
        self.page.on('domcontentloaded',self._dom_loaded)

    def _dom_loaded(self,*_):
        self.dom_ready=True
        if not urlsplit(self.page.url).path.startswith('/shop/checkout'):
            self.checkout_pickup_confirmed=None
            self.review_advance_attempted=False

    def detach(self):
        self.checkout_updates.detach()
        self.page.remove_listener('response',self._response)
        self.page.remove_listener('domcontentloaded',self._dom_loaded)

    def bind_page(self,page):
        changed=page is not self.page
        self.detach();self.page=page;self.dom_ready=None;self.last_document_status=200
        if changed:
            self.auth_service_status=None
            self.checkout_pickup_confirmed=None
            self.review_advance_attempted=False
        self.checkout_updates.attach(page)
        page.on('response',self._response);page.on('domcontentloaded',self._dom_loaded)

    def _response(self,response):
        auth_response=apple_auth_url(response.url)
        if not apple_url(response.url) and not auth_response:
            return
        path=urlsplit(response.url).path
        auth_completion = auth_response and (path.endswith('/signin/complete') or '/auth/verify' in path)
        auth_restarted = response.request.resource_type=='document' and (
            auth_response or shop_signin_url(response.url) or session_page_url(response.url))
        if ((response.status < 400 and (auth_restarted or auth_completion))
                or (auth_response and path.endswith('/signin/complete') and response.status==409)):
            # Apple's normal 409 can advance to two-factor authentication.
            # Clear a prior service failure; this alone never confirms login.
            recovered=bool(getattr(self,'auth_service_status',None))
            self.auth_service_status=None
            if recovered:
                self.progress('login',{'authenticated':None,'status':'authentication_progress',
                    'checked_at':datetime.now(timezone.utc).isoformat(),
                    'reason':'当前认证流程已继续；请完成官网验证，再检查有效登录'})
        if response.request.resource_type=='document' and getattr(response.request,'frame',None) is getattr(self.page,'main_frame',None):
            self.dom_ready=False;self.navigation_started=asyncio.get_running_loop().time()
            self.response_episode=uuid4().hex
            self.last_document_status=response.status
        if response.status==503:
            path=urlsplit(response.url).path
            resource_type=response.request.resource_type
            shop_request=(path.startswith('/shop/') and resource_type in {'document','xhr','fetch'})
            if not (auth_response or shop_request):
                self.progress('http_status',{'status':503,'resource_type':resource_type,'blocks_checkout':False,
                    'wait_seconds':0,'context':'resource','affects':'resource'})
                return
            retry=response.headers.get('retry-after','');wait=0
            try:wait=int(retry) if retry.isdigit() else max(0,int((parsedate_to_datetime(retry)-datetime.now(timezone.utc)).total_seconds())) if retry else 0
            except (ValueError,TypeError,OverflowError):pass
            if wait and self.control.site_cooldown:wait=self.control.site_cooldown.busy(wait)
            self.busy_until=max(self.busy_until,asyncio.get_running_loop().time()+wait)
            login_failure=auth_response or shop_signin_url(response.url)
            self.progress('http_status',{'status':503,'resource_type':response.request.resource_type,'blocks_checkout':False,'wait_seconds':wait,
                'context':'login' if login_failure else 'shop','affects':'login_service' if login_failure else 'store_busy'})
            if login_failure:
                self.auth_service_status=503
                self.progress('login',{'authenticated':None,'status':'service_unavailable','checked_at':datetime.now(timezone.utc).isoformat(),
                    'reason':'Apple 认证请求返回 HTTP 503；这是服务请求失败，不能据此判断密码错误。保留会话，不自动重发密码'})
            return
        if response.status in (403,429,541):
            resource_type=response.request.resource_type
            path=urlsplit(response.url).path
            purchase_request='/shop/' in path
            context='login' if auth_response or shop_signin_url(response.url) else 'account' if account_page_url(response.url) else 'shop'
            blocking=response.status in (429,503) or resource_type in {'document','script','stylesheet'} or (resource_type in {'xhr','fetch'} and (purchase_request or auth_response))
            # This public product-page GET supplies a delivery estimate. The
            # observed pickup modal makes its own independent request. A failed
            # delivery estimate must not masquerade as failed pickup inventory.
            # Explicit server waits, login, stock, and any bag/checkout mutation
            # remain blocking. This does not retry or alter the rejected request.
            delivery_preview=(path=='/shop/fulfillment-messages' and response.status in (403,541)
                              and resource_type in {'xhr','fetch'} and getattr(response.request,'method','')=='GET'
                              and not self.add_attempted and not self.cart_checked
                              and not response.headers.get('retry-after'))
            if delivery_preview:
                blocking=False
                self.delivery_preview_failed=True
            wait=5 if blocking else 0
            retry=response.headers.get('retry-after','')
            if retry:
                try:
                    wait=max(wait,int(retry)) if retry.isdigit() else max(wait,int((parsedate_to_datetime(retry)-datetime.now(timezone.utc)).total_seconds()))
                except (ValueError,TypeError,OverflowError):pass
            if blocking and self.control.site_cooldown:wait=self.control.site_cooldown.block(wait,episode=self.response_episode)
            self.progress('http_status',{'status':response.status,'resource_type':resource_type,'blocks_checkout':blocking,'wait_seconds':wait if blocking else 0,'context':context,
                                        'affects':'delivery_preview' if delivery_preview else context})
            if blocking:
                self.http_status=response.status
                self.opening_retry=(resource_type=='document' and getattr(response.request,'method','')=='GET'
                                    and path.startswith('/shop/buy-iphone') and context=='shop'
                                    and not self.add_attempted and not self.cart_checked and not self.submit_attempted)
                self.limited_until=max(self.limited_until,asyncio.get_running_loop().time()+wait)
                if context in ('login','account'):
                    self.progress('login',{'authenticated':None,'checked_at':datetime.now(timezone.utc).isoformat(),
                                          'reason':f'官网登录请求返回 HTTP {response.status}，暂时无法确认；现有会话保留'})

    def origin(self,page=None):
        if not apple_url((page or self.page).url):
            raise UserAction("浏览器已离开 Apple 中国大陆官网；已停止自动填写")

    async def guard(self,page=None,*,allow_uncommitted=False,allow_busy=False,text=None):
        self.control.check()
        if getattr(self,'auth_service_status',None):
            raise UserAction('Apple 认证请求返回 HTTP 503，暂时无法验证身份；保留当前页面，不自动重发密码')
        page=page or self.page
        browser_error=page.url=='chrome-error://chromewebdata/' and self.browse_connection_failed
        uncommitted=allow_uncommitted and (page.url=='about:blank' or browser_error) and apple_url(self.target_url)
        if not uncommitted:self.origin(page)
        # A visible challenge/refusal still requires user action. Never use a
        # transient HTTP retry to skip it or to resubmit login/checkout forms.
        if text is None:text = await page.locator("body").inner_text()
        if re.search(r"captcha|验证码.{0,8}(图片|字符)|verify you are human|access denied|访问被拒绝",text,re.I):
            raise UserAction("官网要求安全验证，请在浏览器手动处理后继续")
        if self.http_status in (403,429,541,503):
            if asyncio.get_running_loop().time() < self.limited_until:
                remaining=int(self.limited_until-asyncio.get_running_loop().time())+1
                raise RateLimited(f"官网关键请求返回 HTTP {self.http_status}；至少等待 {remaining} 秒，处理官网提示后点“继续检查”",
                                  retry_after=remaining,retry_opening=self.opening_retry)
            self.http_status = 200
            self.opening_retry = False
        if not allow_busy and asyncio.get_running_loop().time()<self.busy_until:
            raise UserAction(f'官网暂时繁忙，请等待 {int(self.busy_until-asyncio.get_running_loop().time())+1} 秒后继续；已有操作不重复提交')
        await self.handle_consent(page,text)


    async def prepare(self,config):
        config.validate(self.mode)
        self.privacy_config=config
        self.discovery_product=config.product
        self.waiting_for_link=not config.product.url and config.product.discover_from_store
        self.target_url=config.product.url or 'https://www.apple.com.cn/shop/buy-iphone'
        self.navigation_started=self.refresh_started_at=asyncio.get_running_loop().time()
        self.dom_ready=False
        # A stalled deferred resource must not consume the browser's 30s default
        # on every public-store opening check. Checkout navigation is unchanged.
        self.browse_timeout_ms=max(1,min(8000,int(config.operation_timeout*1000)))
        try:
            response=await self.page.goto(self.target_url,wait_until="domcontentloaded",timeout=self.browse_timeout_ms)
            family=family_url(self.target_url)
            if response and response.status==404 and family and self.target_url.rstrip('/')!=family:
                # A removed public SKU is recoverable through its published
                # family page. Explicit traffic restrictions are never retried here.
                self.target_url=family;config.product.url=family
                await self.page.goto(family,wait_until='domcontentloaded',timeout=self.browse_timeout_ms)
                self.progress('product_link',{'name':config.product.name,'url':family})
        except PlaywrightError as exc:
            self.browse_unavailable=True
            self.browse_connection_failed=bool(re.search(r'net::ERR_(CONNECTION_|NAME_NOT_RESOLVED|TIMED_OUT|INTERNET_DISCONNECTED)',str(exc)))
            return
        self.browse_unavailable=False
        self.browse_connection_failed=False
        await self.guard(allow_busy=True)
        preparation=self.page.get_by_role('link',name=re.compile(r'提前.{0,8}(准备|预购)|保存购物袋|做好准备')).filter(visible=True)
        labels=await preparation.all_text_contents()
        self.progress('preorder_preparation',{'visible_entries':[s.strip() for s in labels if s.strip()]})


    async def new_page(self):
        return await (self.page_factory() if self.page_factory else self.page.context.new_page())

    async def is_open(self):
        # Rejections may arrive while a navigation is timing out. Check them
        # before treating the timeout as permission for another refresh.
        await self.guard(allow_uncommitted=self.browse_unavailable,allow_busy=True)
        self.navigation_pending=False
        # An error page can contain an H1. Expiry of its retry delay does not
        # turn that failed document into a successful storefront response.
        if self.last_document_status>=400:return False
        if apple_url(self.page.url):
            ready=await self.page.evaluate('document.readyState')
            self.navigation_pending=(ready=='loading' or self.dom_ready is False) and asyncio.get_running_loop().time()-self.navigation_started<20
            current=urlsplit(self.page.url).path;target=urlsplit(self.target_url).path
            matches_target=not self.target_url or current==target or current.startswith(target.rstrip('/')+'/')
            if matches_target and self.dom_ready is not False and ready in ('interactive','complete') and await self.page.get_by_role('heading',level=1).count():self.browse_unavailable=False
        if self.navigation_pending:return False
        if asyncio.get_running_loop().time()<self.busy_until:return False
        if self.browse_unavailable:
            return False
        if self.waiting_for_link:
            link=exact_product_link(await public_links(self.page),self.discovery_product.name)
            if not link:return False
            self.discovery_product.url=link;self.target_url=link;self.waiting_for_link=False
            await self.page.goto(link,wait_until='domcontentloaded',timeout=self.browse_timeout_ms)
            await self.guard()
            self.progress('product_link',{'name':self.discovery_product.name,'url':link})
        body = await self.page.locator("body").inner_text()
        self.in_queue=bool(re.search(r'正在排队|排队中|等待室|当前排队|不要刷新|please do not refresh|you are now in line|your place in line|virtual queue',body[:4000],re.I))
        if self.in_queue:return False
        if re.search(r"马上回来|稍后回来|We.?ll be back|商店.{0,8}(维护|暂时关闭)",body,re.I):
            return False
        # A published product page with a future preorder notice is not an open checkout.
        if re.search(r"接受预购|开始预购",body[:3000]):
            # A published preorder date is independent of unselected form fields.
            notice=preorder_time(body[:3000])
            if notice and notice>datetime.now(ZoneInfo('Asia/Shanghai')):return False
            add=self.page.get_by_role("button",name="添加到购物袋",exact=True)
            next_step=self.page.get_by_role('button',name='继续',exact=True).filter(visible=True)
            if not notice and (not await add.count() or not await add.is_enabled()) and (await next_step.count()!=1 or not await next_step.is_enabled()):
                return False
        return await self.page.get_by_role("heading",level=1).count() > 0

    async def reload(self):
        await self.guard(allow_uncommitted=self.browse_unavailable,allow_busy=True)
        if self.in_queue or self.navigation_pending or asyncio.get_running_loop().time()<self.busy_until:return
        self.navigation_started=self.refresh_started_at=asyncio.get_running_loop().time()
        self.dom_ready=False
        try:
            if apple_url(self.page.url):
                await self.page.reload(wait_until="domcontentloaded",timeout=getattr(self,'browse_timeout_ms',8000))
            else:
                await self.page.goto(self.target_url,wait_until='domcontentloaded',timeout=getattr(self,'browse_timeout_ms',8000))
            self.browse_unavailable=False
            self.browse_connection_failed=False
        except PlaywrightError as exc:
            self.browse_unavailable=True
            self.browse_connection_failed=bool(re.search(r'net::ERR_(CONNECTION_|NAME_NOT_RESOLVED|TIMED_OUT|INTERNET_DISCONNECTED)',str(exc)))

    async def extract_payment_qr(self,store:PaymentStore,*,review=None,submitted_at=None):
        self.origin()
        if urlsplit(self.page.url).path!='/shop/checkout/thankyou':
            raise UserAction('请先在官网完成单次下单，进入显示付款二维码的订单确认页')
        await self.guard()
        heading=self.page.get_by_role('heading',level=1)
        if await heading.count()!=1 or not re.search(r'订单.*(?:等待付款|尚未付款|未付款|待付款)',await heading.inner_text()):
            raise UserAction('官网还没有显示待付款订单；不从加载页或其他页面提取付款码')
        order=self.page.get_by_role('link',name=re.compile(r'^订单\s*#[A-Z]\d{8,16}$'))
        if await order.count()!=1:
            raise UserAction('订单号无法唯一确认，付款码未提取')
        order_id=re.search(r'[A-Z]\d{8,16}',await order.inner_text())[0]
        # Take only the rendered code image, never a whole checkout screenshot or
        # a decoded payment token. Hidden/other-order codes are deliberately excluded.
        qr=self.page.get_by_role('img',name=re.compile(r'二维码|QR\s*code',re.I)).filter(visible=True)
        if await qr.count()!=1:
            raise UserAction('未找到唯一可见的官网付款二维码，请在浏览器查看付款指引')
        bounds=await qr.bounding_box()
        if not bounds or min(bounds['width'],bounds['height'])<64 or max(bounds['width'],bounds['height'])>1024:
            raise UserAction('付款二维码显示尺寸异常，请人工核对')
        body=await self.page.locator('body').inner_text()
        window=re.search(r'请在\s*(\d+)\s*分钟内完成付款',body)
        app=re.search(r'请使用[“"]?([^”"\n]{1,24})[”"]?\s*App\s*扫码支付',body)
        alt=await qr.get_attribute('alt') or ''
        details=review or {}
        payment=details.get('payment','') or ('招商银行' if alt=='CMB 二维码' else '')
        image=await qr.screenshot(type='png')
        return store.save(image,order_id=order_id,payment=payment,product=details.get('product',''),
            total_cny=details.get('total',''),installment_months=details.get('installment_months',0),
            payment_window_minutes=int(window[1]) if window else None,submitted_at=submitted_at,
            payment_app=app[1].strip() if app else payment_app(payment))

    async def options(self):
        await self.guard()
        controls = await self.page.evaluate(CONTROL_SCRIPT)
        # Include custom radios but exclude checkout invoice/fulfillment choices.
        self.options_snapshot = [c for c in controls if c['name'] and (c['name'].startswith('dimension') or 'dimension' in c['name'].lower() or c['kind']=='select')]
        return self.options_snapshot


    async def _choice(self,key,value):
        # Values/names are matched against an observed list, never arbitrary CSS from users.
        choices = await self.options()
        normalized = lambda s: re.sub(r"\s+", "", str(s)).lower()
        candidates = [c for c in choices if c['name']==key and
                      (c['value']==value or normalized(c['label'])==normalized(value) or
                       (normalized(c['label']).startswith(normalized(value)) and re.search(r'\d+(GB|TB)',value,re.I)))]
        if len(candidates)!=1:
            raise UserAction("配置选项无法唯一定位；请点“读取官网选项”更新配置，或在浏览器选择后继续")
        c = candidates[0]
        if c['kind']=='select':
            # Native select controls are addressed by the label discovered above.
            await self.page.get_by_label(c['label'],exact=True).select_option(value)
        else:
            if not c['label']:
                raise UserAction("官网商品选项缺少语义标签，请手动选择")
            # Use the native control observed on this page; its price-bearing
            # accessible label can change while dependent choices are enabled.
            loc=self.page.locator('input[id='+json.dumps(c['id'])+']') if c['id'] else self.page.get_by_role('radio',name=c['label'],exact=True)
            await self._check_radio(loc)

    async def _check_radio(self,loc: Locator):
        await ensure_checked(loc, focus=self.focus_control)

    async def focus_control(self,page):
        if self.page_focus:
            self.progress('browser_focus',True)
            await self.page_focus(page)

    async def select_product(self,config):
        await self.guard()
        if self.cart_checked:
            return
        await self.focus_control(self.page)
        # Product option order is saved in insertion order to preserve dependent selections.
        choices=await self.options() if config.product.options else []
        for key,value in config.product.options.items():
            matched=[c for c in choices if c['name']==key and c['value']==value]
            if len(matched)==1 and matched[0]['kind']=='radio' and matched[0]['checked']:
                continue
            await self._choice(key,value)
            choices=await self.options()
        if not config.product.options:
            choices=await self.options()
            if choices:
                selected={c['name']:c['value'] for c in choices if c['checked']}
                required={c['name'] for c in choices}
                if set(selected)!=required:
                    self.progress('options',choices)
                    raise UserAction('请在官网选择机型、颜色和容量，再点击“继续检查”')
                config.product.options=selected
        add=self.page.get_by_role('button',name='添加到购物袋',exact=True)
        next_step=self.page.get_by_role('button',name='继续',exact=True).filter(visible=True)
        if not await add.count() and await next_step.count()==1 and '/shop/buy-iphone/' in urlsplit(self.page.url).path:
            await self._check_product_continue(next_step)
        # Dependent trade-in/AppleCare sections load after the capacity change.
        # Re-check only these explicit exclusions while waiting for an enabled bag button.
        deadline=asyncio.get_running_loop().time()+15
        add=self.page.get_by_role('button',name='添加到购物袋',exact=True)
        while True:
            for name,enabled in [("不折抵换购",config.product.exclude_trade_in),("不加 AppleCare+ 服务计划",config.product.exclude_applecare)]:
                loc=self.page.get_by_role('radio',name=name,exact=True)
                if enabled and await loc.count()==1 and await loc.is_visible():
                    await self._check_radio(loc)
            if await add.count() and await add.is_enabled():
                break
            if asyncio.get_running_loop().time()>=deadline:
                raise UserAction("商品仍有必选配置或尚未开售，请在浏览器核对；软件不会猜选")
            await self.control.sleep(.2)
        choices = await self.options()
        for key,value in config.product.options.items():
            selected=[c for c in choices if c['name']==key and (c['value']==value or c['label']==value) and (c['checked'] or c['kind']=='select')]
            if not selected:
                raise UserAction("官网未接受商品配置；请重新读取选项")
        # Reliable price will be re-read from the bag before checkout. Selected capacity
        # labels also provide an early bound when available.
        prices=[money(m.group(1)) for c in choices if c['checked'] for m in [re.search(r'RMB\s*([\d,]+(?:\.\d+)?)',c['label'])] if m]
        if prices:await self.accept_price(config,max(prices)*config.product.quantity)
        self.progress('options',choices)

    async def _check_product_continue(self,button):
        # Capacity selection can replace the generic model stage by the final
        # product page. Do not keep waiting on its now-removed Continue button.
        deadline=asyncio.get_running_loop().time()+15
        add=self.page.get_by_role('button',name='添加到购物袋',exact=True)
        while True:
            await self.guard()
            if await add.count():return
            if await button.count()==1 and await button.is_visible() and await button.is_enabled():
                await button.click()
                return
            if asyncio.get_running_loop().time()>=deadline:
                raise UserAction('官网商品配置尚未准备好，请核对当前页面后继续')
            await self.control.sleep(.2)

    async def accept_price(self,config,amount):
        amount=money(amount)
        if amount<=0:raise FlowError('官网金额无法可靠确认')
        if amount<=money(config.product.max_total):return
        if not config.product.price_from_website:raise FlowError('商品或订单金额超出授权')
        key=quote_key(config.product,amount)
        if not self.control.quote_approved(key):
            self.progress('price_confirmation',{'key':key,'product':config.product.name,'options':await self.options_for_bag(config),'quantity':config.product.quantity,'amount':str(amount)})
            raise UserAction(f'官网价格为 RMB {amount:,.2f}，请在软件点选是否接受这个价格后继续')
        config.product.max_total=str(amount)
        self.progress('price_confirmed',{'product':config.product.name,'quantity':config.product.quantity,'amount':str(amount)})

    async def availability(self,config):
        try:
            # One normal location query per selected city, not per shop and
            # never per nationwide directory entry. Stop at the first city
            # with an acceptable selection to keep the priority path short.
            cities={}
            for store in config.stores:
                if store.enabled:
                    enriched=self.store_directory.enrich(store)
                    cities.setdefault((enriched.province,enriched.city),[]).append(enriched)
            result=[];groups=list(cities.values());preferred=[self.store_directory.enrich(s) for s in config.stores if s.enabled]
            for city_index,stores in enumerate(groups):
                region_config=deepcopy(config);region_config.stores=stores
                states=await self._read_availability(region_config,publish=False)
                result.extend(a for a in states if a.store.store_id not in {s.store.store_id for s in result})
                acceptable=lambda a: a.available and (not (config.pickup_from or config.pickup_to) or bool(a.pickup_date)) and (not config.pickup_from or a.pickup_date>=config.pickup_from) and (not config.pickup_to or a.pickup_date<=config.pickup_to)
                selected_indices=[index for index,s in enumerate(preferred) if any(acceptable(a) and (s.store_id==a.store.store_id or (not s.store_id and s.name==a.store.name)) for a in result)]
                unqueried={(s.province,s.city) for group in groups[city_index+1:] for s in group}
                pending_indices=[index for index,s in enumerate(preferred) if (s.province,s.city) in unqueried]
                if selected_indices and (not pending_indices or min(selected_indices)<min(pending_indices)):break
            self.progress('stores',[{'name':a.store.name,'store_id':a.store.store_id,'city':a.store.city,'province':a.store.province,'district':a.store.district,'available':a.available,'quote':a.quote} for a in result])
            return result
        except PlaywrightError:
            # A rejection can arrive while the modal is still opening. Preserve
            # its actual rate-limit reason instead of reporting a generic timeout.
            await self.guard()
            raise UserAction('官网供货页面尚未加载完成，检测时间未更新；请查看浏览器提示后继续') from None

    async def _read_availability(self,config,*,publish=True):
        query_started=asyncio.get_running_loop().time()
        await self.guard()
        if self.cart_checked:
            raise UserAction('已进入购物袋或结账，先核对当前流程；不会把先前门店结果当作新的库存查询')
        dialog=self.page.get_by_role('dialog').filter(visible=True)
        if not await dialog.count():
            # Apple replaces the generic query label with the remembered store name.
            # Both the sticky header and summary can render the same query action.
            # The remembered store action lives in the product summary.
            # Never treat global "Apple Watch 目录" navigation as pickup.
            main=self.page.get_by_role('main')
            entries=self.page.get_by_role('button',name=re.compile(r'^查看供货情况')).or_(
                main.get_by_role('button',name=re.compile(r'^Apple\s+\S')).filter(has_not_text=re.compile(r'目录|菜单'))
            ).filter(visible=True)
            try:
                # The bag can become enabled before this asynchronous widget.
                # Observe normal rendering; absence is not proof of no stock.
                await entries.first.wait_for(timeout=8000)
            except PlaywrightTimeout:
                await self.guard()
            enabled=[entry for entry in await entries.all() if await entry.is_enabled()]
            labels={re.sub(r'\s+',' ',await entry.inner_text()).strip() for entry in enabled}
            if not enabled or len(labels)!=1:
                if self.delivery_preview_failed:
                    raise UserAction('官网配送查询未成功，商品页未显示可确认的自提入口；这不是无货结果，库存检测时间未更新')
                raise UserAction('官网供货查询入口无法唯一确认，请在商品页打开供货情况后继续')
            await enabled[-1].click()
            await self.page.get_by_role('dialog').filter(visible=True).wait_for()
            dialog=self.page.get_by_role('dialog').filter(visible=True)
        if await dialog.count()!=1:
            raise UserAction('当前弹窗不是可确认的供货查询，请在官网处理后继续')
        # The real modal first displays a skeleton/spinner with no heading.
        # Its presence alone is not readiness, and not an unknown-popup error.
        heading=dialog.get_by_role('heading',name=re.compile('供货情况'))
        try:
            await heading.first.wait_for(timeout=15000)
        except PlaywrightTimeout:
            await self.guard()
            raise UserAction('供货弹窗仍未显示查询内容，库存检测时间未更新；请查看官网提示后继续') from None
        if await heading.count()!=1:
            raise UserAction('当前弹窗的供货标题无法唯一确认，请在官网处理后继续')
        await self.guard()
        await self._refresh_stock_region(dialog,config)
        await dialog.locator('input[name^="store-"]').first.wait_for(timeout=15000)
        await self.guard()
        controls=await dialog.locator('input[name^="store-"]').evaluate_all('''els => els.map(e=>({id:e.value,label:Array.from(e.labels||[]).map(l=>l.innerText).join(' '),disabled:e.disabled}))''')
        result=[]
        for c in controls:
            lines=[l.strip() for l in c['label'].split('\n') if l.strip()]
            if not lines: continue
            quote=' '.join(lines[1:])
            # Presence of an enabled store radio is only an available selection, not a reservation.
            available=not c['disabled'] and bool(re.search(r'可取货|可以取货',quote))
            when=pickup_date(quote)
            store=self.store_directory.enrich(Store(name=lines[0],city=lines[1].split(',')[0] if len(lines)>1 else '',store_id=c['id']))
            result.append(Availability(store,available,quote,when))
        self.progress('stock_query',{'completed_at':datetime.now(timezone.utc).isoformat(),
            'elapsed':round(asyncio.get_running_loop().time()-query_started,2),'store_count':len(result)})
        if publish:self.progress('stores',[{'name':a.store.name,'store_id':a.store.store_id,'city':a.store.city,'province':a.store.province,'district':a.store.district,'available':a.available,'quote':a.quote} for a in result])
        return result

    async def _refresh_stock_region(self,dialog,config):
        # The observed label is just "上海"; the explanatory sentence is outside
        # the button. Re-reading unchanged radios must never renew their timestamp.
        location=dialog.locator('button[aria-expanded]').filter(visible=True)
        if await location.count()!=1:
            raise UserAction('官网区域查询控件无法确认，库存检测时间未更新；请在浏览器核对')
        store=self.store_directory.enrich(next(s for s in config.stores if s.enabled))
        if not store.district:
            raise UserAction('当前官网区域查询需要明确区县，请完善首选门店的区县后再检查')
        try:
            if await location.get_attribute('aria-expanded')=='true':
                await location.click()
                await expect(location).to_have_attribute('aria-expanded','false')
            await location.click()
            # Reopening the actual picker remembers its last district panel.
            # Return through the visible province tab before selecting again.
            # Otherwise "上海" is a tab, not the expected province button.
            province_tab=dialog.get_by_role('tab',name=re.compile('省份')).filter(visible=True)
            if await province_tab.count()>1:
                raise UserAction('官网省份标签无法唯一确认，库存检测时间未更新')
            if await province_tab.count()==1 and await province_tab.get_attribute('aria-selected')!='true':
                await province_tab.click()
            # Scope every choice to its visible panel, avoiding the duplicated
            # "上海" region button beside the "上海" province option.
            panel=dialog.get_by_role('tabpanel').filter(visible=True)
            await panel.get_by_role('button',name=store.province or store.city,exact=True).click()
            if store.city != (store.province or store.city):
                await panel.get_by_role('button',name=store.city,exact=True).click()
            # Observe a normal browser response initiated by the region action.
            # No private endpoint, payload, session token or challenge is replayed.
            async with self.page.expect_request_finished(lambda request: apple_url(request.url)
                    and '/shop/' in urlsplit(request.url).path
                    and urlsplit(request.url).path!='/shop/fulfillment-messages'
                    and request.resource_type in {'xhr','fetch'},timeout=15000) as pending:
                await panel.get_by_role('button',name=store.district,exact=True).click()
            request=await pending.value
            response=await request.response()
            await self.guard()
            if not response or not 200<=response.status<300:
                raise UserAction('官网区域查询未成功，未把原有结果记作新库存；请查看浏览器提示')
            await expect(location).to_have_attribute('aria-expanded','false')
            self.stock_region=(store.province,store.city)
        except PlaywrightError:
            await self.guard()
            raise UserAction('官网尚未返回可确认的新供货结果，检测时间未更新；请处理浏览器提示后继续') from None

    async def select_pickup(self,availability,config):
        await self.guard()
        if self.cart_checked:
            return
        selected_store=self.store_directory.enrich(availability.store)
        if self.stock_region and self.stock_region!=(selected_store.province,selected_store.city):
            selected_config=deepcopy(config);selected_config.stores=[selected_store]
            latest=await self._read_availability(selected_config,publish=False)
            current=next((a for a in latest if a.store.store_id==selected_store.store_id and a.available),None)
            if not current:raise UserAction('目标门店的最新供货已变化；请重新检查门店，不会按旧库存进入结账')
            if (config.pickup_from and (not current.pickup_date or current.pickup_date<config.pickup_from)) or (config.pickup_to and (not current.pickup_date or current.pickup_date>config.pickup_to)):
                raise UserAction('目标门店的最新取货日期不符合设置；请重新检查门店')
            availability=current
        dialog=self.page.get_by_role('dialog')
        if await dialog.is_visible():
            await self._check_radio(dialog.get_by_role('radio',name=re.compile(re.escape(availability.store.name))))
            await dialog.get_by_role('button',name='继续',exact=True).click()
        self.selected=availability
        if not self.add_attempted:
            if await self.resume_cart(config):return
            self.add_attempted=True
            try:
                await self.page.get_by_role('button',name='添加到购物袋',exact=True).click()
            except PlaywrightTimeout:
                raise UserAction("加购结果暂不明确，请在浏览器打开购物袋核对后继续；不会重复加购") from None
        # Some products show the accessory attachment page; only its bag button is allowed.
        if '/shop/bag' not in self.page.url:
            try:
                await self.page.get_by_role('button',name='查看购物袋',exact=True).wait_for(timeout=8000)
                await self.page.get_by_role('button',name='查看购物袋',exact=True).click()
            except PlaywrightTimeout:
                raise UserAction("官网加购后的页面已变化，请在浏览器打开购物袋后继续") from None
        await self.page.wait_for_url(re.compile(r'/shop/bag'),timeout=30000)
        await self.guard()
        await self.checkout_updates.ready(self.control,timeout=30,label='购物袋')
        if not await bag_has_items(self.page,self.control,self.guard,timeout=30,expect_items=True):
            raise UserAction('加购后购物袋仍未显示目标商品，页面已保留；不会重复加购')
        quantities=self.page.get_by_role('combobox',name='数量',exact=True)
        if await quantities.count()!=1:
            raise UserAction("购物袋商品数量无法唯一确认，已暂停")
        if await quantities.input_value()!=str(config.product.quantity):
            await quantities.select_option(str(config.product.quantity))
            await self.checkout_updates.ready(self.control,timeout=30,label='购物袋数量')
            await bag_has_items(self.page,self.control,self.guard,timeout=30,expect_items=True)
        await self.page.get_by_role('heading',level=1).wait_for()
        await self._validate_bag(config)
        self.cart_checked=True
        if self.public_screenshot:
            # Opt-in proof at the verified bag, before any contact/identity form.
            self.public_screenshot.parent.mkdir(parents=True,exist_ok=True)
            await self.page.screenshot(path=str(self.public_screenshot))
            protect_path(self.public_screenshot)

    async def resume_cart(self,config,*,timeout=15):
        """Use an exact existing bag in trials and purchases, without mutations."""
        if self.cart_checked:return True
        if self.add_attempted:return False
        bag=await self.new_page();bag.on('response',self._response)
        try:
            await self.focus_control(bag)
            await bag.goto('https://www.apple.com.cn/shop/bag',wait_until='domcontentloaded')
            if urlsplit(bag.url).path.rstrip('/')!='/shop/bag':
                await self.guard(bag)
                raise UserAction('官网购物袋跳转尚未完成，请核对当前页面；不会再次加购')
            if not await bag_has_items(bag,self.control,self.guard,timeout=timeout):return False
            await self._validate_bag(config,bag)
            # There is no stock requirement here: fulfillment will read the
            # current allowed stores from checkout, without re-adding the item.
            self.add_attempted=True
            await self.page.goto('https://www.apple.com.cn/shop/bag',wait_until='domcontentloaded')
            if not await bag_has_items(self.page,self.control,self.guard,timeout=timeout):
                raise UserAction('复核时购物袋已变化，页面保留；不会重新加购')
            await self._validate_bag(config)
            self.cart_checked=True
            if not self.selected:
                preferred=next((s for s in config.stores if s.enabled),None)
                if not preferred:raise UserAction('请先设置允许的取货门店；购物袋保留')
                self.selected=Availability(self.store_directory.enrich(preferred),False,'结账时核对当前取货选项')
            self.progress('bag_reused',{'message':'已复用核对一致的购物袋；直接在结账页检查取货门店，没有重新加购'})
            return True
        finally:
            bag.remove_listener('response',self._response)
            await self.page_closer(bag)

    async def _validate_bag(self,config,page=None):
        page=page or self.page
        title=await page.get_by_role('heading',level=1).inner_text()
        match=re.search(r'总计\s*RMB\s*([\d,]+(?:\.\d+)?)',title)
        if not match or money(match[1])<=0:
            raise FlowError("购物袋总金额超出授权或无法确认")
        headings=page.get_by_role('heading',level=2).filter(visible=True)
        texts=await headings.all_text_contents()
        normalized=lambda s:re.sub(r'\s+','',s).lower()
        model=normalized(config.product.name)
        def matches(s):
            value=normalized(s)
            suffix=value[len(model):]
            # iPhone 17e/Pro and Pro Max cannot match their shorter model names.
            return value.startswith(model) and not (suffix and suffix[0].isascii() and suffix[0].isalpha())
        matching=[s for s in texts if matches(s)]
        if len(matching)!=1 or await page.get_by_role('button',name=re.compile(r'^移除 ')).filter(visible=True).count()!=1:
            raise FlowError("购物袋含非目标商品或商品无法确认")
        quantities=page.get_by_role('combobox',name='数量',exact=True)
        if await quantities.count()!=1 or await quantities.input_value()!=str(config.product.quantity):
            raise FlowError('购物袋数量与购买设置不一致')
        # Semantic bag line must still contain the selected human-readable labels.
        for c in await self.options_for_bag(config):
            if normalized(c) not in normalized(matching[0]):
                raise FlowError("购物袋商品配置与选中配置不一致")
        await self.accept_price(config,money(match[1]))
        self.bag_total=money(match[1])
        self.progress('bag',{'product':matching[0],'quantity':config.product.quantity,'total':str(self.bag_total),'store':self.selected.store.name if self.selected else ''})

    async def options_for_bag(self,config):
        result=[]
        for key,value in config.product.options.items():
            c=next((c for c in self.options_snapshot if c['name']==key and (c['value']==value or c['label']==value)),None)
            if c:
                label=re.split(r'脚注|RMB|人民币',c['label'])[0].strip()
                if 'Color' in key or 'Capacity' in key or 'color' in key or 'capacity' in key:
                    result.append(label)
        return result

    async def fill_customer(self,identity,config):
        if not apple_auth_url(self.page.url):await self.guard()
        else:await self.login_guard(self.page)
        if await self.page.get_by_role('button',name='结账',exact=True).count():
            await self.checkout_updates.ready(self.control,timeout=30,label='购物袋')
            await bag_has_items(self.page,self.control,self.guard,timeout=30,expect_items=True)
            await self._validate_bag(config)
            await self.page.get_by_role('button',name='结账',exact=True).first.click()
            await self._wait_checkout_entry()
        current_stage=(await checkout_state(self.page))['stage']
        fulfillment_ready=current_stage in ('pickup','pickup_unavailable','contact','payment')
        if not fulfillment_ready and config.checkout_strategy!='guest' and config.auto_login:
            # Credentials take priority over the explicitly selected guest backup.
            # A rejected/uncertain attempt pauses with the bag intact.
            if await self.auto_sign_in(self.page,config,wait_for_form=current_stage=='guest'):
                # Login may return while the guest button remains behind an
                # overlay. Use the confirmed return, not the stale entry state.
                fulfillment_ready=True
        guest=self.page.get_by_role('button',name='以游客身份继续',exact=True)
        if not fulfillment_ready and await guest.is_visible():
            if config.checkout_strategy=='account_only':
                raise UserAction('官网要求重新登录；已设置“只用 Apple 账户”，请完成登录后继续。购物袋保留')
            if self.guest_attempted:
                raise UserAction('游客继续已尝试，官网尚未完成跳转；请查看当前页面，不会反复点击')
            self.guest_attempted=True
            await self.focus_control(self.page)
            await guest.click()
            self.progress('checkout_auth',{'path':'guest','strategy':config.checkout_strategy,
                'message':'官网要求登录，已按预选策略进入游客结账；若官网要求手机验证码则暂停'})
            # Apple opens consent over this page: the background guest button
            # stays visible. Observe the next stage instead of waiting for it
            # to disappear, so saved consent can run as soon as it appears.
        elif not fulfillment_ready and await visible_login_form(self.page):
            raise UserAction('官网需要登录且当前未显示游客入口；请完成登录，不会反复提交账户密码')
        stage=await self._wait_checkout_stage()
        while stage in ('pickup','pickup_unavailable'):
            await self._select_checkout_pickup(config)
            stage=await self._wait_checkout_stage(wanted=('contact','payment','pickup_unavailable'),timeout=max(45,config.operation_timeout))
        if stage in ('contact','payment') and self.checkout_last_continued:
            await self.checkout_updates.ready(self.control,timeout=min(30,config.operation_timeout))
            if self.selected and self.selected.store.store_id==self.checkout_last_continued:
                # A checked store plus its completed save and successful
                # continuation is checkout evidence, not an inventory quote.
                self.checkout_pickup_confirmed=(self.page,deepcopy(self.selected.store))
        if stage=='contact':
            await self.inspect_customer_fields()
            if self.identity_filled:
                await self.check_customer_verification()
                return
            if identity is None or not config.transmit_identity:
                raise UserAction("已到官网取货人表单：请在软件的“取货人”直接填写姓名、证件后四位、手机号和邮箱，或导入本机 TXT；也可在官网手动填写并完成手机验证后继续")
            identity.validate()
            surname=getattr(identity,'family_name','')
            given=getattr(identity,'given_name','')
            if not surname or not given:
                raise UserAction("官网姓名分为“姓氏”和“名字”，请在取货人设置中分别填写")
            fields={"姓氏":surname,"名字":given,"电子邮件地址":identity.email,"联系人手机号码":identity.phone.removeprefix('+86'),"政府颁发的身份证件号码的后四位":identity.document_number[-4:]}
            for label,value in fields.items():
                loc=self.page.get_by_role('textbox',name=label,exact=True)
                if await loc.count()!=1:
                    raise UserAction("官网取货表单已变化，请手动核对字段")
                await loc.fill(value)
                if await loc.input_value()!=value:
                    raise UserAction("官网未接受某个取货字段，请手动核对")
            self.identity_filled=True
            # Some guest flows require SMS; the logged-in flow observed on 2026-10-11
            # accepted these fields directly. Pause only for an actual visible verifier.
            await self.check_customer_verification()
            return
        # Manually completed contact can already have advanced to Payment.
        if stage!='payment':
            raise UserAction("当前结账阶段无法确认，请在浏览器处理登录或提示后继续")

    async def check_customer_verification(self):
        verify=self.page.get_by_role('button',name='验证',exact=True).filter(visible=True)
        codes=self.page.locator('input[autocomplete="one-time-code"]').filter(visible=True)
        if await verify.count() or await codes.count():
            self.progress('verification_required',{'kind':'phone','message':'官网要求手机验证码；已填资料并保留购物袋，请在官网完成验证后继续'})
            raise UserAction('已填写并读回取货人字段；官网要求手机验证，请自行点“验证”、输入验证码后点“继续检查”')

    async def _wait_checkout_entry(self,timeout=30):
        return await self._wait_checkout_stage(timeout=timeout,entry=True)

    async def _wait_checkout_stage(self, *, wanted=('pickup','pickup_unavailable','contact','payment'),timeout=30,entry=False):
        deadline=asyncio.get_running_loop().time()+timeout
        observed=tuple(wanted)+('consent',)+(('guest',) if entry or not self.guest_attempted else ())
        while asyncio.get_running_loop().time()<deadline:
            self.control.check()
            try:snapshot=await checkout_state(self.page,wait=500,wanted=observed)
            except PlaywrightError as exc:
                if self.page.is_closed():
                    raise UserAction('官网购买页面已关闭，请重新打开软件浏览器后检查；不会继续提交') from None
                if re.search(r'Execution context was destroyed|Cannot find context with specified id',str(exc)):
                    await self.control.sleep(.05);continue
                raise
            stage=snapshot['stage']
            if stage!=self.checkout_observed_stage:
                self.checkout_observed_stage=stage
                # Control names/store options only. Never emit body text or
                # contact input values from this in-memory snapshot.
                self.progress('checkout_page',{'stage':stage,'buttons':snapshot['diagnostic_buttons'],
                    'radios':snapshot['radios'] if stage in ('pickup','pickup_unavailable') else [],
                    'contact_fields':snapshot['contact_fields'],'continuation_buttons':snapshot['continuation_buttons']})
            if apple_auth_url(self.page.url):await self.login_guard(self.page)
            elif stage!='consent' or not entry:await self.guard(text=snapshot['text'])
            if entry and stage in ('consent','guest'):return stage
            # Consent disappearing precedes the fulfillment form loading. Wait
            # for its controls, not merely the modal disappearing or URL change.
            if stage=='consent':continue
            if stage in wanted:return stage
            if stage=='guest' and self.guest_attempted:
                continue
            if await visible_login_form(self.page):
                if entry:
                    await self.control.sleep(.5);return 'login'
                raise UserAction('官网仍要求登录，请完成登录或检查预选结账策略；当前购物袋保留')
            await self.control.sleep(.05)
        self.progress('checkout_observation',{'stage':snapshot['stage'],
            'contact_fields':snapshot['contact_fields'],'continuation_buttons':snapshot['continuation_buttons'],
            **({'buttons':snapshot['diagnostic_buttons'],
                'radios':[r for r in snapshot['radios'] if re.fullmatch(r'R\d{3,6}',r['value'])
                          or re.match(r'^(为我送货|我要取货|取货日期|取货时间)',r['label'])]} if snapshot['stage'] in ('pickup','pickup_unavailable') else {})})
        raise UserAction('官网结账尚未完成加载，当前购物袋保留，请查看页面后继续')

    async def inspect_customer_fields(self):
        labels=await self.page.get_by_role('textbox').evaluate_all('els=>els.map(e=>e.getAttribute("aria-label") || Array.from(e.labels||[]).map(l=>l.innerText).join(" "))')
        selects=await self.page.locator('select').evaluate_all('els=>els.filter(e=>/证件/.test(Array.from(e.labels||[]).map(l=>l.innerText).join(" "))).flatMap(e=>Array.from(e.options).map(o=>o.textContent.trim()))')
        self.progress('customer_fields',{'labels':labels,'document_types':selects,'only_last_four':any('后四位' in l for l in labels)})

    async def select_payment(self,config):
        await self.guard()
        await self.focus_control(self.page)
        stage=(await checkout_state(self.page))['stage']
        if stage=='contact':
            await self.check_customer_verification()
            contact=self.page.get_by_role('button',name=re.compile(r'^继续选择付款方式(?:\s|$)')).filter(visible=True)
            if not await contact.count():
                contact=self.page.get_by_role('button',name='继续',exact=True).filter(visible=True)
            if await contact.count()!=1:
                raise UserAction('取货资料的继续按钮无法唯一确认，请在官网核对后继续')
            await contact.click()
            try:
                await self._wait_checkout_stage(wanted=('payment',),timeout=15)
            except UserAction as exc:
                if not str(exc).startswith('官网结账尚未完成加载'):raise
                raise UserAction("官网尚未接受取货信息或手机验证，请在浏览器查看错误后继续") from None
        # Runtime discovery: unavailable payment methods are never assumed.
        await self.page.get_by_role('heading',name='你希望如何付款？',exact=True).wait_for()
        controls=await self.page.evaluate(CONTROL_SCRIPT)
        choices=[c for c in controls if c['kind']=='radio' and c['visible'] and any(payment_matches(p,c['label']) for p in config.payments)]
        self.progress('payments',[c['label'] for c in choices])
        for preferred in config.payments:
            candidates=[c for c in choices if payment_matches(preferred,c['label']) and not c['disabled']]
            if len(candidates)==1:
                await self._check_radio(self.page.get_by_role('radio',name=candidates[0]['label'],exact=True).filter(visible=True))
                self.payment=preferred
                self.review_advance_attempted=False
                selected=candidates[0]
                await self.checkout_updates.ready(self.control,timeout=min(30,config.operation_timeout),label='付款方式')
                months=payment_months(preferred,config.installment_months)
                self.installment_months=0
                if months:
                    # Only activate a term rendered for the selected scheme.
                    pattern=re.compile(r'^'+str(months)+r'\s*期(?:\s|0)')
                    term=self.page.get_by_role('radio',name=pattern).filter(visible=True)
                    if await term.count()>1:
                        raise UserAction('当前分期期数无法唯一确认，请在官网核对后继续')
                    try:
                        await term.wait_for(timeout=10000)
                    except PlaywrightTimeout:
                        raise UserAction("当前付款方式没有要求的分期方案；请在官网核对后继续") from None
                    if await term.count()!=1:
                        raise UserAction('当前分期期数无法唯一确认，请在官网核对后继续')
                    if not await term.is_enabled():
                        raise UserAction('当前要求的分期期数不可用，请在官网核对后继续')
                    await self._check_radio(term)
                    self.installment_months=months
                    terms=await self.page.evaluate(CONTROL_SCRIPT)
                    self.progress('installments',[c['label'] for c in terms if c['kind']=='radio' and c['visible'] and re.match(r'^\d+\s*期',c['label'])])
                elif str(selected['value']).startswith('installments'):
                    raise UserAction("已选择分期付款方式，请先设置明确的分期期数")
                return preferred
        raise UserAction("当前页面未找到允许的可用付款方式，请手动核对或调整备用顺序")

    async def review(self,config):
        await self.guard()
        await self.focus_control(self.page)
        await self.checkout_updates.ready(self.control,timeout=min(30,config.operation_timeout),label='付款方案')
        # “检查订单” was observed on the real Billing page; “立即下单” is never clicked.
        advance=self.page.get_by_role('button',name='检查订单',exact=True)
        if not self.review_advance_attempted and await advance.is_visible():
            self.review_advance_attempted=True
            await advance.click()
        try:
            # Observe DOM readiness, without adding a fixed delay or replaying
            # the transition when the website finishes after the old 15s cap.
            await self._wait_checkout_stage(wanted=('review',),timeout=max(45,config.operation_timeout))
        except UserAction as exc:
            if str(exc).startswith('官网结账尚未完成加载'):
                raise UserAction('官网订单核对页尚未完成加载；当前购物袋保留，不重复点击检查订单') from None
            raise
        await self.guard()
        submit=self.page.get_by_role('button',name='立即下单',exact=True).filter(visible=True)
        if await submit.count()!=1 or not await submit.is_enabled():
            raise UserAction('官网下单按钮尚不可用；请在页面完成必要信息或验证，当前购物袋保留')
        items=self.page.locator('li[data-autom^="bag-item-"]')
        if await items.count()!=1:
            raise UserAction('最终核对页商品数量无法可靠解析，请人工核对')
        item=await items.inner_text()
        title=await items.get_by_role('heading',level=2).inner_text()
        normalized=lambda s:re.sub(r'\s+','',s).lower()
        if normalized(config.product.name) not in normalized(title):
            raise FlowError('最终核对页商品与授权不一致')
        for label in await self.options_for_bag(config):
            if normalized(label) not in normalized(title):
                raise FlowError('最终核对页颜色或容量与授权不一致')
        q=re.search(r'数量\s*(\d+)',item)
        total=await self.page.locator('[data-autom="bagtotalvalue"]').inner_text()
        amount=re.fullmatch(r'\s*RMB\s*([\d,]+(?:\.\d+)?)\s*',total)
        if not q or int(q[1])!=config.product.quantity or not amount:
            raise FlowError('最终核对页数量或总金额无法确认')
        value=money(amount[1])
        await self.accept_price(config,value)
        images=await self.page.get_by_role('img').filter(visible=True).evaluate_all('els=>els.map(e=>e.getAttribute("alt")||e.getAttribute("aria-label")||"")')
        if sum(payment_matches(self.payment,name) for name in images)!=1:
            raise UserAction('最终核对页付款方式无法确认，请人工核对')
        body=await self.page.locator('body').inner_text()
        months=re.search(r'(\d+)\s*个月[，,]\s*每月约',body)
        actual_months=int(months[1]) if months else 0
        if actual_months!=payment_months(self.payment,config.installment_months):
            raise FlowError('最终核对页分期期数与授权不一致')
        store=await self.review_pickup_store(body)
        store_visible=store.name in body
        self.progress('review',{'product':title,'quantity':int(q[1]),'total':str(value),
            'payment':self.payment,'installment_months':actual_months,
            'store_last_selected':store.name,'store_visible_in_review':store_visible,
            'store_confirmation':'checkout_saved',
            'submit_clicked':False})
        if config.pickup_from or config.pickup_to:
            raise UserAction('官网最终取货日期尚不能验证是否满足设置的日期范围；当前购物袋保留，未提交订单')
        return Summary(config.product.name,dict(config.product.options),int(q[1]),value,
            store.name,store.store_id,self.payment,
            verified=True,installment_months=actual_months)

    async def submit(self,config):
        if self.mode!=Mode.LIVE or not config.auto_submit:
            raise UserAction('此运行未获真实下单授权，不会点击立即下单')
        if self.submit_attempted:
            raise SubmissionUnknown('已尝试一次提交；只允许核对订单，禁止再次点击')
        summary=await self.review(config)
        summary.authorize(config)
        self.control.check()
        self.submit_attempted=True
        self.submit_started=datetime.now(timezone.utc)
        try:
            await self.page.get_by_role('button',name='立即下单',exact=True).click(timeout=15000)
        except PlaywrightError:
            raise SubmissionUnknown('提交点击的结果不明，订单锁保留') from None
        deadline=asyncio.get_running_loop().time()+45
        while asyncio.get_running_loop().time()<deadline:
            receipt=await self._current_receipt()
            if receipt:
                return receipt
            # Do not cancel/retry the submit on Stop. The engine retains the lock.
            await asyncio.sleep(.5)
        raise SubmissionUnknown('官网尚未返回可核对的订单号，进入订单核对')

    async def _current_receipt(self):
        # A wallet may redirect to its provider. That is payment handoff, not
        # Apple order confirmation; keep the once-only lock until confirmed.
        if not apple_url(self.page.url) or urlsplit(self.page.url).path!='/shop/checkout/thankyou':
            return None
        order=self.page.get_by_role('link',name=re.compile(r'^订单\s*#[A-Z]\d{8,16}$'))
        heading=self.page.get_by_role('heading',level=1)
        if await order.count()!=1 or await heading.count()!=1:
            return None
        title=await heading.inner_text()
        if re.search(r'失败|已取消|已关闭|无法|未能',title):return None
        pending=bool(re.search(r'订单.*(?:等待付款|尚未付款|未付款|待付款)',title))
        if not pending and not re.search(r'(?:订单.*(?:已收到|已提交|已确认|正在处理)|(?:感谢|谢谢).*?(?:订单|购买))',title):return None
        order_id=re.search(r'[A-Z]\d{8,16}',await order.inner_text())[0]
        text=await self.page.locator('body').inner_text()
        window=re.search(r'请在\s*(\d+)\s*分钟内完成付款',text)
        deadline=(self.submit_started+timedelta(minutes=int(window[1]))).isoformat() if window and self.submit_started else None
        return Receipt(order_id,'payment_pending' if pending else 'confirmed',deadline if pending else None)

    async def reconcile(self,config):
        # No match by mere confirmation text: a normal Apple order page needs an
        # exact, verified correlation to this purchase intent. Do not invent one.
        if self.submit_attempted:
            return await self._current_receipt()
        raise UserAction("重新启动后需通过 Apple 正常订单状态页面人工确认旧订单；软件不会重复提交")
