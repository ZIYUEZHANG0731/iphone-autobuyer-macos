from __future__ import annotations
import asyncio
import fcntl
import threading
import time
from pathlib import Path
from app.models.flow import Stopped, FlowError

class Control:
    def __init__(self, interactive=True, *, site_cooldown=None):
        self.stop_event = threading.Event()
        self.cancel_event = threading.Event()
        self.resume_event = threading.Event()
        self.interactive = interactive
        self.site_cooldown=site_cooldown
        self.quote_lock=threading.Lock();self.approved_quotes=set()
        self.consent_lock=threading.Lock();self.consent_approval=None

    def approve_consent(self, key):
        # A deliberate GUI action authorizes only this observed page, once.
        # Nothing is persisted or inherited by another task/agreement.
        with self.consent_lock:self.consent_approval=(key,time.monotonic()+120)

    def consume_consent(self, key):
        with self.consent_lock:
            approval=self.consent_approval;self.consent_approval=None
        return bool(approval and approval[0]==key and time.monotonic()<approval[1])

    def approve_quote(self,key):
        with self.quote_lock:self.approved_quotes.add(key)

    def quote_approved(self,key):
        with self.quote_lock:return key in self.approved_quotes

    def stop(self):
        self.stop_event.set()
        with self.consent_lock:self.consent_approval=None

    def resume(self):
        self.resume_event.set()

    def cancel_task(self):
        self.cancel_event.set()
        with self.consent_lock:self.consent_approval=None

    def reset_task(self):
        self.cancel_event.clear()
        self.resume_event.clear()
        with self.quote_lock:self.approved_quotes.clear()
        with self.consent_lock:self.consent_approval=None

    def check_session(self):
        if self.stop_event.is_set():
            raise Stopped("用户已关闭会话")

    def check(self):
        self.check_session()
        if self.cancel_event.is_set():
            raise Stopped("用户已停止当前任务")

    async def sleep(self, seconds: float):
        deadline = asyncio.get_running_loop().time() + seconds
        while asyncio.get_running_loop().time() < deadline:
            self.check()
            await asyncio.sleep(min(0.1, max(0,deadline-asyncio.get_running_loop().time())))
        self.check()

    async def wait_resume(self):
        while not self.resume_event.is_set():
            await self.sleep(.15)
        self.resume_event.clear()

class ProcessLock:
    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.handle = self.path.open("a")
        try:
            fcntl.flock(self.handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            self.handle.close()
            self.handle = None
            raise FlowError("另一个购买任务正在运行，请先停止它") from None
        self.path.chmod(0o600)
        return self

    def __exit__(self,*_):
        if self.handle:
            fcntl.flock(self.handle.fileno(),fcntl.LOCK_UN)
            self.handle.close()
