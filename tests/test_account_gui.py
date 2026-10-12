import json
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QFileDialog, QDialog, QLineEdit

from app.gui.account import AccountDialog
from app.gui.window import MainWindow
from app.models.config import ConfigError
from app.models.credential import AppleCredential
from app.utils.qt import prepare_plugins


@pytest.fixture(scope='module')
def qt():
    prepare_plugins()
    app=QApplication.instance() or QApplication(['saved-account-test'])
    yield app


def test_account_dialog_masks_password_and_requires_new_password_for_a_different_account(qt):
    saved=AppleCredential('fixture@example.invalid','Test-Only-Secret')
    dialog=AccountDialog(credential=saved,auto_login=True,trust_browser=True)
    try:
        assert dialog.password.echoMode()==QLineEdit.EchoMode.Password
        assert dialog.password.text()==''
        assert dialog.credential()==saved
        dialog.account.setText('other@example.invalid')
        with pytest.raises(ConfigError,match='密码'):dialog.credential()
        dialog.password.setText(' New-Password-With-Spaces ')
        assert dialog.credential().password==' New-Password-With-Spaces '
    finally:dialog.close();dialog.deleteLater();qt.processEvents()


def test_new_account_permission_is_an_explicit_checkbox(qt):
    dialog=AccountDialog()
    try:
        assert not dialog.auto_login.isChecked() and not dialog.trust_browser.isEnabled()
        dialog.auto_login.click()
        assert dialog.trust_browser.isEnabled() and not dialog.trust_browser.isChecked()
    finally:dialog.close();dialog.deleteLater();qt.processEvents()


def test_daily_chrome_button_selects_connection_and_checks_login(qt,tmp_path,monkeypatch):
    window=MainWindow(tmp_path)
    calls=[]
    try:
        before=window.repo.load_config().browser_kind
        monkeypatch.setattr(window,'launch',lambda action:calls.append((action,window.browser_kind.currentData())))
        window.connect_daily_chrome()
        assert calls==[('login_check','daily_chrome')]
        assert window.repo.load_config().browser_kind==before
    finally:window.close();window.deleteLater();qt.processEvents()


def test_gui_saves_credentials_to_vault_and_only_reference_to_sqlite(qt,tmp_path,monkeypatch):
    import app.gui.window as module
    stored={}
    vault=SimpleNamespace(put=lambda key,credential:stored.update({key:credential}),
                          get=lambda key:stored[key],delete=lambda key:stored.pop(key))
    monkeypatch.setattr(module,'CredentialVault',lambda:vault)
    def accept(dialog):
        dialog.account.setText('fixture@example.invalid');dialog.password.setText('Test-Only-Secret')
        dialog.auto_login.click();dialog.trust_browser.click()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(AccountDialog,'exec',accept)
    window=MainWindow(tmp_path)
    try:
        assert not stored
        window.configure_account()
        assert len(stored)==1 and window.config.auto_login and window.config.trust_browser
        config=window.repo.load_config();assert config.credential_id in stored
        with window.repo.connect() as db:raw=db.execute('SELECT data FROM config').fetchone()[0]
        assert 'fixture@example.invalid' not in raw and 'Test-Only-Secret' not in raw
        assert '自动登录已开启' in window.account_status.text()
        assert 'Test-Only-Secret' not in window.redactor.clean('Test-Only-Secret')
        first=window.config.credential_id;window.configure_account()
        assert window.config.credential_id!=first and first not in stored and len(stored)==1
    finally:window.shutdown();window.close();window.deleteLater();qt.processEvents()


def test_import_cannot_grant_credential_or_trust_authority(qt,tmp_path,monkeypatch):
    from uuid import uuid4
    window=MainWindow(tmp_path)
    raw=window.config.to_dict();raw.update(credential_id=str(uuid4()),auto_login=True,trust_browser=True)
    path=tmp_path/'public-config.json';path.write_text(json.dumps(raw))
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *_:(str(path),'JSON'))
    try:
        window.import_config()
        assert not window.config.credential_id and not window.config.auto_login and not window.config.trust_browser
        assert not window.repo.load_config().credential_id
    finally:window.shutdown();window.close();window.deleteLater();qt.processEvents()
