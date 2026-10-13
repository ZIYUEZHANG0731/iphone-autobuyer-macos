import json
import sys

import pytest

from app.platforms import windows_network
from app.services import network_state


@pytest.mark.parametrize('legacy', [False, True])
def test_proxy_query_reads_flags_only_and_uses_documented_legacy_fallback(monkeypatch,legacy):
    import ctypes
    from types import SimpleNamespace
    calls=[]
    def query(handle,option,buffer,size):
        options=ctypes.cast(buffer,ctypes.POINTER(windows_network.ProxyOptions)).contents
        current=options.options.contents
        calls.append(current.option)
        assert handle is None and option==75 and options.count==1 and not options.connection
        if legacy and current.option==10:return False
        current.value.flags=2|4|8
        return True
    monkeypatch.setattr(windows_network.ctypes,'WinDLL',lambda *_args,**_kwargs:SimpleNamespace(InternetQueryOptionW=query),raising=False)
    assert windows_network.read_proxy_flags()==['auto_discovery','manual','pac']
    assert calls==([10,1] if legacy else [10])


def test_windows_adapter_summary_excludes_names_addresses_and_disconnected_dns():
    adapters = [(6, 1, ['192.168.24.32', 'fe80::1'], ['192.168.24.1']),
                (131, 1, ['fd00::9'], ['2001:db8::123', '192.168.24.1']),
                (24, 1, ['127.0.0.1', '::1'], ['127.0.0.1']),
                (71, 2, ['192.168.99.1'], ['192.168.99.2']),
                (1, 1, ['invalid', '0.0.0.0'], ['invalid'])]
    result = windows_network.summarize_adapters(adapters)
    assert result['active_interfaces'] == {'physical': 1, 'tunnel': 1, 'other': 1}
    assert result['ipv4_available'] and result['ipv6_available']
    assert result['dns_server_count'] == 2
    assert len(result['dns_server_fingerprint']) == 16
    assert all(value not in json.dumps(result) for value in ('192.168', 'fd00', '2001:db8', 'fe80'))
    reordered = windows_network.summarize_adapters(list(reversed(adapters)))
    assert reordered == result


def test_only_loopback_and_link_local_do_not_claim_network_connectivity():
    result = windows_network.summarize_adapters([(6, 1, ['169.254.1.2', 'fe80::1'], [])])
    assert not result['ipv4_available'] and not result['ipv6_available']
    assert result['dns_server_count'] == 0 and result['dns_server_fingerprint'] is None


@pytest.mark.parametrize('failed', ['proxy', 'interfaces', 'both'])
def test_partial_native_query_failure_remains_unknown(monkeypatch, failed):
    def unavailable():
        raise OSError('PRIVATE_NETWORK_ERROR')
    monkeypatch.setattr(windows_network, 'read_proxy_flags', unavailable if failed in ('proxy', 'both') else lambda: ['pac'])
    monkeypatch.setattr(windows_network, 'read_adapters', unavailable if failed in ('interfaces', 'both') else lambda: [])
    result = windows_network.read_network_snapshot()
    assert result['available'] is (failed != 'both')
    assert result['connected_vpn_count'] is None and 'vpn' in result['unavailable']
    if failed in ('proxy', 'both'):
        assert result['system_proxy_flags'] is None and 'proxy' in result['unavailable']
    if failed in ('interfaces', 'both'):
        assert result['active_interfaces'] is None and result['ipv4_available'] is None
    assert 'PRIVATE' not in json.dumps(result)
    summary = network_state.network_summary(result)
    if failed == 'proxy':
        assert '系统代理状态未确认' in summary
    if failed == 'interfaces':
        assert '接口状态未确认' in summary


async def test_windows_dispatch_does_not_run_macos_queries_or_save_proxy_values(monkeypatch):
    monkeypatch.setattr(network_state.sys, 'platform', 'win32')
    monkeypatch.setattr(windows_network, 'read_network_snapshot', lambda: {'available': True})
    async def forbidden(_):
        raise AssertionError('macOS query on Windows')
    monkeypatch.setattr(network_state, 'read_scutil', forbidden)
    monkeypatch.setenv('HTTPS_PROXY', 'https://PRIVATE_PASSWORD@PRIVATE_PROXY.invalid')
    result = await network_state.read_local_network()
    assert result['available'] and 'HTTPS_PROXY' in result['proxy_environment_keys']
    assert 'PRIVATE' not in json.dumps(result)


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows read-only native network query')
async def test_native_windows_network_queries_finish_without_shell_or_admin():
    import asyncio
    result = await asyncio.wait_for(network_state.read_local_network(), 2)
    assert result['available'] and result['active_interfaces'] is not None
    assert result['system_proxy_flags'] is not None
    assert result['unavailable'] == ['vpn']
    assert result['connected_vpn_count'] is None
