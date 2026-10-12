from __future__ import annotations
import re
from dataclasses import asdict
from threading import RLock
from urllib.parse import urlsplit, urlunsplit
from app.models.config import Identity

class Redactor:
    def __init__(self):
        self._values: set[str] = set()
        self._lock = RLock()

    def register(self, identity: Identity):
        with self._lock:
            self._values.update(v for v in asdict(identity).values() if v and len(v) > 2)

    def clean(self, text: object) -> str:
        value = str(text)
        with self._lock:
            for secret in sorted(self._values, key=len, reverse=True):
                value = value.replace(secret, "[已脱敏]")
        value = re.sub(r"(?<!\d)\d{17}[0-9Xx](?!\d)", "[证件已脱敏]", value)
        value = re.sub(r"(?<!\d)(?:\+?86[ -]?)?1[3-9]\d{9}(?!\d)", "[电话已脱敏]", value)
        value = re.sub(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", "[邮箱已脱敏]", value)
        value = re.sub(r"(https?://[^\s?#]+)[?#][^\s]*", r"\1?[参数已移除]", value)
        return value

    def object(self, value):
        if isinstance(value, str):
            return self.clean(value)
        if isinstance(value, dict):
            return {self.clean(k): self.object(v) for k,v in value.items()}
        if isinstance(value, list):
            return [self.object(v) for v in value]
        return value

def public_url(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, p.path, "", ""))
