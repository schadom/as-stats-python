"""IP address to origin AS lookup from the iptoasn.com dataset.

Used for exporters that do not put BGP AS numbers into their flows (VyOS,
MikroTik, Linux softflowd, most switches...). The dataset is a list of
address ranges with the AS announcing them:

    range_start <TAB> range_end <TAB> AS number <TAB> country <TAB> description

The web process downloads and parses it (see services/ipasn_sync.py) and
stores the result in a compact binary file in the data directory. The
collector loads that file in well under a second and does a binary search per
address.
"""

from __future__ import annotations

import gzip
import ipaddress
import logging
import os
import socket
import struct
import sys
import time
from array import array
from bisect import bisect_right
from pathlib import Path

log = logging.getLogger(__name__)

FILENAME = "ip2asn.bin"
MAGIC = b"ASIP1"
DEFAULT_URL = "https://iptoasn.com/data/ip2asn-combined.tsv.gz"


class IpAsnTable:
    """Non-overlapping address ranges, sorted by start, separately for v4 and v6."""

    def __init__(self) -> None:
        self.v4_start = array("I")
        self.v4_end = array("I")
        self.v4_asn = array("I")
        self.v6_start: list[int] = []
        self.v6_end: list[int] = []
        self.v6_asn = array("I")
        self.mtime = 0.0

    def __len__(self) -> int:
        return len(self.v4_start) + len(self.v6_start)

    def lookup_v4(self, addr: int) -> int:
        i = bisect_right(self.v4_start, addr) - 1
        if i >= 0 and addr <= self.v4_end[i]:
            return self.v4_asn[i]
        return 0

    def lookup_v6(self, addr: int | bytes) -> int:
        if isinstance(addr, (bytes, bytearray)):
            addr = int.from_bytes(addr, "big")
        i = bisect_right(self.v6_start, addr) - 1
        if i >= 0 and addr <= self.v6_end[i]:
            return self.v6_asn[i]
        return 0

    def lookup(self, addr: str) -> int:
        """Convenience lookup by textual address (not used on the hot path)."""
        ip = ipaddress.ip_address(addr)
        return self.lookup_v6(int(ip)) if ip.version == 6 else self.lookup_v4(int(ip))


def parse_lines(lines) -> IpAsnTable:
    """Build a table from dataset lines. Ranges with AS 0 ("not routed") are skipped."""
    v4: list[tuple[int, int, int]] = []
    v6: list[tuple[int, int, int]] = []
    inet_aton = socket.inet_aton
    for line in lines:
        if not line or line[0] == "#":
            continue
        parts = line.split("\t", 3)
        if len(parts) < 3:
            continue
        try:
            asn = int(parts[2])
            if asn <= 0 or asn > 0xFFFFFFFF:
                continue
            start, end = parts[0], parts[1]
            if ":" in start:
                v6.append((int(ipaddress.IPv6Address(start)), int(ipaddress.IPv6Address(end)), asn))
            else:
                v4.append((int.from_bytes(inet_aton(start), "big"), int.from_bytes(inet_aton(end), "big"), asn))
        except (ValueError, OSError):
            continue
    v4.sort()
    v6.sort()
    t = IpAsnTable()
    for s, e, a in v4:
        t.v4_start.append(s)
        t.v4_end.append(e)
        t.v4_asn.append(a)
    for s, e, a in v6:
        t.v6_start.append(s)
        t.v6_end.append(e)
        t.v6_asn.append(a)
    return t


def _le(a: array) -> array:
    if sys.byteorder != "little":
        a = array(a.typecode, a)
        a.byteswap()
    return a


def save_binary(table: IpAsnTable, path: Path) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "wb") as fh:
        fh.write(struct.pack("<5sII", MAGIC, len(table.v4_start), len(table.v6_start)))
        for a in (table.v4_start, table.v4_end, table.v4_asn):
            _le(a).tofile(fh)
        fh.write(b"".join(v.to_bytes(16, "big") for v in table.v6_start))
        fh.write(b"".join(v.to_bytes(16, "big") for v in table.v6_end))
        _le(table.v6_asn).tofile(fh)
    os.replace(tmp, path)


def load_binary(path: Path) -> IpAsnTable:
    started = time.monotonic()
    t = IpAsnTable()
    with open(path, "rb") as fh:
        magic, n4, n6 = struct.unpack("<5sII", fh.read(13))
        if magic != MAGIC:
            raise ValueError(f"{path} is not an IP-to-AS table")
        for a in (t.v4_start, t.v4_end, t.v4_asn):
            a.fromfile(fh, n4)
            if sys.byteorder != "little":
                a.byteswap()
        raw = fh.read(16 * n6)
        t.v6_start = [int.from_bytes(raw[i : i + 16], "big") for i in range(0, len(raw), 16)]
        raw = fh.read(16 * n6)
        t.v6_end = [int.from_bytes(raw[i : i + 16], "big") for i in range(0, len(raw), 16)]
        t.v6_asn.fromfile(fh, n6)
        if sys.byteorder != "little":
            t.v6_asn.byteswap()
    t.mtime = path.stat().st_mtime
    log.info(
        "IP-to-AS table loaded: %d IPv4 and %d IPv6 ranges in %.2fs",
        len(t.v4_start),
        len(t.v6_start),
        time.monotonic() - started,
    )
    return t


def parse_gzip(path: Path) -> IpAsnTable:
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
        return parse_lines(line.rstrip("\n") for line in fh)
