from __future__ import annotations
import asyncio
import os
import sys


class KeepAwake:
    """Only prevent idle system sleep while the user's browser session is open."""
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.process = None

    async def __aenter__(self):
        if self.enabled and sys.platform == 'darwin':
            self.process = await asyncio.create_subprocess_exec('/usr/bin/caffeinate', '-i', '-w', str(os.getpid()),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        return self

    async def __aexit__(self, *_):
        if self.process and self.process.returncode is None:
            self.process.terminate()
            await self.process.wait()
