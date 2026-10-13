from __future__ import annotations
import asyncio
import threading
import time
from app.models.flow import Stopped
from app.platforms.locking import ProcessLock

class Control:
    def __init__(self, interactive=True, *, site_cooldown=None):
        self.stop_event = threading.Event()
        self.cancel_event = threading.Event()
        self.resume_event = threading.Event()
        self.interactive = interactive
        self.site_cooldown=site_cooldown
        self.quote_lock=threading.Lock();self.approved_quotes=set()
        self.consent_lock=threading.Lock();self.consent_approval=None
        self.consent_generation=0

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
        with self.consent_lock:self.consent_generation+=1
        self.resume_event.set()

    def cancel_task(self):
        self.cancel_event.set()
        with self.consent_lock:
            self.consent_approval=None;self.consent_generation=0

    def reset_task(self):
        self.cancel_event.clear()
        self.resume_event.clear()
        with self.quote_lock:self.approved_quotes.clear()
        with self.consent_lock:
            self.consent_approval=None;self.consent_generation=0

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

    async def wait_resume_or(self,probe):
        while not self.resume_event.is_set():
            self.check()
            try:
                if await asyncio.wait_for(probe(),3):
                    return True
            except Exception:
                # A page can navigate during an observation. Keep waiting;
                # this never resubmits credentials or fills a verifier.
                pass
            await self.sleep(1)
        self.resume_event.clear()
        return False
