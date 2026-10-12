"""Recover only the current user's exact app-owned Chrome executable/profile."""
from dataclasses import dataclass
import os
from pathlib import Path
import re
import psutil


@dataclass(frozen=True)
class OwnedChrome:
    pid: int
    port: int


def parse_owned_chrome(processes, executable: Path, profile: Path, active_port='', *, username) -> list[OwnedChrome]:
    results = []
    expected = os.path.normcase(str(executable.resolve()))
    required = f'--user-data-dir={profile}'
    for process in processes:
        args = process.get('cmdline') or []
        if process.get('username') != username or not process.get('exe') or required not in args:
            continue
        if os.path.normcase(str(Path(process['exe']).resolve())) != expected:
            continue
        if any(arg.startswith('--type=') for arg in args):
            continue
        if sum(arg.startswith('--user-data-dir=') for arg in args) != 1:
            continue
        ports = [arg.removeprefix('--remote-debugging-port=') for arg in args if arg.startswith('--remote-debugging-port=')]
        if len(ports) != 1 or not ports[0].isdigit():
            continue
        number = int(ports[0])
        if number == 0:
            lines = active_port.splitlines()
            if len(lines) != 2 or not lines[0].isdigit() or not re.fullmatch(r'/devtools/browser/[a-zA-Z0-9-]+', lines[1]):continue
            number = int(lines[0])
        if 1 <= number <= 65535:results.append(OwnedChrome(process['pid'], number))
    return results


def find_owned_chrome(executable: Path, profile: Path) -> list[OwnedChrome]:
    try:
        path = profile/'DevToolsActivePort'
        active = path.read_text(encoding='utf-8') if path.is_file() and not path.is_symlink() else ''
        processes = (process.info for process in psutil.process_iter(['pid', 'username', 'exe', 'cmdline']))
        return parse_owned_chrome(processes, executable, profile, active, username=psutil.Process().username())
    except (OSError, psutil.Error):
        raise RuntimeError('无法核对软件 Chrome 会话归属') from None
