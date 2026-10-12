import json
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QFileDialog, QDialog, QLineEdit, QPushButton, QWidget

from app.gui.account import AccountDialog
from app.gui.window import MainWindow
from app.automation.control import Control
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


def test_direct_account_entry_defaults_to_session_memory_and_auto_login(qt):
    dialog=AccountDialog()
    try:
        assert dialog.auto_login.isChecked() and dialog.trust_browser.isEnabled()
        assert dialog.trust_browser.isChecked() and not dialog.persist.isChecked()
        dialog.auto_login.click()
        assert not dialog.trust_browser.isEnabled()
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
    import app.gui.browser_controls as module
    stored={}
    vault=SimpleNamespace(put=lambda key,credential:stored.update({key:credential}),
                          cached=lambda key:stored.get(key),is_temporary=lambda key:False,
                          delete=lambda key:stored.pop(key))
    monkeypatch.setattr(module,'CredentialVault',lambda:vault)
    def accept(dialog):
        dialog.account.setText('fixture@example.invalid');dialog.password.setText('Test-Only-Secret')
        dialog.auto_login.setChecked(True);dialog.trust_browser.setChecked(True);dialog.persist.setChecked(True)
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


def test_direct_account_entry_never_reads_native_storage_and_session_reference_is_not_saved(qt,tmp_path,monkeypatch):
    from app.security.vault import CredentialVault
    import app.security.vault as vaults
    monkeypatch.setattr(vaults,'credential_backend',lambda:pytest.fail('Session entry must not open secure storage'))
    def accept(dialog):
        dialog.account.setText('session@example.invalid');dialog.password.setText('Session-Only-Test-Secret')
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(AccountDialog,'exec',accept)
    window=MainWindow(tmp_path)
    reference=''
    try:
        window.configure_account();reference=window.config.credential_id
        assert CredentialVault.is_temporary(reference)
        assert CredentialVault().get(reference).password=='Session-Only-Test-Secret'
        assert window.config.auto_login and window.config.trust_browser
        window.save()
        stored=window.repo.load_config()
        assert not stored.credential_id and not stored.auto_login and not stored.trust_browser
        with window.repo.connect() as db:raw=db.execute('SELECT data FROM config').fetchone()[0]
        assert reference not in raw and 'Session-Only-Test-Secret' not in raw and 'session@example.invalid' not in raw
        assert '无需安全存储密码' in window.account_status.text()
    finally:
        if reference:CredentialVault.forget(reference)
        window.shutdown();window.close();window.deleteLater();qt.processEvents()


def test_opening_account_editor_does_not_unlock_an_existing_uncached_account(qt,tmp_path,monkeypatch):
    from uuid import uuid4
    from app.security.vault import CredentialVault
    monkeypatch.setattr(CredentialVault,'get',lambda *_:pytest.fail('Opening an editor must not unlock credentials'))
    monkeypatch.setattr(AccountDialog,'exec',lambda dialog:QDialog.DialogCode.Rejected)
    window=MainWindow(tmp_path)
    try:
        window.config.credential_id=str(uuid4());window.config.auto_login=True
        window.configure_account()
        assert window.config.credential_id
    finally:window.shutdown();window.close();window.deleteLater();qt.processEvents()


def test_gui_chooses_published_sku_without_a_link_input(qt,tmp_path):
    window=MainWindow(tmp_path)
    try:
        window.name.setCurrentIndex(window.name.findText('iPhone Duo'))
        choices=window.product_choices
        color=choices.controls['dimensionColor'];color.setCurrentIndex(color.findData('nightsky'))
        capacity=choices.controls['dimensionCapacity'];capacity.setCurrentIndex(capacity.findData('512gb'))
        config=window.collect()
        assert config.product.url=='https://www.apple.com.cn/shop/buy-iphone/iphone-duo/mk2q4ch/a'
        assert config.product.max_total=='17999.00' and 'SKU 直达' in choices.note.text()
        assert window.url.isHidden()
        choices.offer['variants']=[]
        assert window.collect().product.url=='https://www.apple.com.cn/shop/buy-iphone/iphone-duo'
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


@pytest.mark.parametrize('action,page,label',[
    ('login',4,'检查登录状态'),
    ('login',6,'检查登录状态'),
    ('login_diagnosis',6,'已完成登录，检查'),
])
def test_login_check_buttons_resume_the_existing_paused_signin(qt,tmp_path,action,page,label):
    window=MainWindow(tmp_path)
    control=Control()
    window.worker=SimpleNamespace(isRunning=lambda:True,idle=False,action=action,control=control)
    try:
        window.stack.setCurrentIndex(page);window.last_state='PAUSED'
        button=next(button for button in window.stack.widget(page).findChildren(QPushButton) if button.text()==label)
        button.click()
        assert control.resume_event.is_set()
        assert '正在核验' in window.message_label.text()
        assert not window.resume_button.isEnabled()
    finally:
        window.worker=None;window.close();window.deleteLater();qt.processEvents()


@pytest.mark.parametrize('action',['website','live'])
def test_login_check_cannot_resume_a_paused_purchase_or_consent(qt,tmp_path,action):
    window=MainWindow(tmp_path)
    control=Control()
    window.worker=SimpleNamespace(isRunning=lambda:True,idle=False,action=action,control=control)
    try:
        window.last_state='PAUSED'
        window.launch('login_check');window.complete_browser_login()
        assert not control.resume_event.is_set()
        assert '已有浏览器任务' in window.message_label.text()
    finally:
        window.worker=None;window.close();window.deleteLater();qt.processEvents()


def test_paused_login_check_keeps_the_current_site_wait(qt,tmp_path):
    import time
    window=MainWindow(tmp_path)
    control=Control()
    window.worker=SimpleNamespace(isRunning=lambda:True,idle=False,action='login',control=control)
    try:
        window.last_state='PAUSED';window.cooldown_until=time.monotonic()+30
        window.complete_browser_login()
        assert not control.resume_event.is_set()
        assert '官网访问限制仍需等待' in window.message_label.text()
    finally:
        window.worker=None;window.close();window.deleteLater();qt.processEvents()


def test_switching_browser_hides_the_previous_embedded_page_without_destroying_it(qt,tmp_path):
    window=MainWindow(tmp_path)
    previous=QWidget(window.browser_holder)
    window.embedded_browser=previous
    window.browser_holder_layout.addWidget(previous)
    try:
        previous.show()
        window.on_data('browser',{'kind':'chrome','name':'Google Chrome（软件独立会话）'})
        assert previous.isHidden() and not window.browser_placeholder.isHidden()
        assert '软件独立 Chrome' in window.browser_placeholder.text()
        assert window.browser_holder_layout.indexOf(previous)>=0
        window.on_data('browser',{'kind':'embedded','name':'内置浏览器'})
        assert not previous.isHidden() and window.browser_placeholder.isHidden()
        assert window.embedded_browser is previous
    finally:
        window.embedded_browser=None;window.close();window.deleteLater();qt.processEvents()


def test_login_confirmation_time_is_shown_in_beijing_and_observation_keeps_utc(qt,tmp_path):
    window=MainWindow(tmp_path)
    try:
        observation={'authenticated':True,'checked_at':'2026-10-12T17:34:29+00:00'}
        window.on_data('login',observation)
        assert window.login_label.text()=='登录已确认 · 北京时间 10-13 01:34:29'
        assert window.login_observation==observation
    finally:window.close();window.deleteLater();qt.processEvents()
