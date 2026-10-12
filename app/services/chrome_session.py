"""Recover only a running Chrome process using this application's exact profile."""
from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess


@dataclass(frozen=True)
class OwnedChrome:
    pid: int
    port: int


def parse_owned_chrome(output: str, executable: Path, profile: Path, active_port: str = '') -> list[OwnedChrome]:
    results = []
    for line in output.splitlines():
        fields = line.strip().split(None, 2)
        if len(fields) != 3 or not fields[0].isdigit() or fields[1] != str(os.getuid()):continue
        pid, _, command = fields
        # Our launcher always supplies flags immediately after the executable.
        # A space within a longer binary name (e.g. Chrome Helper) is not an
        # argv boundary and must not be treated as the owned main process.
        if not command.startswith(str(executable)+' --'):continue
        if not re.search(r'--user-data-dir='+re.escape(str(profile))+r'(?=\s--|$)', command):continue
        port = re.search(r'--remote-debugging-port=(\d+)(?=\s|$)', command)
        if not port:continue
        number = int(port[1])
        if number == 0:
            lines = active_port.splitlines()
            if len(lines) != 2 or not lines[0].isdigit() or not re.fullmatch(r'/devtools/browser/[a-zA-Z0-9-]+', lines[1]):continue
            number = int(lines[0])
        if 1 <= number <= 65535:results.append(OwnedChrome(int(pid), number))
    return results


def find_owned_chrome(executable: Path, profile: Path) -> list[OwnedChrome]:
    try:
        output = subprocess.run(['/bin/ps','-axo','pid=,uid=,command='],capture_output=True,text=True,timeout=3,check=True).stdout
        path = profile/'DevToolsActivePort'
        active = path.read_text(encoding='utf-8') if path.is_file() and not path.is_symlink() else ''
        return parse_owned_chrome(output, executable, profile, active)
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError('无法核对软件 Chrome 会话归属') from None
