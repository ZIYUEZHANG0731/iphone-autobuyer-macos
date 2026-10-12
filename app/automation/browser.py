from __future__ import annotations
import asyncio
from pathlib import Path
import socket
import sys
import time
from playwright.async_api import async_playwright
from app.models.flow import FlowError
from app.models.config import apple_url
from urllib.parse import urlsplit


def installed_chrome() -> Path | None:
    if sys.platform != 'darwin':return None
    for root in (Path('/Applications'),Path.home()/'Applications'):
        path=root/'Google Chrome.app'/'Contents'/'MacOS'/'Google Chrome'
        if path.is_file():return path
    return None


class BrowserSession:
    def __init__(self, root: Path, *, headless=False, isolated=False):
        self.root = root
        self.headless = headless
        self.isolated = isolated
        self.context = None
        self.browser = None
        self.playwright = None
        self.page = None
        self.process = None
        self.label = 'Chromium'

    async def _start_chrome(self, executable: Path):
        # Use Chrome's supported local connection and a private, app-owned
        # profile. Never connect to the user's everyday browser or copy cookies.
        # Leave website/browser defaults intact; no injected scripts, modified
        # fingerprints, proxy overrides or replayed Apple requests.
        profile=self.root/'chrome-profile'
        profile.mkdir(parents=True,exist_ok=True,mode=0o700);profile.chmod(0o700)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
        self.process=await asyncio.create_subprocess_exec(str(executable),f'--user-data-dir={profile}',
            f'--remote-debugging-port={port}','--remote-debugging-address=127.0.0.1',
            '--no-first-run','--no-default-browser-check','about:blank',
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
            elif not self.headless and (chrome:=installed_chrome()):
                await self._start_chrome(chrome)
            else:
                profile = self.root / "browser-profile"
                profile.mkdir(parents=True,exist_ok=True,mode=0o700)
                profile.chmod(0o700)
                self.context = await self.playwright.chromium.launch_persistent_context(
                    str(profile),headless=self.headless,locale="zh-CN",timezone_id="Asia/Shanghai",service_workers="block",
                    handle_sigint=False,handle_sigterm=False,handle_sighup=False,
                )
            # No trace, HAR, videos or storage_state dumps; checkout can contain identities.
            self.page = await self.context.new_page()
            self.page.set_default_timeout(15000)
            self.page.set_default_navigation_timeout(30000)
            return self
        except Exception:
            await self.__aexit__(None,None,None)
            raise FlowError("浏览器启动失败；请检查软件会话是否被占用。已安装 Chrome 时使用其独立会话；否则在“诊断…”安装 Chromium") from None

    async def ensure_page(self, *, prefer_account=False):
        """Recover a closed tab inside this app's own browser context only."""
        if not self.context or (self.browser and not self.browser.is_connected()):
            raise FlowError('软件浏览器已关闭，请点击“登录并保持会话”重新打开')
        pages=[p for p in self.context.pages if not p.is_closed()]
        accounts=[p for p in pages if apple_url(p.url) and urlsplit(p.url).path.startswith('/shop/account/')]
        if prefer_account and len(accounts)==1:
            self.page=accounts[0]
        elif self.page and not self.page.is_closed():
            return self.page
        else:
            apple_pages=[p for p in pages if apple_url(p.url)]
            if len(apple_pages)==1:self.page=apple_pages[0]
            elif len(pages)==1:self.page=pages[0]
            elif not pages:self.page=await self.context.new_page()
            else:raise FlowError('软件浏览器有多个页面，请保留本次购买页面后再检查')
        self.page.set_default_timeout(15000)
        self.page.set_default_navigation_timeout(30000)
        return self.page

    async def __aexit__(self,*_):
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
                self.process.terminate()
                try:await asyncio.wait_for(self.process.wait(),5)
                except asyncio.TimeoutError:self.process.kill();await self.process.wait()
        if self.context and self.process is None:
            try:
                await self.context.close()
            except Exception:
                pass
        if self.browser:
            try:
                await self.browser.close()
            except Exception:
                pass
        if self.playwright:
            await self.playwright.stop()
