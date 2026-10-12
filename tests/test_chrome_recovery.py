from pathlib import Path

from app.services.chrome_session import parse_owned_chrome


def process(pid, executable, profile, *, username='fixture-user', port='0', extra=()):
    return dict(pid=pid, username=username, exe=str(executable),
                cmdline=[str(executable), f'--user-data-dir={profile}',
                         f'--remote-debugging-port={port}', '--no-first-run', *extra])


def test_recovery_accepts_only_the_exact_owned_executable_profile_and_user():
    executable = Path('/fixture/Google Chrome/Chrome')
    profile = Path('/fixture/Apple AutoBuyer/chrome-profile')
    processes = [process(101, executable, profile),
                 process(102, executable, Path(str(profile)+'-other')),
                 process(103, executable, profile, username='other-user'),
                 process(104, Path(str(executable)+' Helper'), profile),
                 process(105, executable, profile, extra=['--type=renderer']),
                 process(106, executable, profile, extra=['--user-data-dir=/other']),
                 process(107, executable, profile, extra=['--remote-debugging-port=9214'])]
    owners = parse_owned_chrome(processes, executable, profile,
                                '9213\n/devtools/browser/fixture-uuid\n', username='fixture-user')
    assert [(o.pid, o.port) for o in owners] == [(101, 9213)]


def test_stale_or_malformed_debug_endpoint_is_not_used():
    executable, profile = Path('/fixture/Chrome'), Path('/fixture/owned')
    owner = process(101, executable, profile)
    for active in ('', '9213', '70000\n/devtools/browser/id', '9213\nhttps://attacker.invalid',
                   '9213\n/devtools/browser/id\nextra'):
        assert not parse_owned_chrome([owner], executable, profile, active, username='fixture-user')
    assert parse_owned_chrome([process(101, executable, profile, port='9214')],
                              executable, profile, username='fixture-user')[0].port == 9214
