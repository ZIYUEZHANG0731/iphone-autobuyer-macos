"""Control fixtures run everywhere; native API evidence is Windows-only."""
import io
from importlib.util import find_spec
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

import app.platforms as platforms
from app.platforms import files, runtime, windows
from app.services.chrome_session import parse_owned_chrome
from app.services.environment import check_environment
from app.utils.files import write_private_json


def test_windows_recovery_compares_native_paths_and_rejects_similar_profiles(monkeypatch):
    monkeypatch.setattr(platforms, 'sys', SimpleNamespace(platform='win32'))
    exe = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
    profile = r'C:\Users\测试用户\AppData\Local\Apple AutoBuyer\chrome-profile'
    process = {'pid': 42, 'username': r'PC\测试用户', 'exe': exe.upper(),
               'cmdline': [exe, '--user-data-dir', profile.upper().replace('\\', '/'), '--remote-debugging-port=9222']}
    assert parse_owned_chrome([process], exe, profile, username=r'PC\测试用户')[0].port == 9222
    for replacement in (profile + '-other', profile + '\\..\\daily-profile', ''):
        changed = {**process, 'cmdline': [exe, '--user-data-dir=' + replacement, '--remote-debugging-port=9222']}
        assert not parse_owned_chrome([changed], exe, profile, username=r'PC\测试用户')
    duplicate = {**process, 'cmdline': process['cmdline'] + ['--user-data-dir=' + profile]}
    assert not parse_owned_chrome([duplicate], exe, profile, username=r'PC\测试用户')
    assert not parse_owned_chrome([process], exe, profile, username='other-user')


def test_windows_chrome_can_use_registered_nonstandard_install(tmp_path, monkeypatch):
    monkeypatch.setattr(platforms, 'sys', SimpleNamespace(platform='win32'))
    registered = Path(r'D:\软件 Chrome\chrome.exe')
    monkeypatch.setattr(Path, 'is_file', lambda path: path == registered)
    monkeypatch.setattr(platforms, 'chrome_locations', lambda: ([], tmp_path))
    monkeypatch.setattr(windows, 'chrome_app_paths', lambda: ['relative.exe', str(registered)])
    assert platforms.installed_chrome() == registered


def test_empty_localappdata_uses_home_fallback(monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', '')
    assert platforms.local_app_data() == Path.home() / 'AppData' / 'Local'


def test_relative_data_override_becomes_absolute_without_touching_default(tmp_path, monkeypatch):
    from app.utils.paths import data_dir
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('AUTOBUYER_DATA_DIR', '中文 空格')
    assert data_dir() == tmp_path / '中文 空格'


def test_window_fits_logical_work_area_at_high_scaling():
    changes = {}
    area = SimpleNamespace(width=lambda: 960, height=lambda: 510)
    window = SimpleNamespace(screen=lambda: SimpleNamespace(availableGeometry=lambda: area),
        setMinimumSize=lambda w, h: changes.update(minimum=(w, h)),
        resize=lambda w, h: changes.update(size=(w, h)))
    runtime.fit_window(window)
    assert changes == {'minimum': (936, 486), 'size': (936, 486)}


@pytest.mark.skipif(find_spec('tzdata') is None,
                    reason='Windows-only dependency tzdata is not installed')
def test_beijing_timezone_works_without_system_zone_database():
    result = subprocess.run([sys.executable, '-c',
        "from datetime import datetime; from zoneinfo import ZoneInfo; print(datetime(2026,10,16,20,tzinfo=ZoneInfo('Asia/Shanghai')).isoformat())"],
        env={**os.environ, 'PYTHONTZPATH': ''}, capture_output=True, text=True, encoding='utf-8', timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == '2026-10-16T20:00:00+08:00'


def test_windows_private_paths_dispatch_to_acl_and_propagate_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(files, 'sys', SimpleNamespace(platform='win32'))
    called = []
    monkeypatch.setattr(windows, 'protect_path', lambda path: called.append(path))
    root = files.private_directory(tmp_path / '中文 空格')
    assert called == [root]
    def failed(_):raise OSError('Synthetic ACL failure')
    monkeypatch.setattr(windows, 'protect_path', failed)
    with pytest.raises(OSError):files.protect_path(root)
    assert windows.private_sddl('S-1-5-21-123', True) == (
        'D:P(A;OICI;FA;;;S-1-5-21-123)(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)')
    assert 'OICI' not in windows.private_sddl('S-1-5-21-123', False)


def test_private_directory_rejects_junctions_before_creating_or_chmod(tmp_path, monkeypatch):
    monkeypatch.setattr(files, 'is_link', lambda _: True)
    root = tmp_path / 'junction-fixture'
    with pytest.raises(OSError):files.private_directory(root)
    assert not root.exists()


def test_atomic_private_write_failure_keeps_old_file(tmp_path, monkeypatch):
    target = tmp_path / '中文 空格.json'
    write_private_json(target, {'before': '合成'})
    previous = target.read_bytes()
    monkeypatch.setattr('app.utils.files.protect_path', lambda _: (_ for _ in ()).throw(OSError('ACL')))
    with pytest.raises(OSError):write_private_json(target, {'after': '不能写入'})
    assert target.read_bytes() == previous
    assert not list(tmp_path.glob('*.tmp'))


def test_windows_console_reconfiguration_handles_windowed_no_console(monkeypatch):
    output = io.TextIOWrapper(io.BytesIO(), encoding='cp1252')
    monkeypatch.setattr(runtime, 'sys', SimpleNamespace(platform='win32', stdout=output, stderr=None))
    runtime.prepare_stdio()
    output.write('中文日志');output.flush()
    assert output.buffer.getvalue().decode('utf-8') == '中文日志'


def test_offline_environment_reports_missing_resources_as_failure(monkeypatch, tmp_path):
    monkeypatch.setattr('app.services.environment.resource_path', lambda name: tmp_path / name)
    result = check_environment()
    assert not result['ok']
    assert not next(item for item in result['checks'] if item['name'] == 'services/official_stores.json')['ok']


@pytest.mark.skipif(sys.platform != 'win32', reason='Native Windows DACL; control fixtures do not validate this API')
def test_native_windows_acl_protects_unicode_directory_and_inherited_file(tmp_path):
    root = files.private_directory(tmp_path / '中文 用户 空格')
    child = root / '合成.txt';child.write_text('synthetic only', encoding='utf-8')
    # Read ACL via an independent Windows API client, not our setter.
    # Python launched from PowerShell 7 inherits its module search path. Native
    # Windows PowerShell must resolve its own Security module for Get-Acl.
    env = {key: value for key, value in os.environ.items() if key.upper() != 'PSMODULEPATH'}
    env['AUTOBUYER_ACL_FIXTURE'] = str(child)
    result = subprocess.run(['powershell.exe', '-NoProfile', '-Command',
        "$a=Get-Acl -LiteralPath $env:AUTOBUYER_ACL_FIXTURE; $a.Sddl"],
        env=env, capture_output=True, text=True, encoding='utf-8', timeout=15)
    assert result.returncode == 0, result.stderr
    sddl = result.stdout.strip()
    assert windows.current_user_sid() in sddl and ';;;SY)' in sddl and ';;;BA)' in sddl
    assert all(';;;' + sid + ')' not in sddl for sid in ('WD', 'BU', 'AU'))


def test_process_crash_releases_only_runtime_lock_and_preserves_unknown_order(tmp_path):
    from app.models.flow import FlowError
    from app.platforms.locking import ProcessLock
    from app.services.storage import Repository
    root = tmp_path / '崩溃 空格';root.mkdir()
    repo = Repository(root / 'autobuyer.sqlite3')
    repo.claim_submission('synthetic-intent', 'synthetic-run')
    repo.record_submission('synthetic-intent', 'UNKNOWN')
    code = "from pathlib import Path; import sys, time; from app.platforms.locking import ProcessLock; lock=ProcessLock(Path(sys.argv[1])); lock.__enter__(); print('LOCKED',flush=True); time.sleep(30)"
    process = subprocess.Popen([sys.executable, '-c', code, str(root / 'session.lock')],
                               stdout=subprocess.PIPE, text=True, encoding='utf-8')
    def terminate_fixture():
        if sys.platform == 'win32':
            # A Windows venv redirector may spawn the real Python process.
            # Killing only the launcher leaves that lock holder alive.
            subprocess.run(['taskkill.exe', '/PID', str(process.pid), '/T', '/F'],
                           capture_output=True, timeout=10, check=True)
        else:
            process.kill()
        process.wait(timeout=10)
    try:
        assert process.stdout.readline().strip() == 'LOCKED'
        with pytest.raises(FlowError):
            with ProcessLock(root / 'session.lock'):pass
        terminate_fixture()
        with ProcessLock(root / 'session.lock'):pass
        reopened = Repository(repo.path)
        assert reopened.submission('synthetic-intent')['status'] == 'UNKNOWN'
        assert not reopened.claim_submission('synthetic-intent', 'synthetic-restart')
    finally:
        if process.poll() is None:terminate_fixture()
        process.stdout.close()
