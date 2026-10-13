from __future__ import annotations

import asyncio
import json
import sys
import pytest

from app.automation.browser import BrowserSession, installed_chrome


@pytest.mark.e2e
@pytest.mark.skipif(sys.platform!='win32' or installed_chrome() is None,
    reason='Windows native Chrome occlusion integration')
async def test_owned_windows_chrome_remains_interactive_when_covered(tmp_path):
    async with BrowserSession(tmp_path) as session:
        await session.context.route('**/*',lambda route:route.fulfill(
            content_type='text/html',body='''<label><input type="radio">Offline choice</label>
            <script>window.changes=[];document.querySelector('input').addEventListener('change',e=>changes.push(e.isTrusted));</script>'''))
        await session.page.goto('https://autobuyer.invalid/occlusion')
        cdp=await session.context.new_cdp_session(session.page)
        try:bounds=(await cdp.send('Browser.getWindowForTarget'))['bounds']
        finally:await cdp.detach()
        # A temporary opaque Qt window covers only this fixture's Chrome.
        # It makes Windows' native occlusion reproducible without altering
        # the user's windows, browser profile or desktop settings.
        script='''import json,sys
from PySide6.QtWidgets import QApplication,QWidget
from PySide6.QtCore import Qt,QTimer
app=QApplication([]);window=QWidget()
window.setWindowTitle('AutoBuyer offline occlusion fixture')
window.setWindowFlags(Qt.WindowType.FramelessWindowHint|Qt.WindowType.WindowStaysOnTopHint)
b=json.loads(sys.argv[1]);window.setGeometry(b['left']-20,b['top']-20,b['width']+40,b['height']+40)
window.setStyleSheet('background:#eeeeee');window.show()
QTimer.singleShot(200,lambda:print('READY',flush=True))
QTimer.singleShot(10000,app.quit);app.exec()
'''
        cover=await asyncio.create_subprocess_exec(sys.executable,'-c',script,json.dumps(bounds),
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
        try:
            assert (await asyncio.wait_for(cover.stdout.readline(),5)).strip()==b'READY'
            assert await session.page.evaluate('document.visibilityState')=='visible'
            assert await asyncio.wait_for(session.page.evaluate('new Promise(resolve=>requestAnimationFrame(()=>resolve(true)))'),3)
            await session.page.get_by_role('radio',name='Offline choice').check(timeout=3000)
            assert await session.page.evaluate('changes')==[True]
            assert await session.page.evaluate('navigator.webdriver') is False
        finally:
            if cover.returncode is None:cover.terminate()
            await cover.wait()


@pytest.mark.e2e
@pytest.mark.skipif(installed_chrome() is None,reason='Native Chrome integration needs installed Google Chrome')
async def test_native_session_uses_its_own_profile_preserves_cookies_and_closes_its_process(tmp_path):
    old=tmp_path/'browser-profile';old.mkdir();(old/'existing-marker.txt').write_text('preserve')
    async with BrowserSession(tmp_path) as session:
        assert session.label=='Google Chrome（软件独立会话）'
        process=session.process
        assert process is not None and process.returncode is None
        # The verified native startup uses an explicit localhost port. Observe
        # the browser's default; no script changes navigator or any fingerprint.
        assert await session.page.evaluate('navigator.webdriver') is False
        # This reserved domain is fulfilled in memory. No Apple/account request.
        await session.context.route('**/*',lambda route:route.fulfill(
            content_type='text/html',body='<h1>Offline browser fixture</h1><button onclick="document.cookie=\'fixture_session=synthetic;max-age=3600;path=/\';document.cookie=\'fixture_transient=synthetic;path=/\'">Save fixture session</button>'))
        await session.page.goto('https://autobuyer.invalid/fixture')
        await session.page.get_by_role('button',name='Save fixture session').click()
        assert await session.page.evaluate("document.cookie.includes('fixture_session=synthetic')")
        assert await session.page.evaluate("document.cookie.includes('fixture_transient=synthetic')")
    assert process.returncode is not None
    assert (old/'existing-marker.txt').read_text(encoding='utf-8')=='preserve'
    assert not (tmp_path/'chrome-profile'/'existing-marker.txt').exists()
    # A normal restart reads only the same app-owned profile, never a daily profile.
    async with BrowserSession(tmp_path) as reopened:
        # Cookie restoration must not reload old checkout/login tabs.
        assert len(reopened.context.pages)==1
        assert reopened.page.url=='about:blank'
        await reopened.context.route('**/*',lambda route:route.fulfill(content_type='text/html',body='<h1>Offline restored fixture</h1>'))
        await reopened.page.goto('https://autobuyer.invalid/fixture')
        assert await reopened.page.evaluate("document.cookie.includes('fixture_session=synthetic')")
        # Login also uses session cookies, which have no explicit expiry.
        assert await reopened.page.evaluate("document.cookie.includes('fixture_transient=synthetic')")
    assert reopened.process.returncode is not None


@pytest.mark.e2e
@pytest.mark.skipif(installed_chrome() is None,reason='Native Chrome integration needs installed Google Chrome')
async def test_cold_restart_closes_restored_tabs_but_preserves_session_cookie(tmp_path):
    async with BrowserSession(tmp_path) as session:
        await session.context.route('**/*',lambda route:route.fulfill(content_type='text/html',body='<h1>Old offline login tab</h1>'))
        await session.page.goto('https://autobuyer.invalid/old-login-0')
        await session.page.evaluate("document.cookie='fixture_transient=synthetic;path=/'")
        for index in range(1,4):
            page=await session.new_page()
            await page.goto(f'https://autobuyer.invalid/old-login-{index}')
        assert len(session.context.pages)==4
    async with BrowserSession(tmp_path) as restarted:
        assert not restarted.recovered
        assert restarted.page.url=='about:blank' and restarted.context.pages==[restarted.page]
        await restarted.context.route('**/*',lambda route:route.fulfill(content_type='text/html',body='<h1>Fresh offline login</h1>'))
        await restarted.page.goto('https://autobuyer.invalid/fresh-login')
        assert await restarted.page.evaluate("document.cookie.includes('fixture_transient=synthetic')")


@pytest.mark.e2e
@pytest.mark.skipif(installed_chrome() is None,reason='Native Chrome integration needs installed Google Chrome')
async def test_disconnected_app_can_reattach_its_surviving_chrome_without_logging_in_again(tmp_path):
    import asyncio
    first=BrowserSession(tmp_path)
    await first.__aenter__()
    process=first.process
    try:
        await first.context.route('**/*',lambda route:route.fulfill(content_type='text/html',body='''
            <h1>Offline recovery fixture</h1><button onclick="document.cookie='fixture_session=synthetic;path=/'">Save session</button>'''))
        await first.page.goto('https://autobuyer.invalid/recovery')
        await first.page.get_by_role('button',name='Save session').click()
        extra=await first.new_page()
        await extra.goto('https://autobuyer.invalid/pending-step')
        # A protocol disconnect reproduces the surviving browser after an app
        # exit without deliberately crashing Python/Qt or touching real login.
        await first.browser.close();await first.playwright.stop()
        first.browser=None;first.playwright=None
        assert process.returncode is None
        async with BrowserSession(tmp_path) as restored:
            assert restored.recovered and restored.process is None
            assert len(restored.context.pages)==2
            assert restored.chrome_owner.pid==process.pid
            assert await restored.page.get_by_role('heading',name='Offline recovery fixture').count()==1
            assert await restored.page.evaluate("document.cookie.includes('fixture_session=synthetic')")
        await asyncio.wait_for(process.wait(),5)
        assert process.returncode is not None
    finally:
        await first.__aexit__(None,None,None)
