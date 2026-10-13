"""Durable direct inputs without accessing the native secret backend."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from uuid import uuid4

from cryptography.fernet import Fernet
import pytest

from app.models.config import Identity
from app.models.credential import AppleCredential
from app.models.flow import FlowError
from app.security.local_inputs import LocalInputs
from app.security.privacy import Redactor
from app.security.vault import CredentialVault, IdentityVault


def contact():
    return Identity('格式测试', '居民身份证后四位', '123X', '00000000000',
                    'format@example.com', '格', '式测试')


@pytest.fixture(autouse=True)
def no_native_and_no_old_cache(monkeypatch):
    import app.security.vault as module
    monkeypatch.setattr(module, 'credential_backend', lambda: pytest.fail('No native access allowed'))
    CredentialVault.clear_cache()
    yield
    CredentialVault.clear_cache()


def test_encrypted_inputs_restore_with_metadata_and_register_redaction(tmp_path):
    store = LocalInputs(tmp_path)
    account_id, profile_id, legacy_id = (str(uuid4()) for _ in range(3))
    account = AppleCredential('fixture@example.invalid', ' Test-Only=Secret ')
    store.put('account', account_id, account)
    store.put('profile', profile_id, contact(), alias='默认测试', source_id=legacy_id)
    for field in [account.account, account.password, contact().phone, contact().email, contact().document_number]:
        assert field.encode() not in store.path.read_bytes()
    if os.name != 'nt':
        assert store.root.stat().st_mode & 0o777 == 0o700
        assert store.path.stat().st_mode & 0o777 == 0o600
        assert store.key_path.stat().st_mode & 0o777 == 0o600
    redactor = Redactor()
    restored = LocalInputs(tmp_path)
    restored.restore(redactor)
    assert CredentialVault.cached(account_id) == account
    assert IdentityVault.cached(profile_id) == contact()
    assert not CredentialVault.is_temporary(account_id) and not IdentityVault.is_temporary(profile_id)
    assert restored.profiles() == {profile_id: {'id': profile_id, 'alias': '默认测试', 'source_id': legacy_id}}
    assert account.password not in redactor.clean(account.password)
    assert contact().document_number not in redactor.clean(contact().document_number)
    restored.remove(account_id)
    CredentialVault.clear_cache()
    reopened = LocalInputs(tmp_path)
    reopened.restore(Redactor())
    assert CredentialVault.cached(account_id) is None and IdentityVault.cached(profile_id) == contact()


@pytest.mark.parametrize('damage', ['ciphertext', 'missing-key', 'wrong-key', 'root-list', 'bad-record', 'bad-metadata'])
def test_unreadable_inputs_preserve_files_and_do_not_partially_restore_or_overwrite(tmp_path, damage):
    store = LocalInputs(tmp_path)
    account_id, profile_id = str(uuid4()), str(uuid4())
    store.put('account', account_id, AppleCredential('fixture@example.invalid', 'Private-Test-Secret'))
    if damage == 'ciphertext':
        store.path.write_bytes(b'corrupted Private-Test-Secret')
    elif damage == 'missing-key':
        store.key_path.unlink()
    elif damage == 'wrong-key':
        store.key_path.write_bytes(Fernet.generate_key())
    else:
        records = {account_id: store.records[account_id], profile_id: []}
        if damage == 'bad-metadata':
            records[profile_id] = {'kind': 'profile', 'record': asdict(contact()), 'alias': '测试', 'source_id': []}
        raw = [] if damage == 'root-list' else {'version': 1, 'records': records}
        store.path.write_bytes(Fernet(store.key_path.read_bytes()).encrypt(json.dumps(raw).encode()))
    before = store.path.read_bytes()
    restored = LocalInputs(tmp_path)
    with pytest.raises(FlowError, match='原文件已保留') as error:
        restored.restore(Redactor())
    assert 'Private-Test-Secret' not in str(error.value)
    assert not restored.records and CredentialVault.cached(account_id) is None
    with pytest.raises(FlowError, match='取消'):
        restored.put('account', str(uuid4()), AppleCredential('new@example.invalid', 'New-Test-Secret'))
    assert store.path.read_bytes() == before


def test_atomic_write_failure_keeps_previous_inputs_and_removes_its_temporary_file(tmp_path, monkeypatch):
    import app.utils.files as module
    store = LocalInputs(tmp_path)
    account_id = str(uuid4())
    account = AppleCredential('fixture@example.invalid', 'Original-Test-Secret')
    store.put('account', account_id, account)
    before = store.path.read_bytes()
    def denied(*_):
        raise OSError('Synthetic write failure')
    monkeypatch.setattr(module.os, 'replace', denied)
    with pytest.raises(FlowError, match='保存失败'):
        store.put('account', account_id, AppleCredential(account.account, 'Changed-Test-Secret'))
    assert store.path.read_bytes() == before and store.records[account_id]['record']['password'] == account.password
    assert not list(store.root.glob('*.tmp'))


def test_real_gui_processes_remember_edit_delete_and_session_only_choices(tmp_path):
    script = textwrap.dedent('''
        import sys
        from pathlib import Path
        from PySide6.QtWidgets import QApplication, QDialog, QFileDialog
        from shiboken6 import delete
        import app.security.vault as vaults
        from app.gui.account import AccountDialog
        from app.gui.profiles import ProfileDialog
        from app.gui.window import MainWindow
        from app.security.vault import CredentialVault, IdentityVault
        from app.utils.qt import prepare_plugins
        def forbidden():raise AssertionError('Native backend called')
        vaults.credential_backend=forbidden
        prepare_plugins()
        qt=QApplication(['durable-inputs-test'])
        root=Path(sys.argv[1]);phase=sys.argv[2]
        window=MainWindow(root)
        def fill_account(dialog):
            assert dialog.persist.isChecked()
            dialog.account.setText('fixture@example.invalid');dialog.password.setText('Durable-Test-Secret')
            return QDialog.DialogCode.Accepted
        def fill_profile(dialog):
            assert dialog.persist.isChecked()
            for key,value in {'alias':'默认测试','family_name':'格','given_name':'式测试',
                'document_number':'123X','phone':'00000000000','email':'format@example.com'}.items():
                dialog.fields[key].setText(value)
            return QDialog.DialogCode.Accepted
        try:
            if phase=='save':
                AccountDialog.exec=fill_account;ProfileDialog.exec=fill_profile
                window.configure_account();window.add_profile();window.save()
            elif phase=='reopen':
                assert CredentialVault.cached(window.config.credential_id).password=='Durable-Test-Secret'
                assert IdentityVault.cached(window.config.profile_id).document_number=='123X'
                assert window.config.auto_login and window.config.trust_browser and window.config.transmit_identity
                assert window.profiles[0]['alias']=='默认测试'
                assert '重开自动载入' in window.account_status.text()
                assert '本机已保存'==window.profile_table.item(0,2).text()
                assert next(i for i in window.preparation_for(window.config) if i.key=='identity').status=='ready'
                def edit_account(dialog):
                    assert not dialog.password.text() and dialog.credential().password=='Durable-Test-Secret'
                    dialog.auto_login.setChecked(False)
                    return QDialog.DialogCode.Accepted
                AccountDialog.exec=edit_account
                old=window.config.credential_id;window.configure_account()
                assert not window.local_inputs.has(old) and not window.config.auto_login
                def session_profile(dialog):
                    assert dialog.identity().document_number=='123X'
                    dialog.persist.setChecked(False)
                    return QDialog.DialogCode.Accepted
                ProfileDialog.exec=session_profile
                old=window.config.profile_id;window.profile_table.selectRow(0);window.edit_profile()
                assert not window.local_inputs.has(old) and IdentityVault.is_temporary(window.config.profile_id)
                assert not window.repo.load_config().profile_id
            else:
                assert CredentialVault.cached(window.config.credential_id).password=='Durable-Test-Secret'
                assert not window.config.auto_login and not window.config.profile_id and not window.profiles
                window.remove_account()
                assert not window.local_inputs.records and not window.repo.load_config().credential_id
            exported=root/'public.json'
            QFileDialog.getSaveFileName=lambda *_:(str(exported),'JSON')
            window.export_config()
            with window.repo.connect() as db:raw=db.execute('SELECT data FROM config').fetchone()[0]
            public=raw+exported.read_text(encoding='utf-8')
            for field in ('fixture@example.invalid','Durable-Test-Secret','format@example.com','00000000000','123X'):
                assert field not in public and field.encode() not in window.local_inputs.path.read_bytes()
        finally:
            window.shutdown();delete(window)
        print('LOCAL_INPUTS_OK:'+phase,flush=True)
    ''')
    env = {**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'AUTOBUYER_DATA_DIR': str(tmp_path)}
    for phase in ('save', 'reopen', 'remove'):
        result = subprocess.run([sys.executable, '-c', script, str(tmp_path), phase],
                                cwd=Path(__file__).resolve().parents[1], env=env,
                                capture_output=True, text=True, encoding='utf-8', timeout=30)
        assert result.returncode == 0, result.stdout + '\n' + result.stderr
        assert 'LOCAL_INPUTS_OK:' + phase in result.stdout
