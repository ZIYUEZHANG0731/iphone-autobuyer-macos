"""Typed records in native secure storage; settings hold references only."""
from dataclasses import asdict
from copy import deepcopy
import json
import threading
from keyring.errors import PasswordDeleteError

from app.models.config import Identity
from app.models.credential import AppleCredential
from app.models.flow import FlowError
from app.platforms import credential_backend, vault_label


class SecureVault:
    missing_ok = False
    _records = {}
    _temporary = set()
    _fallback = {}
    _lock = threading.RLock()

    def __init__(self):
        self._backend = None

    @property
    def backend(self):
        if self._backend is None:
            self._backend = credential_backend()
        return self._backend

    @backend.setter
    def backend(self, value):
        self._backend = value

    @classmethod
    def cached(cls, reference):
        with cls._lock:
            return deepcopy(cls._records.get((cls.service, reference)))

    @classmethod
    def is_temporary(cls, reference):
        with cls._lock:
            return (cls.service, reference) in cls._temporary

    @classmethod
    def remember(cls, reference, record, *, temporary=True, fallback=None):
        record.validate()
        key = (cls.service, reference)
        with cls._lock:
            cls._records[key] = deepcopy(record)
            if temporary:
                cls._temporary.add(key)
                cls._fallback[key] = deepcopy(fallback or {})
            else:
                cls._temporary.discard(key)
                cls._fallback.pop(key, None)

    @classmethod
    def forget(cls, reference):
        key = (cls.service, reference)
        with cls._lock:
            cls._records.pop(key, None)
            cls._temporary.discard(key)
            cls._fallback.pop(key, None)

    @classmethod
    def clear_cache(cls):
        with cls._lock:
            cls._records.clear()
            cls._temporary.clear()
            cls._fallback.clear()

    def put(self, reference: str, record):
        record.validate()
        try:
            self.backend.set_password(self.service, reference,
                                      json.dumps(asdict(record), ensure_ascii=False))
            self.remember(reference, record, temporary=False)
        except Exception:
            raise FlowError(f'{self.label}保存失败，请检查{vault_label()}权限') from None

    def get(self, reference: str):
        cached = self.cached(reference)
        if cached is not None:
            return cached
        try:
            record = self.record_type(**json.loads(self.backend.get_password(self.service, reference)))
            record.validate()
            self.remember(reference, record, temporary=False)
            return record
        except Exception:
            raise FlowError(f'无法读取保存的{self.label}，请重新保存或允许{vault_label()}访问') from None

    def delete(self, reference: str):
        if self.is_temporary(reference):
            self.forget(reference)
            return
        try:
            self.backend.delete_password(self.service, reference)
            self.forget(reference)
        except PasswordDeleteError:
            self.forget(reference)
            if not self.missing_ok:
                raise FlowError(f'{self.label}删除失败') from None
        except Exception:
            raise FlowError(f'{self.label}删除失败，请检查{vault_label()}权限') from None


class IdentityVault(SecureVault):
    service = 'com.autobuyer.apple.identities'
    record_type = Identity
    label = '取货人'


class CredentialVault(SecureVault):
    service = 'com.autobuyer.apple.credentials'
    record_type = AppleCredential
    label = 'Apple 账户'
    missing_ok = True


def settings_for_disk(config):
    """Session-only inputs never leave dangling references in settings/export."""
    raw = config.to_dict()
    key = (CredentialVault.service, config.credential_id)
    with CredentialVault._lock:
        if key in CredentialVault._temporary:
            saved = CredentialVault._fallback.get(key, {})
            raw.update(credential_id=saved.get('credential_id', ''),
                       auto_login=saved.get('auto_login', False),
                       trust_browser=saved.get('trust_browser', False))
        identity_key = (IdentityVault.service, config.profile_id)
        if identity_key in IdentityVault._temporary:
            saved = IdentityVault._fallback.get(identity_key, {})
            raw.update(profile_id=saved.get('profile_id', ''),
                       transmit_identity=saved.get('transmit_identity', False))
    return raw
