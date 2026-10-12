from dataclasses import dataclass

from app.models.config import ConfigError


@dataclass(repr=False)
class AppleCredential:
    account: str
    password: str

    def __repr__(self):
        return 'AppleCredential(<private>)'

    def validate(self):
        if (not isinstance(self.account, str) or not self.account.strip()
                or len(self.account) > 320 or any(c.isspace() for c in self.account)):
            raise ConfigError('请填写 Apple 账户的邮箱或电话号码')
        if not isinstance(self.password, str) or not self.password or len(self.password) > 1024:
            raise ConfigError('请填写 Apple 账户密码')
