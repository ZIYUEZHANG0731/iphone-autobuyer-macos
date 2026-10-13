"""Offline dependency/resource checks, also usable inside a frozen build."""
import importlib
from pathlib import Path
import struct
import sys

from app import __version__, __build__
from app.platforms import installed_chrome, system_label
from app.utils.paths import resource_path


def check_environment():
    checks = []

    def check(name, action):
        try:
            ok = bool(action())
        except Exception:
            ok = False
        checks.append({'name': name, 'ok': ok})

    check('python_3_12_plus', lambda: sys.version_info >= (3, 12))
    check('64_bit', lambda: struct.calcsize('P') == 8)
    for module in ('PySide6.QtWidgets', 'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
                   'playwright.async_api', 'portalocker', 'psutil', 'keyring', 'cryptography.fernet'):
        check(module, lambda name=module: importlib.import_module(name))

    def encryption():
        from cryptography.fernet import Fernet
        cipher = Fernet(Fernet.generate_key())
        return cipher.decrypt(cipher.encrypt(b'synthetic-only')) == b'synthetic-only'

    def timezone():
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return datetime(2026, 10, 16, 20, tzinfo=ZoneInfo('Asia/Shanghai')).utcoffset().total_seconds() == 28800

    check('fernet_roundtrip', encryption)
    check('Asia/Shanghai', timezone)
    for name in ('gui/theme.qss', 'services/sim_store.html', 'services/official_catalog.json',
                 'services/official_stores.json', 'services/daily_chrome_bridge.cjs'):
        check(name, lambda value=name: resource_path(value).is_file())

    def driver():
        from playwright._impl._driver import compute_driver_executable
        node, cli = compute_driver_executable()
        return Path(node).is_file() and Path(cli).is_file()

    check('playwright_driver', driver)
    if sys.platform == 'win32':
        def qt_file(name):
            import PySide6
            return any(Path(PySide6.__file__).parent.rglob(name))
        for name in ('QtWebEngineProcess.exe', 'qwindows.dll', 'icudtl.dat',
                     'qtwebengine_resources.pak', 'qtwebengine_resources_100p.pak',
                     'qtwebengine_resources_200p.pak', 'v8_context_snapshot.bin', 'zh-CN.pak'):
            check(name, lambda value=name: qt_file(value))
        check('tzdata_package', lambda: importlib.import_module('tzdata'))
    try:chrome_available = installed_chrome() is not None
    except OSError:chrome_available = False
    return {'ok': all(item['ok'] for item in checks), 'version': __version__, 'build': __build__,
            'system': system_label(), 'frozen': bool(getattr(sys, 'frozen', False)),
            'chrome_available': chrome_available, 'checks': checks}
