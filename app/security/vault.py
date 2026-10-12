"""Typed records in native secure storage; settings hold references only."""
from dataclasses import asdict
import json
from keyring.errors import PasswordDeleteError

from app.models.config import Identity
from app.models.credential import AppleCredential
from app.models.flow import FlowError
from app.platforms import credential_backend, vault_label


class SecureVault:
    missing_ok = False

    def __init__(self):
        self.backend = credential_backend()

    def put(self, reference: str, record):
        record.validate()
        try:
            self.backend.set_password(self.service, reference,
                                      json.dumps(asdict(record), ensure_ascii=False))
        except Exception:
            raise FlowError(f'{self.label}保存失败，请检查{vault_label()}权限') from None

    def get(self, reference: str):
        try:
            record = self.record_type(**json.loads(self.backend.get_password(self.service, reference)))
            record.validate()
            return record
        except Exception:
            raise FlowError(f'无法读取保存的{self.label}，请重新保存或允许{vault_label()}访问') from None

    def delete(self, reference: str):
        try:
            self.backend.delete_password(self.service, reference)
        except PasswordDeleteError:
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
