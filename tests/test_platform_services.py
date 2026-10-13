"""Platform branches are simulated; native Windows execution remains separate."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from keyring.errors import PasswordDeleteError
import pytest

import app.platforms as platforms
import app.platforms.windows as windows
import app.security.vault as vaults
import app.platforms.power as power
from app.models.config import Identity
from app.models.credential import AppleCredential
from app.models.flow import FlowError
from app.platforms.locking import ProcessLock
from app.utils.files import write_private_json


def test_macos_update_keeps_existing_data_and_browser_locations(monkeypatch):
    monkeypatch.setattr(platforms, 'sys', SimpleNamespace(platform='darwin'))
    assert platforms.default_data_dir() == Path.home()/'Library/Application Support/Apple AutoBuyer'
    assert platforms.browser_cache_dir() == Path.home()/'Library/Caches/ms-playwright'
    locations, metadata = platforms.chrome_locations()
    assert locations[0] == Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
    assert metadata == Path.home()/'Library/Application Support/Google/Chrome'


def test_windows_paths_use_local_app_data_and_actual_chrome_installs(tmp_path, monkeypatch):
    monkeypatch.setattr(platforms, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path/'local'))
    monkeypatch.setenv('PROGRAMFILES', str(tmp_path/'programs'))
    monkeypatch.setenv('PROGRAMFILES(X86)', str(tmp_path/'programs-x86'))
    monkeypatch.setattr(windows, 'chrome_app_paths', lambda: [])
    assert platforms.default_data_dir() == tmp_path/'local/Apple AutoBuyer'
    assert platforms.browser_cache_dir() == tmp_path/'local/ms-playwright'
    locations, metadata = platforms.chrome_locations()
    assert metadata == tmp_path/'local/Google/Chrome/User Data'
    assert not platforms.installed_chrome()
    locations[1].parent.mkdir(parents=True)
    locations[1].touch()
    assert platforms.installed_chrome() == locations[1]


@pytest.mark.parametrize('system,module_name,class_name', [
    ('darwin','keyring.backends.macOS','Keyring'),
    ('win32','keyring.backends.Windows','WinVaultKeyring'),
])
def test_vault_selects_only_the_explicit_native_backend(monkeypatch, system, module_name, class_name):
    sentinel = object()
    monkeypatch.setattr(platforms, 'sys', SimpleNamespace(platform=system))
    monkeypatch.setitem(sys.modules, module_name, SimpleNamespace(**{class_name:lambda:sentinel}))
    assert platforms.credential_backend() is sentinel
    def failed():raise RuntimeError('private backend detail')
    monkeypatch.setitem(sys.modules, module_name, SimpleNamespace(**{class_name:failed}))
    with pytest.raises(FlowError) as error:platforms.credential_backend()
    assert 'private backend detail' not in str(error.value)


def test_unsupported_secure_store_does_not_fall_back_to_plaintext(monkeypatch):
    monkeypatch.setattr(platforms, 'sys', SimpleNamespace(platform='unsupported'))
    with pytest.raises(FlowError):platforms.credential_backend()


@pytest.mark.parametrize('kind,record,service', [
    (vaults.CredentialVault,AppleCredential('fixture@example.invalid','Synthetic-only!'),
     'com.autobuyer.apple.credentials'),
    (vaults.IdentityVault,Identity('测试取货人','居民身份证后四位','123X','00000000000','vault-fixture@example.com'),
     'com.autobuyer.apple.identities'),
])
def test_shared_vault_preserves_record_types_services_and_error_privacy(monkeypatch, kind, record, service):
    stored = {}
    def delete(service,key):
        if (service,key) not in stored:raise PasswordDeleteError('private backend detail')
        stored.pop((service,key))
    backend = SimpleNamespace(set_password=lambda service,key,value:stored.update({(service,key):value}),
        get_password=lambda service,key:stored.get((service,key)), delete_password=delete)
    monkeypatch.setattr(vaults,'credential_backend',lambda:backend)
    vault=kind();vault.put('synthetic-ref',record)
    assert vault.service == service and vault.get('synthetic-ref') == record
    vault.delete('synthetic-ref')
    with pytest.raises(FlowError,match='无法读取保存'):vault.get('synthetic-ref')
    if kind is vaults.CredentialVault:vault.delete('missing')
    else:
        with pytest.raises(FlowError) as error:vault.delete('missing')
        assert 'private backend detail' not in str(error.value)


def test_process_lock_contends_between_processes_and_releases_after_error(tmp_path):
    path=tmp_path/'task.lock'
    script='''import sys
from pathlib import Path
from app.platforms.locking import ProcessLock
from app.models.flow import FlowError
try:
    with ProcessLock(Path(sys.argv[1])):print('ACQUIRED')
except FlowError:print('BUSY')
'''
    def probe():
        result=subprocess.run([sys.executable,'-c',script,str(path)],text=True,encoding='utf-8',capture_output=True,
                              cwd=Path(__file__).resolve().parents[1],timeout=10)
        assert result.returncode==0,result.stderr
        return result.stdout.strip()
    with pytest.raises(RuntimeError):
        with ProcessLock(path):
            assert probe()=='BUSY'
            raise RuntimeError('fixture failure')
    assert probe()=='ACQUIRED'


def test_private_json_failure_retains_previous_record_and_cleans_temporary_file(tmp_path):
    path=tmp_path/'preferences.json'
    write_private_json(path,{'保留':1})
    previous=path.read_bytes()
    with pytest.raises(TypeError):write_private_json(path,{'invalid':object()})
    assert path.read_bytes()==previous and not list(tmp_path.glob('*.tmp'))
    write_private_json(path,{'更新':2})
    assert json.loads(path.read_text(encoding='utf-8'))=={'更新':2}
    if os.name!='nt':assert path.stat().st_mode & 0o777 == 0o600


async def test_windows_keep_awake_restores_previous_thread_state_even_on_failure(monkeypatch):
    calls=[]
    monkeypatch.setattr(power,'sys',SimpleNamespace(platform='win32'))
    monkeypatch.setattr(power,'windows_execution_state',lambda flags:calls.append(flags) or 0x80000002)
    with pytest.raises(RuntimeError):
        async with power.KeepAwake():raise RuntimeError('fixture failure')
    assert calls==[0x80000001,0x80000002]
    async with power.KeepAwake(enabled=False):pass
    assert len(calls)==2
