"""Addresses that are not on the public Internet.

Flows whose remote address falls in one of these ranges are router housekeeping
(IPv6 neighbor discovery, link-local BGP/OSPF sessions, multicast) or private
networks, not traffic exchanged with another AS. With the setting
"ignore_non_internet" (on by default) they are not counted.

An address of 0 / :: means "unknown" (no address in the flow) and is never
treated as non-internet, so exporters without address fields are unaffected.
"""

from __future__ import annotations

import ipaddress

V4_RANGES = [
    "0.0.0.0/8",        # "this network"
    "10.0.0.0/8",       # RFC 1918
    "100.64.0.0/10",    # shared address space (CGNAT), RFC 6598
    "127.0.0.0/8",      # loopback
    "169.254.0.0/16",   # link-local
    "172.16.0.0/12",    # RFC 1918
    "192.0.0.0/24",     # IETF protocol assignments
    "192.0.2.0/24",     # documentation (TEST-NET-1)
    "192.168.0.0/16",   # RFC 1918
    "198.18.0.0/15",    # benchmarking
    "198.51.100.0/24",  # documentation (TEST-NET-2)
    "203.0.113.0/24",   # documentation (TEST-NET-3)
    "224.0.0.0/4",      # multicast
    "240.0.0.0/4",      # reserved and limited broadcast
]

V6_RANGES = [
    "::/96",            # unspecified, loopback, old IPv4-compatible
    "::ffff:0:0/96",    # IPv4-mapped
    "100::/64",         # discard-only
    "2001:db8::/32",    # documentation
    "fc00::/7",         # unique local (ULA)
    "fe80::/10",        # link-local
    "ff00::/8",         # multicast
]


def _compile(ranges: list[str]) -> list[tuple[int, int]]:
    out = []
    for r in ranges:
        net = ipaddress.ip_network(r)
        out.append((int(net.network_address), int(net.netmask)))
    return out


_V4 = _compile(V4_RANGES)
_V6 = _compile(V6_RANGES)
# first octets that can only be non-internet when one of the ranges starts there
_V4_FIRST = {int(ipaddress.ip_network(r).network_address) >> 24 for r in V4_RANGES if int(r.split("/")[1]) >= 8}
_V4_FIRST |= set(range(224, 256))


def is_non_internet_v4(addr: int) -> bool:
    if not addr or (addr >> 24) not in _V4_FIRST:
        return False
    for net, mask in _V4:
        if addr & mask == net:
            return True
    return False


def is_non_internet_v6(addr: int | bytes) -> bool:
    if isinstance(addr, (bytes, bytearray)):
        first = addr[0]
        # fast path: global unicast 2000::/3 except the documentation prefix
        if 0x20 <= first <= 0x3F:
            return bytes(addr[:4]) == b"\x20\x01\x0d\xb8"
        addr = int.from_bytes(addr, "big")
    if not addr:
        return False
    for net, mask in _V6:
        if addr & mask == net:
            return True
    return False
