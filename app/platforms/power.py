from __future__ import annotations
import asyncio
import os
import sys
from app.models.flow import FlowError


def windows_execution_state(flags):
    from app.platforms.windows import execution_state
    try:
        return execution_state(flags)
    except OSError:
        raise FlowError('Windows 未能保持唤醒，请检查系统电源设置')


class KeepAwake:
    """Only prevent idle system sleep while the user's browser session is open."""
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.process = None
        self.windows_previous = None

    async def __aenter__(self):
        if self.enabled and sys.platform == 'darwin':
            self.process = await asyncio.create_subprocess_exec('/usr/bin/caffeinate', '-i', '-w', str(os.getpid()),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        elif self.enabled and sys.platform == 'win32':
            # Execution state is thread-scoped; enter/exit stay on the worker.
            self.windows_previous = windows_execution_state(0x80000001)
        return self

    async def __aexit__(self, *_):
        if self.windows_previous is not None:
            windows_execution_state(self.windows_previous | 0x80000000)
            self.windows_previous = None
        if self.process and self.process.returncode is None:
            self.process.terminate()
            await self.process.wait()
