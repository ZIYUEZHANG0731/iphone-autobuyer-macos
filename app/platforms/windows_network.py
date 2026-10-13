"""Read Windows network state through native APIs; never change settings.

Only proxy flags, interface counts, IP-family availability and a DNS hash leave
this module. Interface names, local addresses and proxy destinations do not.
Windows interface types cannot identify every VPN or its actual traffic route.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import ipaddress


class SocketAddress(ctypes.Structure):
    _fields_ = [('pointer', ctypes.c_void_p), ('length', ctypes.c_int)]


class AddressNode(ctypes.Structure):
    pass


AddressNode._fields_ = [('length', wintypes.ULONG), ('flags', wintypes.DWORD),
                       ('next', ctypes.POINTER(AddressNode)), ('address', SocketAddress)]


class Adapter(ctypes.Structure):
    pass


Adapter._fields_ = [
    ('length', wintypes.ULONG), ('index', wintypes.DWORD),
    ('next', ctypes.POINTER(Adapter)), ('name', ctypes.c_void_p),
    ('unicast', ctypes.POINTER(AddressNode)), ('anycast', ctypes.c_void_p),
    ('multicast', ctypes.c_void_p), ('dns', ctypes.POINTER(AddressNode)),
    ('suffix', ctypes.c_void_p), ('description', ctypes.c_void_p),
    ('friendly_name', ctypes.c_void_p), ('physical_address', ctypes.c_ubyte * 8),
    ('physical_address_length', wintypes.ULONG), ('flags', wintypes.ULONG),
    ('mtu', wintypes.ULONG), ('interface_type', wintypes.ULONG),
    ('operational_status', ctypes.c_int),
]


def _addresses(node):
    values = []
    visited = set()
    while node and len(visited) < 256:
        position = ctypes.addressof(node.contents)
        if position in visited:
            break
        visited.add(position)
        address = node.contents.address
        if address.pointer and 2 <= address.length <= 128:
            raw = ctypes.string_at(address.pointer, address.length)
            family = int.from_bytes(raw[:2], 'little')
            packed = raw[4:8] if family == 2 and len(raw) >= 8 else raw[8:24] if family == 23 and len(raw) >= 24 else None
            if packed:
                values.append(str(ipaddress.ip_address(packed)))
        node = node.contents.next
    return values


def read_adapters():
    api = ctypes.WinDLL('iphlpapi', use_last_error=True).GetAdaptersAddresses
    api.argtypes = [wintypes.ULONG, wintypes.ULONG, ctypes.c_void_p,
                    ctypes.POINTER(Adapter), ctypes.POINTER(wintypes.ULONG)]
    api.restype = wintypes.ULONG
    size = wintypes.ULONG(15 * 1024)
    for _ in range(3):
        if not 0 < size.value <= 1024 * 1024:
            raise OSError('Network adapter query unavailable')
        buffer = ctypes.create_string_buffer(size.value)
        first = ctypes.cast(buffer, ctypes.POINTER(Adapter))
        # Skip anycast, multicast and friendly-name data. Keep unicast/DNS.
        result = api(0, 2 | 4 | 32, None, first, ctypes.byref(size))
        if result == 111:  # ERROR_BUFFER_OVERFLOW
            continue
        if result == 232:  # ERROR_NO_DATA
            return []
        if result:
            raise OSError('Network adapter query unavailable')
        values, visited = [], set()
        current = first
        while current and len(visited) < 256:
            position = ctypes.addressof(current.contents)
            if position in visited:
                break
            visited.add(position)
            adapter = current.contents
            if adapter.length < Adapter.operational_status.offset + ctypes.sizeof(ctypes.c_int):
                raise OSError('Network adapter structure unavailable')
            values.append((adapter.interface_type, adapter.operational_status,
                           _addresses(adapter.unicast), _addresses(adapter.dns)))
            current = adapter.next
        return values
    raise OSError('Network adapter query unavailable')


class ProxyValue(ctypes.Union):
    _fields_ = [('flags', wintypes.DWORD), ('string', ctypes.c_void_p),
                ('time', wintypes.FILETIME)]


class ProxyOption(ctypes.Structure):
    _fields_ = [('option', wintypes.DWORD), ('value', ProxyValue)]


class ProxyOptions(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('connection', ctypes.c_void_p),
                ('count', wintypes.DWORD), ('error', wintypes.DWORD),
                ('options', ctypes.POINTER(ProxyOption))]


def read_proxy_flags():
    api = ctypes.WinDLL('wininet', use_last_error=True).InternetQueryOptionW
    api.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                    ctypes.POINTER(wintypes.DWORD)]
    api.restype = wintypes.BOOL
    # Query LAN connection flags only. Do not request proxy/PAC destinations.
    # FLAGS_UI preserves the configured auto-detection flag on modern Windows.
    # Microsoft's documented fallback for older clients is FLAGS.
    option = ProxyOption(option=10)  # INTERNET_PER_CONN_FLAGS_UI
    options = ProxyOptions(size=ctypes.sizeof(ProxyOptions), count=1,
                           options=ctypes.pointer(option))
    size = wintypes.DWORD(ctypes.sizeof(options))
    success = api(None, 75, ctypes.byref(options), ctypes.byref(size))
    if not success:
        option.option = 1  # INTERNET_PER_CONN_FLAGS
        size.value = ctypes.sizeof(options)
        success = api(None, 75, ctypes.byref(options), ctypes.byref(size))
    if not success:
        raise OSError('System proxy query unavailable')
    return sorted(name for bit, name in ((2, 'manual'), (4, 'pac'), (8, 'auto_discovery'))
                  if option.value.flags & bit)


def summarize_adapters(adapters):
    counts = {'physical': 0, 'tunnel': 0, 'other': 0}
    versions, servers = set(), set()
    for interface_type, status, addresses, dns in adapters:
        if status != 1 or interface_type == 24:  # Up; exclude loopback
            continue
        category = 'physical' if interface_type in (6, 71, 243, 244) else 'tunnel' if interface_type in (23, 131) else 'other'
        counts[category] += 1
        for value in addresses:
            try:
                address = ipaddress.ip_address(value)
            except ValueError:
                continue
            if not (address.is_loopback or address.is_unspecified or address.is_link_local):
                versions.add(address.version)
        for value in dns:
            try:
                address = ipaddress.ip_address(value)
            except ValueError:
                continue
            if not address.is_unspecified:
                servers.add(str(address))
    return {'active_interfaces': counts,
            'ipv4_available': 4 in versions, 'ipv6_available': 6 in versions,
            'dns_server_count': len(servers),
            'dns_server_fingerprint': hashlib.sha256('\n'.join(sorted(servers)).encode()).hexdigest()[:16] if servers else None}


def read_network_snapshot():
    result = {'available': False, 'system_proxy_flags': None,
              'active_interfaces': None, 'ipv4_available': None,
              'ipv6_available': None, 'connected_vpn_count': None,
              'dns_server_count': None, 'dns_server_fingerprint': None}
    unavailable = ['vpn']  # Third-party VPN connections are not enumerated.
    try:
        result['system_proxy_flags'] = read_proxy_flags()
        result['available'] = True
    except OSError:
        unavailable.append('proxy')
    try:
        result.update(summarize_adapters(read_adapters()))
        result['available'] = True
    except OSError:
        unavailable.extend(('interfaces', 'dns'))
    result['unavailable'] = unavailable
    return result
