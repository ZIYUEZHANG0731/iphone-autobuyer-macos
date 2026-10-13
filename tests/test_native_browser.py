from __future__ import annotations

import pytest

from app.automation.browser import BrowserSession, installed_chrome


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
        # A protocol disconnect reproduces the surviving browser after an app
        # exit without deliberately crashing Python/Qt or touching real login.
        await first.browser.close();await first.playwright.stop()
        first.browser=None;first.playwright=None
        assert process.returncode is None
        async with BrowserSession(tmp_path) as restored:
            assert restored.recovered and restored.process is None
            assert restored.chrome_owner.pid==process.pid
            assert await restored.page.get_by_role('heading',name='Offline recovery fixture').count()==1
            assert await restored.page.evaluate("document.cookie.includes('fixture_session=synthetic')")
        await asyncio.wait_for(process.wait(),5)
        assert process.returncode is not None
    finally:
        await first.__aexit__(None,None,None)
