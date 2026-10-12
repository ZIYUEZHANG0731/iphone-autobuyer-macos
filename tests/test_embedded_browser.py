"""A real Qt host, a worker bridge, CDP detach, profile reopen and normal exit.

Only the reserved autobuyer.invalid origin is fulfilled in memory. This tests
the browser mechanism, not Apple authentication or sale-day entry speed.
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
        from pathlib import Path
        from PySide6.QtCore import QThread, QTimer
        from PySide6.QtWidgets import QApplication
        from shiboken6 import delete, isValid
        from app.automation.browser import BrowserSession
        from app.automation.control import Control
        from app.gui.window import MainWindow
        from app.utils.qt import prepare_plugins, single_shot

        prepare_plugins()
        qt=QApplication(['embedded-regression'])
        window=MainWindow(Path(sys.argv[1]))
        window.show()
        info=window.ensure_embedded()
        window.stack.setCurrentIndex(6)
        phase=sys.argv[2]

        class Probe(QThread):
            def __init__(self):
                super().__init__(window)
                self.control=Control(False)
                self.idle=False
                self.session_open=True
                self.error=None
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
                    if phase=='save':await session.page.get_by_role('button',name='Save fixture').click()
                    assert await session.page.evaluate("document.cookie.includes('fixture_session=synthetic') && document.cookie.includes('fixture_persistent=synthetic')")
                    background=await session.new_page()
                    await background.goto('https://autobuyer.invalid/background')
                    assert background is not session.page and not session.page.is_closed()
                    await session.close_page(background)
                    await asyncio.sleep(.1)
                    assert background.is_closed() and not session.page.is_closed()
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
