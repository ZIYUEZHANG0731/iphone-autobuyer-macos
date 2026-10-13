"""A real Qt host, a worker bridge, CDP detach, profile reopen and normal exit.

All pages, including an Apple-shaped privacy fixture, are fulfilled in memory.
This tests the browser mechanism, not Apple authentication or sale-day speed.
"""
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


@pytest.mark.e2e
@pytest.mark.parametrize(('method','months'),[
    ('招商银行',24),('支付宝',24),('微信支付',24),('中国建设银行',6),
])
def test_embedded_browser_reuses_tabs_persists_session_cookies_and_quits_cleanly(tmp_path,method,months):
    script=textwrap.dedent('''
        import asyncio
        import sys
        import threading
        from pathlib import Path
        from PySide6.QtCore import QThread, Qt, Signal
        from PySide6.QtWidgets import QApplication
        from shiboken6 import delete, isValid
        from app.automation.browser import BrowserSession
        from app.automation.control import Control
        from app.adapters.apple import AppleAdapter
        from app.models.config import Settings, Store, Identity, Mode
        from app.models.flow import Availability
        from app.models.credential import AppleCredential
        from app.services.payment_methods import payment_months
        from app.services.consent import NOTICE_TITLE, NOTICE_INTRO, NOTICE_CHOICES, NOTICE_MORE, NOTICE_VERSION
        from app.gui.window import MainWindow
        from app.utils.qt import prepare_plugins, single_shot

        prepare_plugins()
        qt=QApplication(['embedded-regression'])
        window=MainWindow(Path(sys.argv[1]))
        window.show()
        info=window.ensure_embedded()
        assert window.embedded_browser.profile.httpAcceptLanguage()=='zh-CN,zh;q=0.9'
        assert not window.embedded_browser.profile.isOffTheRecord()
        window.stack.setCurrentIndex(6)
        phase=sys.argv[2]
        method=sys.argv[3];months=int(sys.argv[4]);actual_months=payment_months(method,months)

        class Probe(QThread):
            data=Signal(str,object)
            hide_browser=Signal()
            def __init__(self):
                super().__init__(window)
                self.control=Control(False)
                self.idle=False
                self.session_open=True
                self.error=None
                self.embedded=info
                self.browser_hidden=threading.Event()
            async def check_browser(self):
                async with BrowserSession(Path(sys.argv[1]),embedded=info) as session:
                    async def fixture(route):
                        await route.fulfill(content_type='text/html',body="""
                            <h1>Offline embedded fixture</h1><button onclick=
                            "document.cookie='fixture_session=synthetic;path=/;secure';
                            document.cookie='fixture_persistent=synthetic;max-age=3600;path=/;secure'">Save fixture</button>""")
                    await session.context.route('**/*',fixture)
                    await session.page.goto('https://autobuyer.invalid/embedded')
                    assert await session.page.get_by_role('heading',name='Offline embedded fixture').count()==1
                    assert await session.page.evaluate('navigator.languages')==['zh-CN','zh']
                    if phase=='save':await session.page.get_by_role('button',name='Save fixture').click()
                    assert await session.page.evaluate("document.cookie.includes('fixture_session=synthetic') && document.cookie.includes('fixture_persistent=synthetic')")
                    background=await session.new_page()
                    await background.goto('https://autobuyer.invalid/background')
                    assert await background.evaluate('navigator.languages')==['zh-CN','zh']
                    assert background is not session.page and not session.page.is_closed()
                    await session.close_page(background)
                    await asyncio.sleep(.1)
                    assert background.is_closed() and not session.page.is_closed()
                    # Exercise the real Qt/CDP input path with the whole window
                    # hidden, styled native inputs and a disabled button.
                    window_focus=lambda page:session.focus_page(page)
                    adapter=AppleAdapter(session.page,self.control,progress=self.data.emit,page_focus=window_focus)
                    adapter.privacy_config=Settings(privacy_policy_accepted=True,privacy_transfer_acknowledged=True,
                        privacy_notice_version=NOTICE_VERSION)
                    await session.page.goto('https://secure.www.apple.com.cn/shop/checkout')
                    notice='<h1>'+NOTICE_TITLE+'</h1>'+''.join('<p>'+text+'</p>' for text in NOTICE_INTRO)
                    for index,text in enumerate(NOTICE_CHOICES):
                        notice+=f'<div><input id="consent{index}" type="checkbox" style="position:absolute;opacity:0;pointer-events:none"><label for="consent{index}">{text}</label></div>'
                    notice+='<p>'+NOTICE_MORE+'</p><button aria-label="关闭">×</button><button id="accept" disabled onclick="window.accepts++;document.body.innerHTML=&quot;<h1>Privacy confirmed</h1>&quot;">同意并继续</button>'
                    notice+='<script>window.accepts=0;document.addEventListener("change",()=>document.querySelector("#accept").disabled=!Array.from(document.querySelectorAll("input")).every(e=>e.checked));document.addEventListener("keydown",e=>{if(e.code==="Space")e.preventDefault()});</script>'
                    await session.page.set_content(notice)
                    self.hide_browser.emit()
                    assert await asyncio.to_thread(self.browser_hidden.wait,3), 'Console did not hide web view'
                    await adapter.guard()
                    assert await session.page.get_by_role('heading',name='Privacy confirmed').count()==1
                    assert await session.page.evaluate('window.accepts')==1
                    # Activate the page's observed button after restoring the
                    # GUI-owned view, including a scrolled button.
                    contact='<h1>取货联系信息</h1>'+''.join('<label>'+label+'<input></label>' for label in
                        ('姓氏','名字','电子邮件地址','联系人手机号码','政府颁发的身份证件号码的后四位'))+'<button>继续选择付款方式</button>'
                    await session.page.set_content("""<h1>选择取货零售店</h1>
                        <label><input type="radio" name="store" value="R359">2 Apple 南京东路 今天可取货</label>
                        <button style="margin-top:1400px" onclick="window.continues++;document.body.innerHTML=window.contact">继续填写取货详情</button>
                        <script>window.continues=0</script>""")
                    await session.page.evaluate('html=>window.contact=html',contact)
                    adapter.selected=Availability(Store('Apple 南京东路',store_id='R359'),True,'今天可取货')
                    self.browser_hidden.clear();self.hide_browser.emit()
                    assert await asyncio.to_thread(self.browser_hidden.wait,3)
                    config=Settings(stores=[Store('Apple 南京东路',store_id='R359')],transmit_identity=True)
                    await adapter.fill_customer(Identity('格式测试','居民身份证后四位','123X','00000000000','format@example.com','格','式测试'),config)
                    assert adapter.identity_filled and await session.page.evaluate('window.continues')==1
                    # Real Qt submit input must work from a hidden window with
                    # a checkout-confirmed store omitted from the Review text.
                    receipts=[]
                    async def receipt_fixture(route):
                        receipts.append('synthetic-confirmation')
                        await route.fulfill(content_type='text/html; charset=utf-8',body="""<h1>你的订单正在等待付款</h1>
                          <p>请在 30 分钟内完成付款</p><a href="#fixture">订单 #W1234567890</a>""")
                    await session.context.route('**/shop/checkout/thankyou',receipt_fixture)
                    review="""<main><h1>准备下单了吗？</h1>
                      <h2>取货日期待付款完成后确定。</h2><a href="#pickup">更改零售店</a>
                      <li data-autom="bag-item-fixture"><h2>iPhone 17</h2><p>数量 1</p></li>
                      <p data-autom="bagtotalvalue">RMB 6,799</p><img alt="招商银行">
                      <p>24 个月，每月约 RMB 284</p>
                      <button onclick="location.href='/shop/checkout/thankyou'">立即下单</button></main>"""
                    review=review.replace('alt="招商银行"','alt="'+method+'"').replace('24 个月，每月约 RMB 284',str(actual_months)+' 个月，每月约 RMB 284' if actual_months else '')
                    await session.page.set_content(review)
                    config.product.name='iPhone 17';config.product.max_total='6799'
                    config.auto_submit=True;config.payments=[method];config.installment_months=months
                    adapter.mode=Mode.LIVE;adapter.payment=method;adapter.installment_months=actual_months
                    self.browser_hidden.clear();self.hide_browser.emit()
                    assert await asyncio.to_thread(self.browser_hidden.wait,3)
                    receipt=await adapter.submit(config)
                    assert receipt.order_id=='W1234567890' and receipt.status=='payment_pending'
                    assert (await adapter.reconcile(config)).order_id==receipt.order_id
                    assert receipts==['synthetic-confirmation']
                    await session.context.unroute('**/shop/checkout/thankyou',receipt_fixture)
                    # Account recovery in checkout must restore the hidden Qt
                    # view before submitting its normal iframe login button.
                    async def signin_fixture(route):
                        auth=route.request.url.startswith('https://idmsa.apple.com.cn/')
                        body="""<label>电子邮件或电话号码<input></label><label>密码<input type="password"></label>
                          <label><input type="checkbox">记住我的账户</label>
                          <button id="sign-in" onclick="parent.postMessage('fixture-authenticated','https://secure.www.apple.com.cn')">登录</button>""" if auth else """
                          <iframe style="height:500px;width:100%" src="https://idmsa.apple.com.cn/appleauth/auth/signin"></iframe>
                          <script>window.loginReturns=0;addEventListener('message',e=>{
                            if(e.origin==='https://idmsa.apple.com.cn'&&e.data==='fixture-authenticated'){
                              window.loginReturns++;document.body.innerHTML='<h1>取货联系信息</h1><button>继续选择付款方式</button>';
                            }})</script>"""
                        await route.fulfill(content_type='text/html; charset=utf-8',body=body)
                    await session.context.route('**/*',signin_fixture)
                    await session.page.goto('https://secure.www.apple.com.cn/shop/checkout?fixture-auth')
                    config=Settings(credential_id='fixture-only-reference',auto_login=True,trust_browser=True)
                    adapter.credential_provider=lambda _:AppleCredential('fixture@example.invalid','Synthetic-Only-Password!')
                    self.browser_hidden.clear();self.hide_browser.emit()
                    assert await asyncio.to_thread(self.browser_hidden.wait,3)
                    assert await adapter.auto_sign_in(session.page,config,wait_for_form=True)
                    assert await session.page.evaluate('window.loginReturns')==1
                    await session.context.unroute('**/*',signin_fixture)
                    adapter.detach()
                    await session.page.goto('https://autobuyer.invalid/embedded')
                    await session.focus_page()
                    await session.context.unroute('**/*',fixture)
                # Closing the automation connection retains the GUI-owned page.
                async with BrowserSession(Path(sys.argv[1]),embedded=info) as attached:
                    assert await attached.page.get_by_role('heading',name='Offline embedded fixture').count()==1
                    assert await attached.page.evaluate("document.cookie.includes('fixture_session=synthetic')")
            def run(self):
                try:asyncio.run(self.check_browser())
                except Exception as error:self.error=error
                finally:self.session_open=False

        probe=Probe();window.worker=probe
        probe.data.connect(window.on_data)
        def hide_browser():
            window.stack.setCurrentIndex(4)
            window.hide()
            probe.browser_hidden.set()
        probe.hide_browser.connect(hide_browser,Qt.ConnectionType.QueuedConnection)
        probe.finished.connect(qt.quit)
        qt.aboutToQuit.connect(window.shutdown)
        probe.start()
        single_shot(window,30000,lambda:qt.quit())
        try:
            qt.exec()
            assert not probe.isRunning(), 'Worker did not finish'
            if probe.error:raise probe.error
        finally:
            window.shutdown();delete(window)
        assert not isValid(window)
        print('EMBEDDED_PROFILE_OK:'+phase,flush=True)
    ''')
    env={**os.environ,'QT_QPA_PLATFORM':'offscreen','AUTOBUYER_DATA_DIR':str(tmp_path)}
    for phase in ('save','reopen'):
        result=subprocess.run([sys.executable,'-c',script,str(tmp_path),phase,method,str(months)],
                              cwd=Path(__file__).resolve().parents[1],env=env,
                              text=True,encoding='utf-8',capture_output=True,timeout=45)
        assert result.returncode==0,result.stdout+'\n'+result.stderr
        assert 'EMBEDDED_PROFILE_OK:'+phase in result.stdout
        assert 'QThread: Destroyed' not in result.stderr
    if os.name != 'nt':
        assert (tmp_path/'embedded-profile'/'storage').stat().st_mode & 0o777 == 0o700
