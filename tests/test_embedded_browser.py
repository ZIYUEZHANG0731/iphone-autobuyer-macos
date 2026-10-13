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
def test_embedded_browser_reuses_tabs_persists_session_cookies_and_quits_cleanly(tmp_path):
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
        from app.models.config import Settings, Store, Identity
        from app.models.flow import Availability
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
        result=subprocess.run([sys.executable,'-c',script,str(tmp_path),phase],
                              cwd=Path(__file__).resolve().parents[1],env=env,
                              text=True,capture_output=True,timeout=45)
        assert result.returncode==0,result.stdout+'\n'+result.stderr
        assert 'EMBEDDED_PROFILE_OK:'+phase in result.stdout
        assert 'QThread: Destroyed' not in result.stderr
    assert (tmp_path/'embedded-profile'/'storage').stat().st_mode & 0o777 == 0o700
