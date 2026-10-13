from __future__ import annotations
import asyncio
from pathlib import Path
import socket
import time
from playwright.async_api import async_playwright
from app.models.flow import FlowError
from app.models.config import apple_url
from urllib.parse import urlsplit
from app.services.chrome_session import find_owned_chrome
from app.services.login import account_page_url
from app.platforms import installed_chrome
from app.platforms.files import private_directory


class BrowserSession:
    def __init__(self, root: Path, *, headless=False, isolated=False, embedded=None, kind='chrome', stop_check=None):
        self.root = root
        self.headless = headless
        self.isolated = isolated
        self.context = None
        self.browser = None
        self.playwright = None
        self.page = None
        self.process = None
        self.label = 'Chromium'
        self.embedded = embedded
        self.recovered = False
        self.chrome_owner = None
        self.chrome_executable = None
        self.chrome_profile = None
        self.kind = kind
        self.stop_check = stop_check
        self.daily_bridge = None

    async def _start_daily_chrome(self):
        from app.services.daily_chrome import DailyChromeBridge, selected_apple_page
        self.daily_bridge = DailyChromeBridge(stop_check=self.stop_check)
        endpoint = await self.daily_bridge.start()
        self.browser = await self.playwright.chromium.connect(endpoint, timeout=10000)
        if len(self.browser.contexts) != 1:
            raise FlowError('Chrome 连接未提供唯一会话，请重新选择 Apple 页面')
        self.context = self.browser.contexts[0]
        self.page = selected_apple_page(self.context.pages)
        self.label = '日常 Chrome（沿用原有登录）'

    async def _start_chrome(self, executable: Path):
        # Use Chrome's supported local connection and a private, app-owned
        # profile. Never connect to the user's everyday browser or copy cookies.
        # Leave website/browser defaults intact; no injected scripts, modified
        # fingerprints, proxy overrides or replayed Apple requests.
        profile=self.root/'chrome-profile'
        private_directory(profile)
        self.chrome_executable,self.chrome_profile=executable,profile
        existing=await asyncio.to_thread(find_owned_chrome,executable,profile)
        if len(existing)>1:raise FlowError('发现多个软件 Chrome 会话，请保留一个再恢复')
        if existing:
            self.chrome_owner=existing[0]
            self.browser=await self.playwright.chromium.connect_over_cdp(
                f'http://127.0.0.1:{self.chrome_owner.port}',no_defaults=True,timeout=3000)
            self.context=self.browser.contexts[0];self.recovered=True
            self.label='Google Chrome（已恢复软件会话）'
            return
        # Preserve the verified native startup's explicit localhost port.
        # Recovery still matches the owned process and exact profile only.
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
        (profile/'DevToolsActivePort').unlink(missing_ok=True)
        self.process=await asyncio.create_subprocess_exec(str(executable),f'--user-data-dir={profile}',
            f'--remote-debugging-port={port}','--remote-debugging-address=127.0.0.1',
            # Chrome session restoration is also needed for cookies without
            # expiry. Load the profile without restoring old checkout tabs;
            # our normal new_page below creates the visible fresh window.
            '--restore-last-session','--no-startup-window',
            '--no-first-run','--no-default-browser-check',
            stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
        deadline=time.monotonic()+15
        while True:
            try:
                self.browser=await self.playwright.chromium.connect_over_cdp(
                    f'http://127.0.0.1:{port}',no_defaults=True,timeout=1000)
                break
            except Exception:
                if self.process.returncode is not None or time.monotonic()>=deadline:raise
                await asyncio.sleep(.1)
        self.context=self.browser.contexts[0]
        self.label='Google Chrome（软件独立会话）'

    async def __aenter__(self):
        try:
            self.playwright = await async_playwright().start()
            if self.isolated:
                self.browser = await self.playwright.chromium.launch(headless=self.headless)
                self.context = await self.browser.new_context(locale="zh-CN",timezone_id="Asia/Shanghai",service_workers="block")
            elif self.kind == 'daily_chrome':
                if self.headless:
                    raise FlowError('接入日常 Chrome 需要在扩展中选择官网页面')
                await self._start_daily_chrome()
            elif self.embedded and not self.headless:
                # Qt queues its first blank navigation during initialization.
                # Wait for it before any real navigation can be overwritten.
                await self.embedded['bridge'].request('ready',self.embedded['primary_id'])
                self.browser=await self.playwright.chromium.connect_over_cdp(self.embedded['endpoint'],no_defaults=True,timeout=10000)
                self.context=self.browser.contexts[0]
                self.label='内置浏览器（持久登录会话）'
            elif not self.headless and (chrome:=installed_chrome()):
                await self._start_chrome(chrome)
            else:
                profile = self.root / "browser-profile"
                private_directory(profile)
                self.context = await self.playwright.chromium.launch_persistent_context(
                    str(profile),headless=self.headless,locale="zh-CN",timezone_id="Asia/Shanghai",service_workers="block",
                    handle_sigint=False,handle_sigterm=False,handle_sighup=False,
                )
            # No trace, HAR, videos or storage_state dumps; checkout can contain identities.
            if self.kind == 'daily_chrome' and not self.isolated:
                pass  # Preserve the exact tab selected in the approval page.
            elif self.embedded and not self.headless:
                self.page=await self.target_page(self.embedded['primary_id'])
            elif self.recovered:
                pages=[p for p in self.context.pages if not p.is_closed()]
                checkout=[p for p in pages if apple_url(p.url) and urlsplit(p.url).path.startswith('/shop/checkout')]
                accounts=[p for p in pages if account_page_url(p.url)]
                public=[p for p in pages if apple_url(p.url)]
                nonblank=[p for p in pages if p.url and not p.url.startswith('about:blank')]
                self.page=(checkout or accounts or public or nonblank or pages)[-1] if pages else await self.new_page()
            else:self.page = await self.context.new_page()
            self.page.set_default_timeout(15000)
            self.page.set_default_navigation_timeout(30000)
            return self
        except Exception as error:
            await self.__aexit__(None,None,None)
            if isinstance(error,FlowError):raise
            raise FlowError('内置浏览器连接未完成，请重试或切换独立 Chrome' if self.embedded else "浏览器启动失败；请检查软件会话是否被占用。已安装 Chrome 时使用其独立会话；否则在“诊断…”安装 Chromium") from None

    async def target_id(self,page):
        cdp=await self.context.new_cdp_session(page)
        try:return (await cdp.send('Target.getTargetInfo'))['targetInfo']['targetId']
        finally:await cdp.detach()

    async def target_page(self,target):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            for page in self.context.pages:
                if not page.is_closed() and await self.target_id(page)==target:return page
            await asyncio.sleep(.05)
        raise FlowError('内置浏览器的新页面暂未连接')

    async def new_page(self):
        if not self.embedded:return await self.context.new_page()
        target=await self.embedded['bridge'].request('new')
        return await self.target_page(target)

    async def close_page(self,page):
        if not self.embedded:return await page.close()
        if not page.is_closed():await self.embedded['bridge'].request('close',await self.target_id(page))

    async def focus_page(self,page=None):
        page=page or self.page
        if self.embedded:await self.embedded['bridge'].request('focus',await self.target_id(page))
        else:await page.bring_to_front()

    async def ensure_page(self, *, prefer_account=False):
        """Recover a closed tab inside this app's own browser context only."""
        if not self.context or (self.browser and not self.browser.is_connected()):
            raise FlowError('软件浏览器已关闭，请点击“登录并保持会话”重新打开')
        pages=[p for p in self.context.pages if not p.is_closed()]
        accounts=[p for p in pages if account_page_url(p.url)]
        if prefer_account and len(accounts)==1:
            self.page=accounts[0]
        elif self.page and not self.page.is_closed():
            return self.page
        else:
            apple_pages=[p for p in pages if apple_url(p.url)]
            if len(apple_pages)==1:self.page=apple_pages[0]
            elif len(pages)==1:self.page=pages[0]
            elif not pages:self.page=await self.new_page()
            else:raise FlowError('软件浏览器有多个页面，请保留本次购买页面后再检查')
        self.page.set_default_timeout(15000)
        self.page.set_default_navigation_timeout(30000)
        return self.page

    async def __aexit__(self,*_):
        if self.daily_bridge or self.embedded:
            # These connections borrow a browser owned by Chrome/the GUI.
            # Disconnect only: do not close its context, tabs or process.
            await self._close_connection()
            return
        if self.chrome_owner and self.browser and self.browser.is_connected():
            # Close only the recovered app-owned process, never daily Chrome.
            try:
                cdp=await self.browser.new_browser_cdp_session();await cdp.send('Browser.close')
            except Exception:pass
            # Wait for this owned PID to finish its normal disk flush. Never
            # signal a PID found by inspection: it could have been reused.
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                owners=await asyncio.to_thread(find_owned_chrome,self.chrome_executable,self.chrome_profile)
                if not any(owner.pid==self.chrome_owner.pid for owner in owners):break
                await asyncio.sleep(.05)
        if self.process and self.process.returncode is None:
            # A protocol disconnect is not a Chrome quit. Ask our own browser
            # to exit normally so persistent cookies are flushed to its profile.
            if self.browser and self.browser.is_connected():
                try:
                    cdp=await self.browser.new_browser_cdp_session()
                    await cdp.send('Browser.close')
                except Exception:
                    pass
            try:
                await asyncio.wait_for(self.process.wait(),5)
            except asyncio.TimeoutError:
                if self.process.returncode is None:self.process.terminate()
                try:await asyncio.wait_for(self.process.wait(),5)
                except asyncio.TimeoutError:self.process.kill();await self.process.wait()
        if self.context and self.process is None and not self.chrome_owner:
            try:
                await self.context.close()
            except Exception:
                pass
        await self._close_connection()

    async def _close_connection(self):
        try:
            if self.browser:
                try:await self.browser.close()
                except Exception:pass
        finally:
            if self.daily_bridge:await self.daily_bridge.close()
            if self.playwright:await self.playwright.stop()
