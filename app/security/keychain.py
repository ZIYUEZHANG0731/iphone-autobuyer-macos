from __future__ import annotations
import json
import sys
from dataclasses import asdict
from app.models.config import Identity
from app.models.flow import FlowError

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
