import os
from pathlib import Path

from app.services.chrome_session import parse_owned_chrome


def test_recovery_accepts_only_the_exact_owned_executable_profile_and_user():
    executable = Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
    profile = Path('/tmp/Apple AutoBuyer fixture/chrome-profile')
    uid = os.getuid()
    command = f'{executable} --user-data-dir={profile} --remote-debugging-port=0 --no-first-run about:blank'
    unrelated = command.replace(str(profile), str(profile)+'-other')
    helper = command.replace(str(executable), str(executable)+' Helper')
    output = f'101 {uid} {command}\n102 {uid} {unrelated}\n103 {uid+1} {command}\n104 {uid} {helper}'
    owners = parse_owned_chrome(output, executable, profile, '9213\n/devtools/browser/fixture-uuid\n')
    assert [(o.pid, o.port) for o in owners] == [(101, 9213)]


def test_stale_or_malformed_debug_endpoint_is_not_used():
    executable, profile = Path('/fixture/Chrome'), Path('/fixture/owned')
    command = f'101 {os.getuid()} {executable} --user-data-dir={profile} --remote-debugging-port=0'
    for active in ('', '9213', '70000\n/devtools/browser/id', '9213\nhttps://attacker.invalid',
                   '9213\n/devtools/browser/id\nextra'):
        assert not parse_owned_chrome(command, executable, profile, active)
    explicit = command.replace('--remote-debugging-port=0', '--remote-debugging-port=9214')
    assert parse_owned_chrome(explicit, executable, profile)[0].port == 9214
