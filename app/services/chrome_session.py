"""Recover only the current user's exact app-owned Chrome executable/profile."""
from dataclasses import dataclass
from pathlib import Path
import re
import psutil
from app.platforms import path_key


def switch_values(args, name):
    values = []
    for index, arg in enumerate(args):
        if arg.startswith(name + '='):
            values.append(arg[len(name) + 1:].strip('"'))
        elif arg == name:
            values.append(args[index + 1].strip('"') if index + 1 < len(args) else '')
    return values


@dataclass(frozen=True)
class OwnedChrome:
    pid: int
    port: int


def parse_owned_chrome(processes, executable: Path, profile: Path, active_port='', *, username) -> list[OwnedChrome]:
    results = []
    expected = path_key(executable)
    for process in processes:
        args = process.get('cmdline') or []
        if process.get('username') != username or not process.get('exe'):
            continue
        if path_key(process['exe']) != expected:
            continue
        if any(arg.startswith('--type=') for arg in args):
            continue
        profiles = switch_values(args, '--user-data-dir')
        if len(profiles) != 1 or not profiles[0] or path_key(profiles[0]) != path_key(profile):
            continue
        ports = switch_values(args, '--remote-debugging-port')
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
