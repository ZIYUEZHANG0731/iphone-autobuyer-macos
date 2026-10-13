from __future__ import annotations
import os

import asyncio
import json
import sys
from types import SimpleNamespace
import pytest

from app.services.network_diagnosis import NetworkDiagnosis
from app.services import network_state


def test_local_snapshot_retains_only_flags_counts_and_dns_fingerprint():
    values = {
        'proxy': '''<dictionary> {
          HTTPEnable : 1
          HTTPSEnable : 0
          HTTPProxy : PRIVATE_PROXY.invalid
          HTTPPort : 8888
          ProxyAutoConfigEnable : 1
          ProxyAutoConfigURLString : https://PRIVATE_USER:PRIVATE_PASSWORD@PRIVATE_PROXY.invalid/pac
        }''',
        'interfaces': '''IPv4 network interface information
        en0 : flags : 0x5 (IPv4,DNS)
          address : 192.168.24.32
          reach : 0x00000002

IPv6 network interface information
        utun4 : flags : 0x5 (IPv6,DNS)
          address : fd00::9

Network interfaces: en0 utun4
''',
        'vpn': '* (Connected) PRIVATE_UUID PRIVATE_VPN_NAME\n (Disconnected) PRIVATE_OTHER_UUID PRIVATE_OTHER_NAME',
        'dns': 'search domain[0] : PRIVATE_DOMAIN\n nameserver[0] : 192.168.24.1\n nameserver[1] : 2001:db8::123\n nameserver[0] : 192.168.24.1',
    }
    result = network_state.parse_local_network(values,environment={
        'HTTPS_PROXY':'https://PRIVATE_USER:PRIVATE_PASSWORD@PRIVATE_PROXY.invalid:8888',
        'IGNORED_PRIVATE_KEY':'PRIVATE_VALUE'})
    assert result['system_proxy_flags']==['http','pac']
    assert result['active_interfaces']=={'physical':1,'tunnel':1,'other':0}
    assert result['ipv4_available'] and result['ipv6_available']
    assert result['connected_vpn_count']==1 and result['dns_server_count']==2
    assert result['proxy_environment_keys']==['HTTPS_PROXY']
    assert len(result['dns_server_fingerprint'])==16
    report=json.dumps(result)
    assert all(value not in report for value in ('PRIVATE','192.168','fd00::','2001:db8'))
    dns_only=network_state.parse_local_network({'dns':values['dns'].replace('PRIVATE_DOMAIN','ANOTHER_DOMAIN')},environment={})
    assert dns_only['dns_server_fingerprint']==result['dns_server_fingerprint']


async def test_local_collector_uses_only_read_only_os_commands(monkeypatch):
    arguments=[]
    async def read(args):
        arguments.append(args)
        return ''
    monkeypatch.setattr(network_state,'read_scutil',read)
    monkeypatch.setattr(network_state.sys,'platform','darwin')
    monkeypatch.setattr(network_state.Path,'is_file',lambda _:True)
    result=await network_state.read_local_network()
    assert result['available']
    assert set(arguments)=={('--proxy',),('--nwi',),('--nc','list'),('--dns',)}


@pytest.mark.skipif(sys.platform!='darwin',reason='macOS read-only system query')
async def test_native_macos_vpn_query_is_supported_and_does_not_hide_command_errors():
    result=await network_state.read_local_network()
    assert result['available'] and 'vpn' not in result['unavailable']


async def test_network_change_is_correlated_with_auth_events_and_saved_locally(tmp_path):
    current={'available':True,'active_interfaces':{'physical':1,'tunnel':0,'other':0}}
    async def reader():return dict(current)
    seen=[]
    diagnosis=NetworkDiagnosis(tmp_path,'embedded',network_reader=reader,
        progress=lambda kind,data:seen.append((kind,data)))
    first={'sequence':1,'family':'auth_complete','status':503}
    diagnosis.events.append(first)
    await diagnosis.sample_network('auth_response',first)
    assert first['network_revision']==1
    current['active_interfaces']={'physical':1,'tunnel':1,'other':0}
    diagnosis.network_checked_at-=2
    second={'sequence':2,'family':'auth_complete','status':409,'auth_state':'hsa2'}
    diagnosis.events.append(second)
    await diagnosis.sample_network('auth_response',second)
    report=json.loads(diagnosis.path.read_text(encoding='utf-8'))
    assert [event['network_revision'] for event in report['events']]==[1,2]
    assert len(report['network_samples'])==2 and report['network_samples'][1]['state']==current
    assert seen[-1][1]['changed'] is True
    if os.name != 'nt':
        assert diagnosis.path.stat().st_mode & 0o777 == 0o600


async def test_transport_metadata_is_bounded_and_never_stores_peer_address_or_certificate_names(tmp_path):
    async def peer():return {'ipAddress':'17.253.144.10','port':443}
    async def security():return {'protocol':'TLS 1.3','issuer':'PRIVATE_ISSUER','subjectName':'PRIVATE_SUBJECT'}
    async def http():return 'h2'
    response=SimpleNamespace(server_addr=peer,security_details=security,http_version=http)
    result=await NetworkDiagnosis(tmp_path,'chrome').transport_metadata(response)
    assert result['peer_ip_version']==4 and result['peer_scope']=='public'
    assert result['tls_protocol']=='TLS 1.3' and result['http_version']=='h2'
    assert '17.253' not in json.dumps(result) and 'PRIVATE' not in json.dumps(result)
    async def hang():await asyncio.Future()
    unavailable=SimpleNamespace(server_addr=hang,security_details=hang,http_version=hang)
    result=await asyncio.wait_for(NetworkDiagnosis(tmp_path,'chrome').transport_metadata(unavailable),2)
    assert result=={'available':False}


async def test_network_reader_failure_does_not_abort_authentication_diagnosis(tmp_path):
    async def reader():raise OSError('PRIVATE_NETWORK_ERROR')
    diagnosis=NetworkDiagnosis(tmp_path,'chrome',network_reader=reader)
    event={'sequence':1,'family':'auth_complete','status':503}
    diagnosis.events.append(event)
    await diagnosis.sample_network('auth_response',event)
    assert diagnosis.network_samples[-1]['state']=={'available':False}
    assert 'PRIVATE' not in diagnosis.path.read_text(encoding='utf-8')


async def test_closed_observer_does_not_launch_more_os_queries(tmp_path):
    async def reader():raise AssertionError('Already closed')
    diagnosis=NetworkDiagnosis(tmp_path,'chrome',network_reader=reader)
    diagnosis.closed=True
    diagnosis.schedule_network('auth_request',{})
    assert not diagnosis.tasks


async def test_new_session_preserves_previous_diagnostic_without_page_fields(tmp_path):
    async def reader():return {'available':False}
    context=SimpleNamespace(pages=[],on=lambda *_:None,remove_listener=lambda *_:None)
    # A hashable stand-in is needed for observed pages, just like Playwright's
    # real Page object; no browser or remote request is involved in this check.
    class Page:
        async def evaluate(self,_):return {}
        def on(self,*_):pass
        def remove_listener(self,*_):pass
    page=Page();context.pages=[page]
    diagnosis=NetworkDiagnosis(tmp_path,'chrome',network_reader=reader)
    diagnosis.path.write_text('{"events":[{"family":"auth_complete","auth_state":"hsa2"}]}')
    before=diagnosis.path.read_text(encoding='utf-8')
    await diagnosis.start(context,page)
    await diagnosis.close()
    assert diagnosis.previous_path.read_text(encoding='utf-8')==before
    if os.name != 'nt':
        assert diagnosis.previous_path.stat().st_mode & 0o777 == 0o600
