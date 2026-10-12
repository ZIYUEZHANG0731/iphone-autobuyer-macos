"""Read local network settings without changing routes or contacting a service.

Only counts, enable flags and a DNS-server fingerprint leave the parsers. VPN
profile names, addresses, proxy URLs and environment values are never retained.
These observations cannot identify the browser's public exit IP or prove which
traffic a Network Extension forwards.
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import os
from pathlib import Path
import re
import sys


PROXY_FLAGS = {
    'HTTPEnable': 'http', 'HTTPSEnable': 'https', 'SOCKSEnable': 'socks',
    'ProxyAutoConfigEnable': 'pac', 'ProxyAutoDiscoveryEnable': 'auto_discovery',
}
PROXY_ENV = ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
             'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy')
SCUTIL_QUERIES = {'proxy': ('--proxy',), 'interfaces': ('--nwi',),
                 'vpn': ('--nc', 'list'), 'dns': ('--dns',)}


def parse_local_network(values: dict[str, str], *, environment=None) -> dict:
    environment = os.environ if environment is None else environment
    proxy = values.get('proxy', '')
    enabled = sorted(label for key, label in PROXY_FLAGS.items()
                     if re.search(rf'^\s*{key}\s*:\s*1\s*$', proxy, re.M))
    nwi = values.get('interfaces', '')
    active = re.search(r'^Network interfaces:\s*(.*)$', nwi, re.M)
    names = active[1].split() if active else []
    physical = sum(bool(re.fullmatch(r'en\d+', name)) for name in names)
    tunnels = sum(bool(re.fullmatch(r'(?:utun|tun|tap|ppp|ipsec)\d+', name)) for name in names)
    def has_address(version):
        section = re.search(rf'IPv{version} network interface information\n(.*?)(?=\nIPv[46] network|\n\s*REACH|\Z)', nwi, re.S)
        return bool(section and re.search(r'^\s*\w+\s*:\s*flags', section[1], re.M))
    # Hash only server addresses. Search domains and private profile labels are
    # deliberately excluded, even from the fingerprint.
    servers = set()
    for address in re.findall(r'^\s*nameserver\[\d+\]\s*:\s*(\S+)', values.get('dns', ''), re.M):
        try:
            servers.add(str(ipaddress.ip_address(address.split('%')[0])))
        except ValueError:
            pass
    return {
        'system_proxy_flags': enabled,
        'proxy_environment_keys': [key for key in PROXY_ENV if environment.get(key)],
        'active_interfaces': {'physical': physical, 'tunnel': tunnels,
                              'other': max(0, len(names)-physical-tunnels)},
        'ipv4_available': has_address(4), 'ipv6_available': has_address(6),
        'connected_vpn_count': len(re.findall(r'^\s*\*?\s*\(Connected\)', values.get('vpn', ''), re.M)),
        'dns_server_count': len(servers),
        'dns_server_fingerprint': hashlib.sha256('\n'.join(sorted(servers)).encode()).hexdigest()[:16] if servers else None,
    }


async def read_scutil(arguments: tuple[str, ...]) -> str:
    process = await asyncio.create_subprocess_exec('/usr/sbin/scutil', *arguments,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        env={**os.environ, 'LC_ALL': 'C'})
    try:
        output, _ = await asyncio.wait_for(process.communicate(), 1.5)
        if process.returncode:
            raise OSError('Local network query unavailable')
        return output[:128*1024].decode('utf-8', 'replace')
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()


async def read_local_network() -> dict:
    if sys.platform != 'darwin' or not Path('/usr/sbin/scutil').is_file():
        return {'available': False}
    results = await asyncio.gather(*(read_scutil(args) for args in SCUTIL_QUERIES.values()),
                                   return_exceptions=True)
    values, unavailable = {}, []
    for name, result in zip(SCUTIL_QUERIES, results):
        if isinstance(result, str):
            values[name] = result
        else:
            unavailable.append(name)
    return {'available': bool(values), **parse_local_network(values),
            'unavailable': unavailable}


def network_summary(state: dict) -> str:
    if not state.get('available'):
        return '本机网络信息暂时无法读取；浏览器会话保留。'
    interfaces = state.get('active_interfaces', {})
    flags = state.get('system_proxy_flags', [])
    parts = ['检测到隧道接口' if interfaces.get('tunnel') else '未检测到隧道接口',
             '系统代理已启用' if flags else '未检测到系统代理开关',
             'IPv6 可用' if state.get('ipv6_available') else 'IPv4 可用' if state.get('ipv4_available') else 'IP 状态未确认']
    return '本机网络：'+' · '.join(parts)+'。这不能确认公网出口；切换网络后的登录仍需官网验证。'
