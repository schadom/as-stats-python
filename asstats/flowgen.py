"""Synthetic NetFlow v5/v9, IPFIX and sFlow v5 packets.

Used by the test suite and by `asstats flowgen`, which sends random traffic to
a running collector so you can try the UI without a router.
"""

from __future__ import annotations

import random
import socket
import struct
import time
from dataclasses import dataclass


@dataclass
class Flow:
    in_if: int
    out_if: int
    src_as: int
    dst_as: int
    bytes: int
    v6: bool = False
    src_peer: int | None = None
    dst_peer: int | None = None
    packets: int = 1


# ---------------------------------------------------------------- NetFlow v5


def build_v5(flows: list[Flow], sampling: int = 0, seq: int = 0) -> bytes:
    now = int(time.time())
    header = struct.pack("!HHIIIIBBH", 5, len(flows), 1000, now, 0, seq, 0, 0, sampling & 0x3FFF)
    body = b""
    for f in flows:
        body += struct.pack(
            "!4s4s4sHHIIIIHHBBBBHHBBH",
            bytes(4), bytes(4), bytes(4),
            f.in_if, f.out_if, f.packets, f.bytes, 0, 0, 0, 0, 0, 0, 6, 0,
            f.src_as & 0xFFFF, f.dst_as & 0xFFFF, 0, 0, 0,
        )
    return header + body


# ---------------------------------------------------------------- NetFlow v9 / IPFIX

# (type, length) in the order they are written
V4_FIELDS = [(8, 4), (12, 4), (10, 4), (14, 4), (16, 4), (17, 4), (1, 8), (2, 8), (4, 1), (60, 1), (48, 1)]
V6_FIELDS = [(27, 16), (28, 16), (10, 4), (14, 4), (16, 4), (17, 4), (1, 8), (2, 8), (4, 1), (60, 1), (48, 1)]
PEER_FIELDS = [(129, 4), (128, 4)]


def _record(fields: list[tuple[int, int]], f: Flow, sampler_id: int) -> bytes:
    out = b""
    for ftype, length in fields:
        value = {
            10: f.in_if,
            14: f.out_if,
            16: f.src_as,
            17: f.dst_as,
            1: f.bytes,
            2: f.packets,
            4: 6,
            60: 6 if f.v6 else 4,
            48: sampler_id,
            129: f.src_peer or 0,
            128: f.dst_peer or 0,
        }.get(ftype, 0)
        out += value.to_bytes(length, "big")
    return out


def _pad4(b: bytes) -> bytes:
    return b + bytes((-len(b)) % 4)


def build_v9(
    flows: list[Flow],
    *,
    source_id: int = 1,
    with_templates: bool = True,
    sampling: int = 0,
    sampler_id: int = 1,
    peer_fields: bool = False,
    if_names: dict[int, str] | None = None,
    seq: int = 0,
) -> bytes:
    sets = b""
    v4_fields = V4_FIELDS + (PEER_FIELDS if peer_fields else [])
    v6_fields = V6_FIELDS + (PEER_FIELDS if peer_fields else [])
    count = 0
    if with_templates:
        tpl = b""
        for tid, fields in ((256, v4_fields), (257, v6_fields)):
            tpl += struct.pack("!HH", tid, len(fields)) + b"".join(struct.pack("!HH", t, l) for t, l in fields)
            count += 1
        sets += struct.pack("!HH", 0, 4 + len(tpl)) + tpl
        # options template 258: scope = sampler id (as Cisco FNF does), options: random interval
        opt = struct.pack("!HHH", 258, 4, 8) + struct.pack("!HH", 48, 1) + struct.pack("!HHHH", 49, 1, 50, 4)
        # options template 259: scope = interface, option: interface name
        opt += struct.pack("!HHH", 259, 4, 4) + struct.pack("!HH", 2, 4) + struct.pack("!HH", 82, 32)
        sets += _pad4(struct.pack("!HH", 1, 4 + len(opt) + (-(4 + len(opt)) % 4)) + opt)
        count += 2
    if sampling:
        data = struct.pack("!BBI", sampler_id, 2, sampling)
        sets += _pad4(struct.pack("!HH", 258, 4 + len(data) + (-(4 + len(data)) % 4)) + data)
        count += 1
    if if_names:
        data = b"".join(i.to_bytes(4, "big") + n.encode()[:32].ljust(32, b"\x00") for i, n in if_names.items())
        sets += _pad4(struct.pack("!HH", 259, 4 + len(data) + (-(4 + len(data)) % 4)) + data)
        count += len(if_names)
    for tid, fields, group in ((256, v4_fields, [f for f in flows if not f.v6]), (257, v6_fields, [f for f in flows if f.v6])):
        if not group:
            continue
        data = b"".join(_record(fields, f, sampler_id) for f in group)
        sets += _pad4(struct.pack("!HH", tid, 4 + len(data) + (-(4 + len(data)) % 4)) + data)
        count += len(group)
    header = struct.pack("!HHIIII", 9, count, 1000, int(time.time()), seq, source_id)
    return header + sets


def build_ipfix(
    flows: list[Flow],
    *,
    domain: int = 1,
    with_templates: bool = True,
    sampling: int = 0,
    peer_fields: bool = False,
    if_names: dict[int, str] | None = None,
    seq: int = 0,
) -> bytes:
    sets = b""
    v4_fields = V4_FIELDS + (PEER_FIELDS if peer_fields else [])
    v6_fields = V6_FIELDS + (PEER_FIELDS if peer_fields else [])
    if with_templates:
        tpl = b""
        for tid, fields in ((256, v4_fields), (257, v6_fields)):
            tpl += struct.pack("!HH", tid, len(fields)) + b"".join(struct.pack("!HH", t, l) for t, l in fields)
        sets += struct.pack("!HH", 2, 4 + len(tpl)) + tpl
        # options 258: scope observationDomainId(149), option samplingInterval(34)
        opt = struct.pack("!HHH", 258, 2, 1) + struct.pack("!HHHH", 149, 4, 34, 4)
        # options 259: scope ingressInterface(10), option interfaceName(82) variable length
        opt += struct.pack("!HHH", 259, 2, 1) + struct.pack("!HHHH", 10, 4, 82, 65535)
        sets += struct.pack("!HH", 3, 4 + len(opt)) + opt
    if sampling:
        data = struct.pack("!II", domain, sampling)
        sets += struct.pack("!HH", 258, 4 + len(data)) + data
    if if_names:
        data = b""
        for ifindex, name in if_names.items():
            raw = name.encode()
            data += ifindex.to_bytes(4, "big") + bytes([len(raw)]) + raw
        sets += struct.pack("!HH", 259, 4 + len(data)) + data
    for tid, fields, group in ((256, v4_fields, [f for f in flows if not f.v6]), (257, v6_fields, [f for f in flows if f.v6])):
        if not group:
            continue
        data = b"".join(_record(fields, f, 1) for f in group)
        sets += struct.pack("!HH", tid, 4 + len(data)) + data
    header = struct.pack("!HHIII", 10, 16 + len(sets), int(time.time()), seq, domain)
    return header + sets


# ---------------------------------------------------------------- sFlow v5


def _xdr_record(fmt: int, body: bytes) -> bytes:
    body = _pad4(body)
    return struct.pack("!II", fmt, len(body)) + body


def build_sflow(
    flows: list[Flow],
    *,
    rate: int = 1000,
    router_as: int = 64500,
    agent: str = "192.0.2.1",
    seq: int = 0,
) -> bytes:
    samples = b""
    for i, f in enumerate(flows):
        # raw packet header: ethernet + start of IP header
        ethertype = 0x86DD if f.v6 else 0x0800
        eth = bytes(12) + struct.pack("!H", ethertype) + (b"\x60" if f.v6 else b"\x45") + bytes(19)
        raw = struct.pack("!IIII", 1, f.bytes, 4, len(eth)) + eth
        records = _xdr_record(1, raw)
        # extended gateway: next hop (ipv4), as, src_as, src_peer_as, path
        path = []
        if f.dst_as:
            path = [f.dst_peer or f.dst_as] + ([f.dst_as] if f.dst_peer and f.dst_peer != f.dst_as else [])
        gw = struct.pack("!I4sIII", 1, bytes(4), router_as, f.src_as, f.src_peer or 0)
        if path:
            gw += struct.pack("!III", 1, 2, len(path)) + b"".join(struct.pack("!I", a) for a in path)
        else:
            gw += struct.pack("!I", 0)
        gw += struct.pack("!II", 0, 0)  # communities, localpref
        records += _xdr_record(1003, gw)
        sample = struct.pack("!IIIIIIII", seq + i, f.in_if, rate, (seq + i) * rate, 0, f.in_if, f.out_if, 2) + records
        samples += struct.pack("!II", 1, len(sample)) + sample
    header = struct.pack("!II4sIIII", 5, 1, socket.inet_aton(agent), 0, seq, 1000, len(flows))
    return header + samples


# ---------------------------------------------------------------- generator


POPULAR_ASNS = [
    15169, 16509, 13335, 32934, 8075, 2906, 20940, 714, 3356, 1299, 174, 6939, 3320, 6830,
    8447, 1764, 12322, 3215, 5410, 16276, 24940, 14061, 63949, 46489, 36459, 54113, 22822,
    199524, 60068, 9009, 21342, 8412, 25255, 51184, 197540, 29447, 3209, 6805, 680, 553,
]


def random_flows(links: list[int], local_ifs: list[int], count: int, *, peer: bool = False) -> list[Flow]:
    flows = []
    weights = [1 / (i + 1) for i in range(len(POPULAR_ASNS))]
    for _ in range(count):
        asn = random.choices(POPULAR_ASNS, weights)[0]
        link = random.choice(links)
        local = random.choice(local_ifs)
        size = random.randint(64, 9000)
        v6 = random.random() < 0.35
        if random.random() < 0.7:  # inbound
            flows.append(Flow(link, local, asn, 0, size, v6, src_peer=1299 if peer else None))
        else:
            flows.append(Flow(local, link, 0, asn, size // 4, v6, dst_peer=1299 if peer else None))
    return flows


def run(target: str, protocol: str, port: int, links: list[int], local_ifs: list[int], rate: int, pps: float, sampling: int) -> None:
    host, _, p = target.rpartition(":") if target.count(":") == 1 else (target, "", "")
    host = host or target
    port = int(p) if p else port
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_DGRAM)
    seq = 0
    interval = 1.0 / pps
    names = {i: f"et-0/0/{i}" for i in links + local_ifs}
    print(f"sending {protocol} to {host}:{port}, links {links}, local {local_ifs}, ~{pps:g} packets/s (ctrl-c to stop)")
    try:
        while True:
            flows = random_flows(links, local_ifs, rate)
            if protocol == "v5":
                pkt = build_v5(flows, sampling=sampling, seq=seq)
            elif protocol == "v9":
                pkt = build_v9(flows, sampling=sampling, if_names=names if seq % 50 == 0 else None, with_templates=seq % 20 == 0, seq=seq)
            elif protocol == "ipfix":
                pkt = build_ipfix(flows, sampling=sampling, if_names=names if seq % 50 == 0 else None, with_templates=seq % 20 == 0, seq=seq)
            else:
                pkt = build_sflow(flows, rate=sampling or 1000, seq=seq)
            sock.sendto(pkt, (host, port))
            seq += 1
            time.sleep(interval)
    except KeyboardInterrupt:
        print(f"\nsent {seq} packets")
