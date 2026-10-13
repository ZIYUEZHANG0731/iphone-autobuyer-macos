"""An opt-in connection to a user-selected tab via the official extension."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit

from app.models.config import apple_url
from app.models.flow import FlowError
from app.utils.paths import resource_path
from app.platforms import chrome_locations

EXTENSION_ID = 'mmlmfjhmonkocbjadbfplnigmagldckm'
EXTENSION_URL = f'https://chromewebstore.google.com/detail/playwright-extension/{EXTENSION_ID}'


def extension_installed(root: Path | None = None) -> bool:
    """Read installation metadata only; never open cookies or credentials."""
    root = root or chrome_locations()[1]
    if not root.is_dir():
        return False
    return any((profile / 'Extensions' / EXTENSION_ID).is_dir()
               for profile in root.iterdir()
               if profile.name == 'Default' or
               (profile.name.startswith('Profile ') and profile.name[8:].isdigit()))


def local_endpoint(value) -> bool:
    if not isinstance(value, str):
        return False
    try:
        url = urlsplit(value)
        return (url.scheme == 'ws' and url.hostname == '127.0.0.1'
                and 0 < (url.port or 0) <= 65535 and not url.username
                and not url.password and not url.query and not url.fragment
                and len(url.path) > 1)
    except ValueError:
        return False


def selected_apple_page(pages):
    """Never select an arbitrary personal tab or a historic checkout by rank."""
    pages = [page for page in pages if not page.is_closed()]
    if len(pages) != 1 or not apple_url(pages[0].url):
        reason='扩展没有提供页面' if not pages else '扩展提供了多个页面' if len(pages)>1 else '所选页面不是 Apple 中国商店'
        raise FlowError(reason+'；请在 Chrome 的 Welcome 连接页只选择一个 Apple 中国商店页面，再重新接入')
    return pages[0]


class DailyChromeBridge:
    def __init__(self, *, stop_check=None, timeout=600):
        self.process = None
        self.stop_check = stop_check or (lambda: None)
        self.timeout = timeout

    async def start(self):
        if not extension_installed():
            raise FlowError('请先点“安装 Chrome 连接扩展…”，在日常 Chrome 安装官方 Playwright Extension，然后点“接入日常 Chrome”')
        from playwright._impl._driver import compute_driver_executable, get_driver_env
        node, cli = compute_driver_executable()
        environment = get_driver_env()
        # Nothing from the agent's debugging environment should enable traces,
        # override Chrome's profile, or bypass the extension approval page.
        for key in tuple(environment):
            if key.startswith(('DEBUG', 'PWDEBUG', 'PWTEST_', 'PLAYWRIGHT_MCP_', 'PW_EXTENSION_')):
                environment.pop(key, None)
        self.process = await asyncio.create_subprocess_exec(
            node, str(resource_path('services/daily_chrome_bridge.cjs')),
            str(Path(cli).parent), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            env=environment)
        deadline = asyncio.get_running_loop().time() + self.timeout
        reading = asyncio.create_task(self.process.stdout.readline())
        try:
            while asyncio.get_running_loop().time() < deadline:
                self.stop_check()
                done, _ = await asyncio.wait([reading], timeout=.15)
                if not done:
                    continue
                raw = reading.result()
                if not raw:
                    break
                try:
                    message = json.loads(raw)
                except (ValueError, UnicodeError):
                    break
                if message.get('state') == 'connected' and local_endpoint(message.get('endpoint')):
                    return message['endpoint']
                if message.get('state') != 'waiting':
                    break
                reading = asyncio.create_task(self.process.stdout.readline())
            raise FlowError('Chrome 尚未接入。请在官方扩展连接页选择 Apple 中国商店页面并允许本次连接；若没有连接页，请确认扩展在日常 Chrome 中已启用')
        finally:
            reading.cancel()
            await asyncio.gather(reading, return_exceptions=True)

    async def close(self):
        if not self.process:
            return
        if self.process.stdin:
            self.process.stdin.close()
        try:
            await asyncio.wait_for(self.process.wait(), 3)
        except asyncio.TimeoutError:
            if self.process.returncode is None:
                self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 3)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
