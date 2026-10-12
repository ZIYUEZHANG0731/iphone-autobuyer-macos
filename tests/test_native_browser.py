from __future__ import annotations

import pytest

from app.automation.browser import BrowserSession, installed_chrome


@pytest.mark.e2e
@pytest.mark.skipif(installed_chrome() is None,reason='Native macOS Chrome integration')
async def test_native_session_uses_its_own_profile_preserves_cookies_and_closes_its_process(tmp_path):
    old=tmp_path/'browser-profile';old.mkdir();(old/'existing-marker.txt').write_text('preserve')
    async with BrowserSession(tmp_path) as session:
        assert session.label=='Google Chrome（软件独立会话）'
        process=session.process
        assert process is not None and process.returncode is None
        # This reserved domain is fulfilled in memory. No Apple/account request.
        await session.context.route('**/*',lambda route:route.fulfill(
            content_type='text/html',body='<h1>Offline browser fixture</h1><button onclick="document.cookie=\'fixture_session=synthetic;max-age=3600;path=/\'">Save fixture session</button>'))
        await session.page.goto('https://autobuyer.invalid/fixture')
        await session.page.get_by_role('button',name='Save fixture session').click()
        assert await session.page.evaluate("document.cookie.includes('fixture_session=synthetic')")
    assert process.returncode is not None
    assert (old/'existing-marker.txt').read_text()=='preserve'
    assert not (tmp_path/'chrome-profile'/'existing-marker.txt').exists()
    # A normal restart reads only the same app-owned profile, never a daily profile.
    async with BrowserSession(tmp_path) as reopened:
        await reopened.context.route('**/*',lambda route:route.fulfill(content_type='text/html',body='<h1>Offline restored fixture</h1>'))
        await reopened.page.goto('https://autobuyer.invalid/fixture')
        assert await reopened.page.evaluate("document.cookie.includes('fixture_session=synthetic')")
    assert reopened.process.returncode is not None
