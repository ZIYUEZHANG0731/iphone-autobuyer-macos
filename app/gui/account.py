from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout)

from app.models.config import ConfigError
from app.models.credential import AppleCredential
from app.services.private_text import read_private_text


class AccountDialog(QDialog):
    def __init__(self, parent=None, *, credential=None, auto_login=True, trust_browser=True):
        super().__init__(parent)
        self.previous = credential
        self.setWindowTitle('Apple 账户')
        self.resize(510, 390)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(14)
        note = QLabel('直接输入账号密码，无需另外设置授权密码。\n默认加密保存在本机，重新打开软件自动载入，不读取钥匙串。')
        note.setWordWrap(True)
        note.setObjectName('muted')
        layout.addWidget(note)
        form = QFormLayout()
        form.setSpacing(12)
        self.account = QLineEdit(credential.account if credential else '')
        self.account.setAccessibleName('Apple 账户邮箱或电话号码')
        self.account.setPlaceholderText('邮箱或电话号码')
        self.password = QLineEdit()
        self.password.setAccessibleName('Apple 账户密码')
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText('留空保留已填写的密码' if credential else '输入密码')
        form.addRow('Apple 账户', self.account)
        form.addRow('密码', self.password)
        layout.addLayout(form)
        self.import_button = QPushButton('从 TXT 导入账号密码…')
        self.import_button.clicked.connect(self.import_text)
        layout.addWidget(self.import_button)
        self.persist = QCheckBox('记住在这台电脑上，下次自动载入')
        self.persist.setChecked(True)
        layout.addWidget(self.persist)
        self.auto_login = QCheckBox('允许在官网要求登录时自动使用此账户')
        self.auto_login.setChecked(auto_login)
        self.trust_browser = QCheckBox('完成验证后，自动选择“信任此浏览器”')
        self.trust_browser.setChecked(trust_browser)
        self.trust_browser.setEnabled(auto_login)
        self.auto_login.toggled.connect(self.trust_browser.setEnabled)
        layout.addWidget(self.auto_login)
        layout.addWidget(self.trust_browser)
        help_text = QLabel('请提前在同一浏览器完成首次验证。Apple 仍可能再次要求验证码；\n软件会保留当前页面并提醒你，不会反复提交密码。')
        help_text.setWordWrap(True)
        help_text.setObjectName('muted')
        layout.addWidget(help_text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('使用此账户')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.accept_checked)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def import_text(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择本机账号 TXT', '', '文本文件 (*.txt)')
        if not path:
            return
        try:
            fields = read_private_text(path)
            if 'account' not in fields or 'password' not in fields:
                raise ConfigError('TXT 需包含两行：账号=你的账号、密码=你的密码')
            credential = AppleCredential(fields['account'], fields['password'])
            credential.validate()
            self.account.setText(credential.account)
            self.password.setText(credential.password)
        except ConfigError as exc:
            QMessageBox.information(self, 'TXT 导入', str(exc))

    def credential(self):
        account = self.account.text().strip()
        password = self.password.text()
        if not password and self.previous and account == self.previous.account:
            password = self.previous.password
        result = AppleCredential(account, password)
        result.validate()
        return result

    def accept_checked(self):
        try:
            self.credential()
            self.accept()
        except ConfigError as exc:
            QMessageBox.information(self, '请检查账户', str(exc))
