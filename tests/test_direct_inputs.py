"""Direct input/import must work with the native secret backend unavailable."""
from dataclasses import dataclass
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog

from app.gui.account import AccountDialog
from app.gui.profiles import ProfileDialog
from app.gui.window import MainWindow
from app.gui.worker import Worker
from app.models.config import ConfigError, Identity, Settings
from app.models.credential import AppleCredential
from app.models.flow import UserAction
from app.security.vault import CredentialVault, IdentityVault
from app.services.private_text import read_private_text
from app.services.storage import Repository
from app.utils.qt import prepare_plugins


@pytest.fixture(scope='module')
def qt():
    prepare_plugins()
    application=QApplication.instance() or QApplication(['direct-input-test'])
    yield application


@pytest.fixture
def no_native(monkeypatch):
    import app.security.vault as module
    monkeypatch.setattr(module,'credential_backend',lambda:pytest.fail('No native access allowed'))


def fill_profile(dialog):
    for key,value in {'alias':'格式测试','family_name':'格','given_name':'式测试',
        'document_number':'123X','phone':'00000000000','email':'format@example.com'}.items():
        dialog.fields[key].setText(value)
    return QDialog.DialogCode.Accepted


def test_direct_pickup_entry_and_edit_never_write_or_unlock_native_storage(qt,tmp_path,monkeypatch,no_native):
    monkeypatch.setattr(ProfileDialog,'exec',fill_profile)
    window=MainWindow(tmp_path);references=[]
    try:
        window.add_profile();references.append(window.config.profile_id)
        assert window.config.transmit_identity and IdentityVault.is_temporary(references[-1])
        assert IdentityVault.cached(references[-1]).document_number=='123X'
        assert window.repo.profiles()==[] and window.profiles[0]['id']==references[-1]
        window.save();stored=window.repo.load_config()
        assert not stored.profile_id and not stored.transmit_identity
        assert next(i for i in window.preparation_for(window.config) if i.key=='identity').status=='ready'
        window.profile_table.selectRow(0);window.edit_profile();references.append(window.config.profile_id)
        assert len(window.profiles)==1 and IdentityVault.cached(references[0]) is None
        with window.repo.connect() as db:raw=db.execute('SELECT data FROM config').fetchone()[0]
        assert all(value not in raw for value in references+['format@example.com','00000000000','123X'])
        window.profile_table.selectRow(0);window.delete_profile()
        assert not window.config.profile_id and not window.config.transmit_identity and not window.profiles
    finally:
        for reference in references:IdentityVault.forget(reference)
        window.shutdown();window.close();window.deleteLater();qt.processEvents()


def test_refilling_an_old_profile_preserves_legacy_records_and_restart_settings(qt,tmp_path,monkeypatch,no_native):
    legacy=str(uuid4());window=MainWindow(tmp_path);reference=''
    window.repo.save_alias(legacy,'旧资料');window.config.profile_id=legacy;window.config.transmit_identity=True
    window.repo.save_config(window.config);window.refresh_profiles()
    monkeypatch.setattr(ProfileDialog,'exec',fill_profile)
    try:
        assert next(i for i in window.preparation_for(window.config) if i.key=='identity').status=='missing'
        window.profile_table.selectRow(0);window.edit_profile();reference=window.config.profile_id
        assert reference!=legacy and IdentityVault.cached(reference).email=='format@example.com'
        assert len(window.profiles)==1 and window.repo.profiles()==[{'id':legacy,'alias':'旧资料'}]
        stored=window.repo.load_config();assert stored.profile_id==legacy and stored.transmit_identity
        exported=tmp_path/'config.json'
        monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *_:(str(exported),'JSON'))
        window.export_config()
        assert reference not in exported.read_text() and 'format@example.com' not in exported.read_text()
        window.profile_table.selectRow(0);window.delete_profile()
        assert window.config.profile_id==legacy and IdentityVault.cached(reference) is None
        assert window.profiles==[{'id':legacy,'alias':'旧资料'}]
    finally:
        if reference:IdentityVault.forget(reference)
        window.shutdown();window.close();window.deleteLater();qt.processEvents()


def test_combined_txt_populates_only_the_requested_form_without_native_access(qt,tmp_path,monkeypatch,no_native):
    path=tmp_path/'input.txt'
    path.write_text('账号=fixture@example.invalid\n密码= Test=Only:Secret \n姓氏=格\n名字=式测试\n证件后四位=123X\n手机号=00000000000\n邮箱=format@example.com\n',encoding='utf-8-sig')
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *_:(str(path),'TXT'))
    account=AccountDialog();profile=ProfileDialog()
    try:
        account.import_text();profile.import_text()
        assert account.credential()==AppleCredential('fixture@example.invalid',' Test=Only:Secret ')
        assert profile.identity().document_number=='123X';profile.identity().validate()
        assert profile.fields['email'].text()=='format@example.com'
        assert account.result()==QDialog.DialogCode.Rejected and profile.result()==QDialog.DialogCode.Rejected
    finally:
        account.close();profile.close();account.deleteLater();profile.deleteLater();qt.processEvents()


@pytest.mark.parametrize('text',[
    '账号=a\naccount=b\n密码=Synthetic-secret',
    '错误字段=Private-Value',
    '密码=Private-Value\n没有分隔符',
    '',
])
def test_invalid_txt_errors_never_echo_field_contents(tmp_path,text):
    path=tmp_path/'bad.txt';path.write_text(text,encoding='utf-8')
    with pytest.raises(ConfigError) as error:read_private_text(path)
    assert 'Private-Value' not in str(error.value) and 'Synthetic-secret' not in str(error.value)


def test_txt_size_and_encoding_are_bounded_without_logging_raw_text(tmp_path):
    path=tmp_path/'large.txt';path.write_bytes(b'x'*16385)
    with pytest.raises(ConfigError,match='过大'):read_private_text(path)
    path.write_bytes(b'\xff')
    with pytest.raises(ConfigError,match='UTF-8'):read_private_text(path)


async def test_workflow_uses_session_identity_and_credential_without_a_native_read(qt,tmp_path,monkeypatch,no_native):
    import app.gui.worker as module
    identity_ref=str(uuid4());credential_ref=str(uuid4())
    identity=Identity('格式测试','居民身份证后四位','123X','00000000000','format@example.com','格','式测试')
    IdentityVault.remember(identity_ref,identity)
    CredentialVault.remember(credential_ref,AppleCredential('fixture@example.invalid','Synthetic-secret'))
    config=Settings(profile_id=identity_ref,credential_id=credential_ref,auto_login=True,transmit_identity=True)
    captured={}
    class Adapter:
        def __init__(self,page,control,**kwargs):captured.update(kwargs)
        def detach(self):pass
    @dataclass
    class Result:
        state:str='COMPLETED'
    class Engine:
        def __init__(self,*args,**kwargs):pass
        async def run(self,config,adapter,mode,record):captured['identity']=record;return Result()
    monkeypatch.setattr(module,'AppleAdapter',Adapter);monkeypatch.setattr(module,'Engine',Engine)
    worker=Worker('website',config,tmp_path,interactive=False)
    try:
        result=await worker.browser_action(SimpleNamespace(page=object()),Repository(tmp_path/'db.sqlite'))
        assert result['state']=='COMPLETED' and captured['identity']==identity
        assert captured['credential_provider'](credential_ref).password=='Synthetic-secret'
        with pytest.raises(UserAction,match='不读取钥匙串'):captured['credential_provider'](str(uuid4()))
        worker.config.profile_id=str(uuid4())
        with pytest.raises(UserAction,match='不会读取旧钥匙串'):await worker.browser_action(SimpleNamespace(page=object()),Repository(tmp_path/'db.sqlite'))
    finally:
        IdentityVault.forget(identity_ref);CredentialVault.forget(credential_ref);worker.deleteLater();qt.processEvents()
