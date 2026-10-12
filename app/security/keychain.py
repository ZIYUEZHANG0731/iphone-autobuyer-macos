from __future__ import annotations
import json
import sys
from dataclasses import asdict
from keyring.errors import PasswordDeleteError
from app.models.config import Identity
from app.models.flow import FlowError
from app.models.credential import AppleCredential

class IdentityVault:
    service = "com.autobuyer.apple.identities"

    def __init__(self):
        if sys.platform != "darwin":
            raise FlowError("取货人安全保存仅支持 macOS Keychain")
        # Choose the system backend explicitly: never fall back to plaintext keyrings.
        from keyring.backends.macOS import Keyring
        self.backend = Keyring()

    def put(self, profile_id: str, identity: Identity):
        identity.validate()
        try:
            self.backend.set_password(self.service, profile_id, json.dumps(asdict(identity), ensure_ascii=False))
        except Exception:
            raise FlowError("Keychain 保存失败，请检查钥匙串权限") from None

    def get(self, profile_id: str) -> Identity:
        try:
            raw = self.backend.get_password(self.service, profile_id)
            if not raw:
                raise ValueError
            result = Identity(**json.loads(raw))
            result.validate()
            return result
        except Exception:
            raise FlowError("无法读取 Keychain 取货人，请重新保存或授权钥匙串访问") from None

    def delete(self, profile_id: str):
        try:
            self.backend.delete_password(self.service, profile_id)
        except Exception:
            raise FlowError("Keychain 删除失败") from None


class CredentialVault(IdentityVault):
    """A separate system Keychain item; settings contain only an opaque ID."""
    service = 'com.autobuyer.apple.credentials'

    def __init__(self):
        if sys.platform != 'darwin':
            raise FlowError('Apple 账户安全保存仅支持 macOS 钥匙串')
        from keyring.backends.macOS import Keyring
        self.backend = Keyring()

    def put(self, credential_id: str, credential: AppleCredential):
        credential.validate()
        try:
            self.backend.set_password(self.service, credential_id,
                                      json.dumps(asdict(credential), ensure_ascii=False))
        except Exception:
            raise FlowError('Apple 账户保存失败，请检查钥匙串权限') from None

    def get(self, credential_id: str) -> AppleCredential:
        try:
            raw = self.backend.get_password(self.service, credential_id)
            credential = AppleCredential(**json.loads(raw))
            credential.validate()
            return credential
        except Exception:
            raise FlowError('无法读取保存的 Apple 账户，请重新保存或允许钥匙串访问') from None

    def delete(self, credential_id: str):
        try:
            self.backend.delete_password(self.service, credential_id)
        except PasswordDeleteError:
            # Missing items are already deleted; allow clearing a stale reference.
            return
        except Exception:
            raise FlowError('保存的 Apple 账户未能删除，请检查钥匙串权限') from None
