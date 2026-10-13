"""Operating-system choices shared by the GUI, storage and browser runtime."""
from __future__ import annotations

import os
import ntpath
import platform
from pathlib import Path
import sys

from app.models.flow import FlowError


def local_app_data() -> Path:
    return Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')


def path_key(value) -> str:
    """Compare equivalent native paths without losing Chinese characters."""
    if sys.platform == 'win32':
        native = str(Path(value).resolve()) if os.name == 'nt' else str(value)
        return ntpath.normcase(ntpath.normpath(native))
    return str(Path(value).resolve())


def default_data_dir() -> Path:
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'Apple AutoBuyer'
    if sys.platform == 'win32':
        return local_app_data() / 'Apple AutoBuyer'
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'Apple AutoBuyer'


def browser_cache_dir() -> Path:
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Caches/ms-playwright'
    if sys.platform == 'win32':
        return local_app_data() / 'ms-playwright'
    return Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'ms-playwright'


def chrome_locations() -> tuple[list[Path], Path]:
    """Known install locations and metadata root; never read Chrome cookies."""
    if sys.platform == 'darwin':
        relative = Path('Google Chrome.app/Contents/MacOS/Google Chrome')
        return ([root / relative for root in (Path('/Applications'), Path.home() / 'Applications')],
                Path.home() / 'Library/Application Support/Google/Chrome')
    if sys.platform == 'win32':
        roots = [local_app_data(), *[Path(os.environ[key]) for key in ('PROGRAMFILES', 'PROGRAMFILES(X86)') if os.environ.get(key)]]
        return ([root / 'Google/Chrome/Application/chrome.exe' for root in roots],
                local_app_data() / 'Google/Chrome/User Data')
    return ([Path('/usr/bin/google-chrome'), Path('/usr/bin/google-chrome-stable')],
            Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'google-chrome')


def installed_chrome() -> Path | None:
    paths = chrome_locations()[0]
    if sys.platform == 'win32':
        from app.platforms.windows import chrome_app_paths
        paths += [Path(value) for value in chrome_app_paths()
                  if ntpath.isabs(value) and ntpath.basename(value).casefold() == 'chrome.exe']
    return next((path for path in paths if path.is_file()), None)


def vault_label() -> str:
    return {'darwin': 'macOS 钥匙串', 'win32': 'Windows 凭据管理器'}.get(sys.platform, '系统安全存储')


def credential_backend():
    # Explicit native backends only: do not discover third-party/plaintext stores.
    if sys.platform not in ('darwin', 'win32'):
        raise FlowError('当前系统尚未接入安全凭据存储')
    try:
        if sys.platform == 'darwin':
            from keyring.backends.macOS import Keyring
        else:
            from keyring.backends.Windows import WinVaultKeyring as Keyring
        return Keyring()
    except Exception:
        raise FlowError(f'{vault_label()}暂不可用，请检查系统安全存储权限') from None


def system_label() -> str:
    name = 'macOS' if sys.platform == 'darwin' else platform.system()
    version = platform.mac_ver()[0] if sys.platform == 'darwin' else platform.release()
    return f'{name} {version} · {platform.machine()} · Python {platform.python_version()}'
