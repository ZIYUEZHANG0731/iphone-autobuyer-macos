from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.automation.browser import BrowserSession
from app.models.config import Mode, Settings
from app.models.flow import FlowError
from app.services.daily_chrome import (
    DailyChromeBridge, EXTENSION_ID, extension_installed, local_endpoint,
    selected_apple_page,
)


@pytest.mark.parametrize('value', [
    'ws://0.0.0.0:8123/private', 'ws://localhost:8123/private',
    'wss://127.0.0.1:8123/private', 'ws://user:password@127.0.0.1:8123/private',
    'ws://127.0.0.1:8123/private?secret=x', 'ws://127.0.0.1:8123/',
    'ws://127.0.0.1:0/private', 'ws://127.0.0.1:99999/private', None,
])
def test_connection_endpoint_is_private_loopback_only(value):
    assert not local_endpoint(value)
    assert local_endpoint('ws://127.0.0.1:8123/fixture-private-connection')


def test_install_detection_only_checks_chrome_extension_metadata(tmp_path):
    assert not extension_installed(tmp_path)
    (tmp_path/'UnrelatedProfile'/'Extensions'/EXTENSION_ID).mkdir(parents=True)
    assert not extension_installed(tmp_path)
    (tmp_path/'Profile 2'/'Extensions'/EXTENSION_ID).mkdir(parents=True)
    assert extension_installed(tmp_path)


def page(url):
    return SimpleNamespace(url=url, is_closed=lambda: False,
                           set_default_timeout=lambda _: None,
                           set_default_navigation_timeout=lambda _: None)


def test_connection_requires_one_selected_china_store_tab():
    selected = page('https://secure7.www.apple.com.cn/shop/account/home')
    assert selected_apple_page([selected]) is selected
    for pages in ([], [page('https://example.invalid')],
                  [selected, page('https://www.apple.com.cn/store')],
                  [page('https://www.apple.com/shop/account/home')]):
        with pytest.raises(FlowError, match='只选择一个'):
            selected_apple_page(pages)


async def test_daily_session_preserves_the_selected_page_and_never_closes_user_context(tmp_path,monkeypatch):
    import app.automation.browser as module
    selected = page('https://www.apple.com.cn/store')
    context = SimpleNamespace(pages=[selected], close=AsyncMock(), new_page=AsyncMock())
    browser = SimpleNamespace(contexts=[context], close=AsyncMock())
    playwright = SimpleNamespace(chromium=SimpleNamespace(connect=AsyncMock(return_value=browser)),stop=AsyncMock())
    monkeypatch.setattr(module,'async_playwright',lambda:SimpleNamespace(start=AsyncMock(return_value=playwright)))
    monkeypatch.setattr(DailyChromeBridge,'start',AsyncMock(return_value='ws://127.0.0.1:1234/fixture-connection'))
    close = AsyncMock();monkeypatch.setattr(DailyChromeBridge,'close',close)
    async with BrowserSession(tmp_path,kind='daily_chrome') as session:
        assert session.page is selected and session.process is None
        assert session.label=='日常 Chrome（沿用原有登录）'
    context.new_page.assert_not_called();context.close.assert_not_called()
    browser.close.assert_awaited_once();close.assert_awaited_once()
    playwright.stop.assert_awaited_once()


async def test_missing_extension_does_not_launch_a_replacement_profile(monkeypatch):
    import app.services.daily_chrome as module
    monkeypatch.setattr(module,'extension_installed',lambda:False)
    bridge=DailyChromeBridge()
    with pytest.raises(FlowError,match='安装 Chrome 连接扩展'):
        await bridge.start()
    assert bridge.process is None


async def test_browser_focus_feedback_describes_selected_page_without_private_url(tmp_path):
    import asyncio
    from app.gui.worker import Worker
    config=Settings(browser_kind='daily_chrome')
    worker=Worker('login_check',config,tmp_path)
    selected=SimpleNamespace(url='https://secure9.www.apple.com.cn/shop/signIn?ssi=PRIVATE_CONTEXT')
    async def focus(_page=None):
        worker.control.stop_event.set()
    session=SimpleNamespace(page=selected,label='日常 Chrome（沿用原有登录）',
                            ensure_page=AsyncMock(return_value=selected),focus_page=focus)
    events=[];worker.data.connect(lambda kind,data:events.append((kind,data)))
    worker.focus_requested.set()
    await asyncio.wait_for(worker.watch_payment(session,None),3)
    feedback=next(data for kind,data in events if kind=='browser_focus_status')
    assert 'Apple 账户登录页' in feedback and 'Playwright · iPhone AutoBuyer' in feedback
    assert 'PRIVATE' not in str(events) and 'https://' not in str(events)
    assert next(data for kind,data in events if kind=='browser_page')['page']=='Apple 账户登录页'


async def test_waiting_for_extension_grant_is_cancellable_and_child_exits(tmp_path,monkeypatch):
    import app.services.daily_chrome as module
    from app.models.flow import Stopped
    from playwright._impl._driver import compute_driver_executable
    node,_=compute_driver_executable()
    package=tmp_path/'fake-driver';(package/'lib').mkdir(parents=True)
    (package/'lib/coreBundle.js').write_text('module.exports={tools:{createBrowserWithInfo:()=>new Promise(()=>{})}}')
    monkeypatch.setattr(module,'extension_installed',lambda:True)
    monkeypatch.setattr('playwright._impl._driver.compute_driver_executable',lambda:(node,str(package/'cli.js')))
    called=0
    def cancel():
        nonlocal called
        called+=1
        if called>=3:raise Stopped('已取消连接')
    bridge=DailyChromeBridge(stop_check=cancel,timeout=15)
    try:
        with pytest.raises(Stopped):await bridge.start()
    finally:
        await bridge.close()
    assert bridge.process.returncode==0


def test_daily_chrome_setting_roundtrips():
    config=Settings(browser_kind='daily_chrome')
    config.validate(Mode.WEBSITE_DRY_RUN,require_stores=False)
    from dataclasses import asdict
    assert Settings.from_dict(asdict(config)).browser_kind=='daily_chrome'


@pytest.mark.e2e
async def test_bundled_driver_bridge_exposes_existing_default_context(tmp_path,monkeypatch):
    """Real remote protocol, with a synthetic browser in a temporary profile.

    The extension itself still needs a user-approved real connection. This test
    verifies our Python/Node transport and cleanup without accessing daily Chrome.
    """
    import asyncio,json
    from playwright._impl._driver import compute_driver_executable
    from playwright.async_api import async_playwright
    import app.services.daily_chrome as module
    node,cli=compute_driver_executable()
    real_core=str(Path(cli).parent/'lib/coreBundle.js')
    fake_package=tmp_path/'fake-driver';(fake_package/'lib').mkdir(parents=True)
    # Use the actual pinned Playwright server, replacing only the browser
    # factory that normally waits for an extension grant.
    factory=f'''const c=require({json.dumps(real_core)});
module.exports={{remote:c.remote,tools:{{createBrowserWithInfo:async(config,client,opts)=>{{
  if(config.extension!==true || opts.browser!=='chrome' || client.clientName!=='iPhone AutoBuyer') throw Error('wrong factory');
  const ctx=await c.inprocess.playwright.chromium.launchPersistentContext({json.dumps(str(tmp_path/'fixture-profile'))},{{headless:true}});
  await ctx.pages()[0].setContent('<h1>Synthetic connected session</h1>');
  return {{browser:ctx._browser,ownership:'attached'}};
}}}}}};'''
    (fake_package/'lib/coreBundle.js').write_text(factory)
    monkeypatch.setattr(module,'extension_installed',lambda:True)
    monkeypatch.setattr('playwright._impl._driver.compute_driver_executable',lambda:(node,str(fake_package/'cli.js')))
    bridge=DailyChromeBridge(timeout=15)
    try:
        endpoint=await bridge.start()
        async with async_playwright() as pw:
            browser=await pw.chromium.connect(endpoint)
            assert len(browser.contexts)==1
            context=browser.contexts[0]
            assert len(context.pages)==1
            assert await context.pages[0].get_by_role('heading',name='Synthetic connected session').count()==1
            second=await context.new_page()
            await second.set_content('<p>Auxiliary account check fixture</p>')
            assert await second.locator('p').inner_text()=='Auxiliary account check fixture'
            await second.close()
            assert len(context.pages)==1
            await browser.close()
    finally:
        await bridge.close()
    assert bridge.process.returncode==0
