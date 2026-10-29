"""sFlow v5 decoding: flow samples with the extended gateway (BGP) record.

Produces the same flow tuples as the NetFlow decoder:

    (in_if, out_if, src_as, dst_as, src_peer_as, dst_peer_as, bytes, is_v6, sampling_rate,
     src_addr, dst_addr)

`bytes` is the sampled frame length; the collector multiplies by the rate.
"""

from __future__ import annotations

import struct

from .netflow import DecodeError

_U32 = struct.Struct("!I")
_FLOW_SAMPLE = struct.Struct("!IIIIIIII")  # seq, source, rate, pool, drops, input, output, nrecords
_EXP_FLOW_SAMPLE = struct.Struct("!IIIIIIIIIII")

ETH_IPV4 = 0x0800
ETH_IPV6 = 0x86DD
ETH_VLAN = (0x8100, 0x88A8, 0x9100)
ETH_MPLS = (0x8847, 0x8848)


def _u32(data: bytes, pos: int) -> int:
    return _U32.unpack_from(data, pos)[0]


def _addr_len(addr_type: int) -> int:
    if addr_type == 1:
        return 4
    if addr_type == 2:
        return 16
    if addr_type == 0:
        return 0
    raise DecodeError(f"unknown sFlow address type {addr_type}")


def _ip_from_l3(ip_hdr: bytes) -> tuple[bool, int | bytes | None, int | bytes | None] | None:
    """(is_v6, src, dst) from the start of an IP header."""
    if not ip_hdr:
        return None
    version = ip_hdr[0] >> 4
    if version == 4:
        if len(ip_hdr) >= 20:
            return False, int.from_bytes(ip_hdr[12:16], "big"), int.from_bytes(ip_hdr[16:20], "big")
        return False, None, None
    if version == 6:
        if len(ip_hdr) >= 40:
            return True, bytes(ip_hdr[8:24]), bytes(ip_hdr[24:40])
        return True, None, None
    return None


def _ip_from_header(protocol: int, header: bytes):
    """(is_v6, src, dst) of a sampled packet header, or None if it is not IP."""
    if protocol in (11, 12):  # header starts at the IP header
        return _ip_from_l3(header)
    if protocol != 1 or len(header) < 14:  # ethernet
        return None
    pos = 12
    ethertype = int.from_bytes(header[pos : pos + 2], "big")
    while ethertype in ETH_VLAN and len(header) >= pos + 6:
        pos += 4
        ethertype = int.from_bytes(header[pos : pos + 2], "big")
    pos += 2
    if ethertype in (ETH_IPV4, ETH_IPV6):
        return _ip_from_l3(header[pos:])
    if ethertype in ETH_MPLS:
        # walk the label stack, then guess from the IP version nibble
        while pos + 4 <= len(header):
            label = int.from_bytes(header[pos : pos + 4], "big")
            pos += 4
            if label & 0x100:  # bottom of stack
                break
        return _ip_from_l3(header[pos:])
    return None


class SflowDecoder:
    def __init__(self) -> None:
        self.last_rate: dict[str, int] = {}

    def decode(self, data: bytes, ip: str) -> tuple[int, list[tuple]]:
        try:
            return 5, self._decode(data, ip)
        except struct.error as exc:
            raise DecodeError(f"truncated sFlow datagram: {exc}") from exc

    def _decode(self, data: bytes, ip: str) -> list[tuple]:
        if len(data) < 28:
            raise DecodeError("short sFlow datagram")
        version = _u32(data, 0)
        if version != 5:
            raise DecodeError(f"unsupported sFlow version {version}")
        pos = 8 + _addr_len(_u32(data, 4))
        pos += 12  # sub agent id, sequence, uptime
        nsamples = _u32(data, pos)
        pos += 4
        flows: list[tuple] = []
        end = len(data)
        for _ in range(nsamples):
            if pos + 8 > end:
                raise DecodeError("truncated sFlow sample")
            fmt = _u32(data, pos)
            length = _u32(data, pos + 4)
            body = pos + 8
            pos = body + length
            if pos > end:
                raise DecodeError("truncated sFlow sample body")
            enterprise, fmt_id = fmt >> 12, fmt & 0xFFF
            if enterprise != 0:
                continue
            if fmt_id == 1:
                _seq, _src, rate, _pool, _drops, inp, outp, nrec = _FLOW_SAMPLE.unpack_from(data, body)
                in_if = inp & 0x3FFFFFFF if (inp >> 30) == 0 else 0
                out_if = outp & 0x3FFFFFFF if (outp >> 30) == 0 else 0
                rec_pos = body + _FLOW_SAMPLE.size
            elif fmt_id == 3:
                (_seq, _stype, _sidx, rate, _pool, _drops, in_fmt, in_val, out_fmt, out_val, nrec) = (
                    _EXP_FLOW_SAMPLE.unpack_from(data, body)
                )
                in_if = in_val if in_fmt == 0 else 0
                out_if = out_val if out_fmt == 0 else 0
                rec_pos = body + _EXP_FLOW_SAMPLE.size
            else:
                continue  # counter samples etc.
            if in_if == 0x3FFFFFFF:
                in_if = 0
            if out_if == 0x3FFFFFFF:
                out_if = 0
            if rate:
                self.last_rate[ip] = rate
            flow = self._flow_records(data, rec_pos, pos, nrec, in_if, out_if, rate)
            if flow is not None:
                flows.append(flow)
        return flows

    def _flow_records(self, data: bytes, pos: int, end: int, nrec: int, in_if: int, out_if: int, rate: int):
        frame_len = None
        is_v6: bool | None = None
        src_addr = dst_addr = None
        gateway = None
        for _ in range(nrec):
            if pos + 8 > end:
                break
            fmt = _u32(data, pos)
            length = _u32(data, pos + 4)
            body = pos + 8
            pos = body + length
            if pos > end:
                break
            enterprise, fmt_id = fmt >> 12, fmt & 0xFFF
            if enterprise != 0:
                continue
            if fmt_id == 1:  # raw packet header
                protocol = _u32(data, body)
                frame_len = _u32(data, body + 4)
                header_len = _u32(data, body + 12)
                header = data[body + 16 : body + 16 + header_len]
                info = _ip_from_header(protocol, header)
                if info is not None:
                    is_v6, src_addr, dst_addr = info
            elif fmt_id == 3:  # sampled IPv4: length, protocol, src, dst, ...
                frame_len = frame_len or _u32(data, body)
                is_v6 = False
                src_addr, dst_addr = _u32(data, body + 8), _u32(data, body + 12)
            elif fmt_id == 4:  # sampled IPv6: length, protocol, src, dst, ...
                frame_len = frame_len or _u32(data, body)
                is_v6 = True
                src_addr, dst_addr = bytes(data[body + 8 : body + 24]), bytes(data[body + 24 : body + 40])
            elif fmt_id == 1003:  # extended gateway
                gateway = self._gateway(data, body, pos)
        if frame_len is None:
            return None
        if gateway is None:
            src_as = dst_as = 0
            src_peer = dst_peer = None
        else:
            router_as, src_as, src_peer, path = gateway
            # 0 in src_as / empty path means "originated / terminated here"
            src_origin = src_as or router_as
            dst_origin = path[-1] if path else router_as
            src_peer_as = src_peer or router_as
            dst_peer_as = path[0] if path else router_as
            src_as, dst_as, src_peer, dst_peer = src_origin, dst_origin, src_peer_as, dst_peer_as
        return (in_if, out_if, src_as, dst_as, src_peer, dst_peer, frame_len, bool(is_v6), rate, src_addr, dst_addr)

    @staticmethod
    def _gateway(data: bytes, pos: int, end: int):
        addr_type = _u32(data, pos)
        pos += 4 + _addr_len(addr_type)
        router_as = _u32(data, pos)
        src_as = _u32(data, pos + 4)
        src_peer = _u32(data, pos + 8)
        nseg = _u32(data, pos + 12)
        pos += 16
        path: list[int] = []
        for _ in range(nseg):
            if pos + 8 > end:
                break
            _seg_type = _u32(data, pos)
            count = _u32(data, pos + 4)
            pos += 8
            for i in range(count):
                path.append(_u32(data, pos + i * 4))
            pos += count * 4
        return router_as, src_as, src_peer, path
