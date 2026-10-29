"""NetFlow v5, NetFlow v9 and IPFIX decoding.

Only the handful of fields per-AS accounting needs are extracted: interfaces,
AS numbers, byte counts, IP version and sampling information. Templates are
compiled into a struct format so that data records are unpacked in bulk.

Every decoded flow is a plain tuple (see FLOW_* indexes) to keep the hot path
cheap:

    (in_if, out_if, src_as, dst_as, src_peer_as, dst_peer_as, bytes, is_v6, sampling_rate,
     src_addr, dst_addr)

`src_peer_as`/`dst_peer_as` are None when the exporter does not send them,
`sampling_rate` is 0 when the exporter did not report one. Addresses are an int
for IPv4, 16 raw bytes for IPv6 (converted only when an AS lookup needs them),
or None when the template has no address fields.
"""

from __future__ import annotations

import json
import logging
import struct
from pathlib import Path
from typing import Iterable

log = logging.getLogger(__name__)

FLOW_IN_IF = 0
FLOW_OUT_IF = 1
FLOW_SRC_AS = 2
FLOW_DST_AS = 3
FLOW_SRC_PEER = 4
FLOW_DST_PEER = 5
FLOW_BYTES = 6
FLOW_V6 = 7
FLOW_RATE = 8
FLOW_SRC_ADDR = 9
FLOW_DST_ADDR = 10

# Information element numbers (identical in NetFlow v9 and IPFIX for these)
IE_IN_BYTES = 1
IE_IPV4_SRC = 8
IE_IN_IF = 10
IE_IPV4_DST = 12
IE_OUT_IF = 14
IE_SRC_AS = 16
IE_DST_AS = 17
IE_OUT_BYTES = 23
IE_IPV6_SRC = 27
IE_IPV6_DST = 28
IE_SAMPLING_INTERVAL = 34
IE_SAMPLER_ID = 48
IE_SAMPLER_RANDOM_INTERVAL = 50
IE_IP_VERSION = 60
IE_IF_NAME = 82
IE_IF_DESC = 83
IE_NEXT_ADJACENT_AS = 128  # bgpNextAdjacentAsNumber -> destination peer AS
IE_PREV_ADJACENT_AS = 129  # bgpPrevAdjacentAsNumber -> source peer AS
IE_SELECTOR_ID = 302
IE_SAMPLING_PACKET_INTERVAL = 305
IE_SAMPLING_PACKET_SPACE = 306

V9_SCOPE_INTERFACE = 2

VARLEN = 65535
MAX_RATE = 1_000_000

_WANTED = {
    IE_IN_BYTES: "bytes",
    IE_OUT_BYTES: "obytes",
    IE_IN_IF: "in_if",
    IE_OUT_IF: "out_if",
    IE_SRC_AS: "src_as",
    IE_DST_AS: "dst_as",
    IE_PREV_ADJACENT_AS: "src_peer",
    IE_NEXT_ADJACENT_AS: "dst_peer",
    IE_IP_VERSION: "ipver",
    IE_SAMPLER_ID: "sampler",
    IE_SELECTOR_ID: "sampler",
    IE_SAMPLING_INTERVAL: "srate",
    IE_SAMPLER_RANDOM_INTERVAL: "srate",
    IE_IPV4_SRC: "src4",
    IE_IPV4_DST: "dst4",
    IE_IPV6_SRC: "src6",
    IE_IPV6_DST: "dst6",
}
_STRUCT_CODES = {1: "B", 2: "H", 4: "I", 8: "Q"}
_ADDR_CODES = {"src4": {4: "I"}, "dst4": {4: "I"}, "src6": {16: "16s"}, "dst6": {16: "16s"}}


class DecodeError(Exception):
    pass


_IE_NAMES = {
    1: "bytes", 2: "packets", 4: "proto", 8: "src4", 10: "in_if", 12: "dst4", 14: "out_if", 16: "src_as",
    17: "dst_as", 23: "out_bytes", 27: "src6", 28: "dst6", 34: "sampling_interval", 48: "sampler_id",
    49: "sampler_mode", 50: "sampler_interval", 60: "ip_version", 61: "direction", 82: "if_name",
    83: "if_desc", 85: "total_bytes", 128: "next_as", 129: "prev_as", 302: "selector_id",
}


def describe_template(tpl: "Template") -> str:
    parts = []
    for ftype, length, ent, scope in tpl.fields:
        if ent:
            label = f"{ent}:{ftype}"
        elif scope and not tpl.ipfix:
            label = f"scope{ftype}"
        else:
            label = _IE_NAMES.get(ftype, str(ftype))
        parts.append(f"{'*' if scope else ''}{label}/{'var' if length == VARLEN else length}")
    kind = "options " if tpl.is_options else ""
    return f"{kind}[{' '.join(parts)}]"


class Template:
    """A compiled (options) template.

    fields: list of (type, length, enterprise, is_scope)
    """

    __slots__ = ("fields", "is_options", "ipfix", "varlen", "fixed_len", "struct", "idx", "v6_default", "fast")

    def __init__(self, fields: list[tuple[int, int, int, bool]], is_options: bool = False, ipfix: bool = False):
        self.fields = fields
        self.is_options = is_options
        # NetFlow v9 scope fields use their own type namespace; IPFIX scopes use normal IEs
        self.ipfix = ipfix
        self.varlen = any(length == VARLEN for _, length, _, _ in fields)
        self.fixed_len = 0 if self.varlen else sum(length for _, length, _, _ in fields)
        types = {t for t, _, ent, scope in fields if ent == 0 and not scope}
        has_v4 = IE_IPV4_SRC in types or IE_IPV4_DST in types
        has_v6 = IE_IPV6_SRC in types or IE_IPV6_DST in types
        self.v6_default = has_v6 and not has_v4
        self.struct = None
        self.idx: dict[str, int] = {}
        self.fast = False
        if not is_options and not self.varlen and self.fixed_len > 0:
            self._compile()

    def _compile(self) -> None:
        fmt = ["!"]
        pos = 0
        fast = True
        for ftype, length, ent, _scope in self.fields:
            name = _WANTED.get(ftype) if ent == 0 else None
            if name is not None and name not in self.idx:
                code = _ADDR_CODES[name].get(length) if name in _ADDR_CODES else _STRUCT_CODES.get(length)
                if code is None:
                    fast = False
                    fmt.append(f"{length}x")
                    continue
                fmt.append(code)
                self.idx[name] = pos
                pos += 1
            else:
                fmt.append(f"{length}x")
        self.fast = fast
        self.struct = struct.Struct("".join(fmt))

    def to_json(self) -> dict:
        return {"fields": [list(f) for f in self.fields], "options": self.is_options, "ipfix": self.ipfix}

    @classmethod
    def from_json(cls, data: dict) -> "Template":
        fields = [(int(t), int(l), int(e), bool(s)) for t, l, e, s in data["fields"]]
        return cls(fields, bool(data.get("options")), bool(data.get("ipfix")))


def _read_varlen(data: bytes, pos: int) -> tuple[int, int]:
    length = data[pos]
    pos += 1
    if length == 255:
        length = int.from_bytes(data[pos : pos + 2], "big")
        pos += 2
    return length, pos


def _parse_record_slow(tpl: Template, data: bytes, pos: int, end: int) -> tuple[list[tuple[int, int, bool, bytes]], int]:
    """Parse one record field by field. Returns ([(type, enterprise, is_scope, raw)], new_pos)."""
    out = []
    for ftype, length, ent, scope in tpl.fields:
        if length == VARLEN:
            if pos >= end:
                raise DecodeError("truncated variable length field")
            length, pos = _read_varlen(data, pos)
        if pos + length > end:
            raise DecodeError("truncated record")
        out.append((ftype, ent, scope, data[pos : pos + length]))
        pos += length
    return out, pos


class NetflowDecoder:
    """Stateful decoder for NetFlow v5/v9 and IPFIX datagrams.

    State kept per exporter: templates, sampling rates announced via options
    data, and interface names announced via options data.
    """

    _V5_HEADER = struct.Struct("!HHIIIIBBH")
    _V5_RECORD = struct.Struct("!II4xHH4xI16xHH4x")
    _V9_HEADER = struct.Struct("!HHIIII")
    _IPFIX_HEADER = struct.Struct("!HHIII")

    def __init__(self, template_file: Path | None = None):
        # (exporter_ip, version, domain, template_id) -> Template
        self.templates: dict[tuple[str, int, int, int], Template] = {}
        # (exporter_ip, domain) -> rate announced without a sampler id
        self.default_rates: dict[tuple[str, int], int] = {}
        # (exporter_ip, domain, sampler_id) -> rate
        self.sampler_rates: dict[tuple[str, int, int], int] = {}
        # (exporter_ip, ifindex) -> interface name, drained by the collector
        self.iface_names: dict[tuple[str, int], str] = {}
        self.missing_template = 0
        self.template_file = template_file
        self.templates_dirty = False
        if template_file is not None:
            self.load_templates()

    # ------------------------------------------------------------ persistence

    def load_templates(self) -> None:
        if self.template_file is None or not self.template_file.exists():
            return
        try:
            data = json.loads(self.template_file.read_text())
            for key, value in data.get("templates", {}).items():
                ip, version, domain, tid = key.rsplit("|", 3)
                self.templates[(ip, int(version), int(domain), int(tid))] = Template.from_json(value)
            for key, value in data.get("default_rates", {}).items():
                ip, domain = key.rsplit("|", 1)
                self.default_rates[(ip, int(domain))] = int(value)
            for key, value in data.get("sampler_rates", {}).items():
                ip, domain, sid = key.rsplit("|", 2)
                self.sampler_rates[(ip, int(domain), int(sid))] = int(value)
            log.info("loaded %d cached templates", len(self.templates))
        except Exception:  # pragma: no cover - corrupt cache is not fatal
            log.exception("could not load template cache %s", self.template_file)

    def save_templates(self) -> None:
        if self.template_file is None or not self.templates_dirty:
            return
        data = {
            "templates": {f"{ip}|{v}|{d}|{t}": tpl.to_json() for (ip, v, d, t), tpl in self.templates.items()},
            "default_rates": {f"{ip}|{d}": r for (ip, d), r in self.default_rates.items()},
            "sampler_rates": {f"{ip}|{d}|{s}": r for (ip, d, s), r in self.sampler_rates.items()},
        }
        tmp = self.template_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(self.template_file)
        self.templates_dirty = False

    def template_count(self, ip: str) -> int:
        return sum(1 for key in self.templates if key[0] == ip)

    def reported_rate(self, ip: str) -> int | None:
        rates = [r for (eip, _), r in self.default_rates.items() if eip == ip]
        rates += [r for (eip, _, _), r in self.sampler_rates.items() if eip == ip]
        return max(rates) if rates else None

    # ------------------------------------------------------------ entry point

    def decode(self, data: bytes, ip: str) -> tuple[int, list[tuple]]:
        if len(data) < 4:
            raise DecodeError("datagram too short")
        version = int.from_bytes(data[0:2], "big")
        if version == 5:
            return 5, self._decode_v5(data, ip)
        if version == 9:
            return 9, self._decode_v9(data, ip)
        if version == 10:
            return 10, self._decode_ipfix(data, ip)
        raise DecodeError(f"unsupported NetFlow version {version}")

    # ------------------------------------------------------------ v5

    def _decode_v5(self, data: bytes, ip: str) -> list[tuple]:
        if len(data) < 24:
            raise DecodeError("short v5 header")
        _ver, count, _up, _secs, _nsecs, _seq, _etype, _eid, sampling = self._V5_HEADER.unpack_from(data, 0)
        rate = sampling & 0x3FFF
        end = 24 + count * 48
        if len(data) < end:
            raise DecodeError("truncated v5 datagram")
        if rate:
            self.default_rates[(ip, 0)] = rate
        flows = []
        append = flows.append
        for src, dst, in_if, out_if, octets, src_as, dst_as in self._V5_RECORD.iter_unpack(data[24:end]):
            append((in_if, out_if, src_as, dst_as, None, None, octets, False, rate, src, dst))
        return flows

    # ------------------------------------------------------------ v9

    def _decode_v9(self, data: bytes, ip: str) -> list[tuple]:
        if len(data) < 20:
            raise DecodeError("short v9 header")
        domain = int.from_bytes(data[16:20], "big")
        pos = 20
        end = len(data)
        flows: list[tuple] = []
        while pos + 4 <= end:
            set_id = int.from_bytes(data[pos : pos + 2], "big")
            set_len = int.from_bytes(data[pos + 2 : pos + 4], "big")
            if set_len < 4 or pos + set_len > end:
                raise DecodeError("bad v9 flowset length")
            body_start, body_end = pos + 4, pos + set_len
            if set_id == 0:
                self._v9_templates(data, body_start, body_end, ip, domain)
            elif set_id == 1:
                self._v9_options_templates(data, body_start, body_end, ip, domain)
            elif set_id >= 256:
                self._data_set(data, body_start, body_end, ip, 9, domain, set_id, flows)
            pos += set_len
        return flows

    def _v9_templates(self, data: bytes, pos: int, end: int, ip: str, domain: int) -> None:
        while pos + 4 <= end:
            tid = int.from_bytes(data[pos : pos + 2], "big")
            count = int.from_bytes(data[pos + 2 : pos + 4], "big")
            pos += 4
            if pos + count * 4 > end:
                raise DecodeError("truncated v9 template")
            fields = []
            for _ in range(count):
                ftype = int.from_bytes(data[pos : pos + 2], "big")
                flen = int.from_bytes(data[pos + 2 : pos + 4], "big")
                fields.append((ftype, flen, 0, False))
                pos += 4
            self._store_template((ip, 9, domain, tid), Template(fields))

    def _v9_options_templates(self, data: bytes, pos: int, end: int, ip: str, domain: int) -> None:
        while pos + 6 <= end:
            tid = int.from_bytes(data[pos : pos + 2], "big")
            scope_len = int.from_bytes(data[pos + 2 : pos + 4], "big")
            opt_len = int.from_bytes(data[pos + 4 : pos + 6], "big")
            pos += 6
            if tid < 256 or pos + scope_len + opt_len > end:
                break  # padding
            fields = []
            for i in range(scope_len // 4):
                o = pos + i * 4
                fields.append((int.from_bytes(data[o : o + 2], "big"), int.from_bytes(data[o + 2 : o + 4], "big"), 0, True))
            pos += scope_len
            for i in range(opt_len // 4):
                o = pos + i * 4
                fields.append((int.from_bytes(data[o : o + 2], "big"), int.from_bytes(data[o + 2 : o + 4], "big"), 0, False))
            pos += opt_len
            self._store_template((ip, 9, domain, tid), Template(fields, is_options=True))

    # ------------------------------------------------------------ IPFIX

    def _decode_ipfix(self, data: bytes, ip: str) -> list[tuple]:
        if len(data) < 16:
            raise DecodeError("short IPFIX header")
        length = int.from_bytes(data[2:4], "big")
        domain = int.from_bytes(data[12:16], "big")
        end = min(length, len(data)) if length >= 16 else len(data)
        pos = 16
        flows: list[tuple] = []
        while pos + 4 <= end:
            set_id = int.from_bytes(data[pos : pos + 2], "big")
            set_len = int.from_bytes(data[pos + 2 : pos + 4], "big")
            if set_len < 4 or pos + set_len > end:
                raise DecodeError("bad IPFIX set length")
            body_start, body_end = pos + 4, pos + set_len
            if set_id == 2:
                self._ipfix_templates(data, body_start, body_end, ip, domain, options=False)
            elif set_id == 3:
                self._ipfix_templates(data, body_start, body_end, ip, domain, options=True)
            elif set_id >= 256:
                self._data_set(data, body_start, body_end, ip, 10, domain, set_id, flows)
            pos += set_len
        return flows

    def _ipfix_templates(self, data: bytes, pos: int, end: int, ip: str, domain: int, options: bool) -> None:
        header = 6 if options else 4
        while pos + 4 <= end:
            tid = int.from_bytes(data[pos : pos + 2], "big")
            count = int.from_bytes(data[pos + 2 : pos + 4], "big")
            if tid < 256 and tid not in (2, 3):
                break  # padding
            if count == 0:
                # template withdrawal
                pos += 4
                if tid in (2, 3):
                    for key in [k for k in self.templates if k[0] == ip and k[1] == 10 and k[2] == domain]:
                        del self.templates[key]
                else:
                    self.templates.pop((ip, 10, domain, tid), None)
                self.templates_dirty = True
                continue
            scope_count = int.from_bytes(data[pos + 4 : pos + 6], "big") if options else 0
            pos += header
            fields = []
            for i in range(count):
                if pos + 4 > end:
                    raise DecodeError("truncated IPFIX template")
                ftype = int.from_bytes(data[pos : pos + 2], "big")
                flen = int.from_bytes(data[pos + 2 : pos + 4], "big")
                pos += 4
                ent = 0
                if ftype & 0x8000:
                    ftype &= 0x7FFF
                    if pos + 4 > end:
                        raise DecodeError("truncated IPFIX enterprise field")
                    ent = int.from_bytes(data[pos : pos + 4], "big")
                    pos += 4
                fields.append((ftype, flen, ent, i < scope_count))
            self._store_template((ip, 10, domain, tid), Template(fields, is_options=options, ipfix=True))

    # ------------------------------------------------------------ shared

    def _store_template(self, key: tuple[str, int, int, int], tpl: Template) -> None:
        old = self.templates.get(key)
        if old is None or old.fields != tpl.fields or old.is_options != tpl.is_options:
            self.templates[key] = tpl
            self.templates_dirty = True
            ip, version, domain, tid = key
            log.info(
                "%s template %d from %s (%s %d): %s",
                "learned" if old is None else "updated",
                tid,
                ip,
                "IPFIX domain" if version == 10 else "NetFlow v9 source id",
                domain,
                describe_template(tpl),
            )

    def _data_set(
        self, data: bytes, pos: int, end: int, ip: str, version: int, domain: int, tid: int, flows: list
    ) -> None:
        tpl = self.templates.get((ip, version, domain, tid))
        if tpl is None:
            self.missing_template += 1
            return
        if tpl.is_options:
            self._options_data(tpl, data, pos, end, ip, domain)
            return
        default_rate = self.default_rates.get((ip, domain), 0)
        if tpl.fast and tpl.struct is not None:
            size = tpl.struct.size
            count = (end - pos) // size
            if count <= 0:
                return
            self._emit_fast(tpl, tpl.struct.iter_unpack(data[pos : pos + count * size]), ip, domain, default_rate, flows)
        else:
            while pos < end:
                if not tpl.varlen and end - pos < tpl.fixed_len:
                    break  # padding
                if tpl.varlen and end - pos < 4:
                    break
                try:
                    record, pos = _parse_record_slow(tpl, data, pos, end)
                except DecodeError:
                    break
                values: dict[str, int | bytes] = {}
                for ftype, ent, _scope, raw in record:
                    if ent:
                        continue
                    name = _WANTED.get(ftype)
                    if name is None or name in values:
                        continue
                    if name in ("src6", "dst6"):
                        if len(raw) == 16:
                            values[name] = raw
                    elif len(raw) <= 8:
                        values[name] = int.from_bytes(raw, "big")
                flows.append(self._flow_from_values(tpl, values, ip, domain, default_rate))

    def _emit_fast(self, tpl: Template, records: Iterable[tuple], ip: str, domain: int, default_rate: int, flows: list) -> None:
        idx = tpl.idx
        i_bytes = idx.get("bytes", -1)
        if i_bytes < 0:
            i_bytes = idx.get("obytes", -1)
        i_in = idx.get("in_if", -1)
        i_out = idx.get("out_if", -1)
        i_src = idx.get("src_as", -1)
        i_dst = idx.get("dst_as", -1)
        i_sp = idx.get("src_peer", -1)
        i_dp = idx.get("dst_peer", -1)
        i_ver = idx.get("ipver", -1)
        i_sampler = idx.get("sampler", -1)
        i_srate = idx.get("srate", -1)
        i_s4 = idx.get("src4", -1)
        i_d4 = idx.get("dst4", -1)
        i_s6 = idx.get("src6", -1)
        i_d6 = idx.get("dst6", -1)
        v6_default = tpl.v6_default
        sampler_rates = self.sampler_rates
        append = flows.append
        for r in records:
            if i_srate >= 0 and r[i_srate]:
                rate = r[i_srate]
            elif i_sampler >= 0:
                rate = sampler_rates.get((ip, domain, r[i_sampler]), default_rate)
            else:
                rate = default_rate
            v6 = (r[i_ver] == 6) if i_ver >= 0 else v6_default
            if v6:
                src = r[i_s6] if i_s6 >= 0 else None
                dst = r[i_d6] if i_d6 >= 0 else None
            else:
                src = r[i_s4] if i_s4 >= 0 else None
                dst = r[i_d4] if i_d4 >= 0 else None
            append(
                (
                    r[i_in] if i_in >= 0 else 0,
                    r[i_out] if i_out >= 0 else 0,
                    r[i_src] if i_src >= 0 else 0,
                    r[i_dst] if i_dst >= 0 else 0,
                    r[i_sp] if i_sp >= 0 else None,
                    r[i_dp] if i_dp >= 0 else None,
                    r[i_bytes] if i_bytes >= 0 else 0,
                    v6,
                    rate,
                    src,
                    dst,
                )
            )

    def _flow_from_values(self, tpl: Template, v: dict, ip: str, domain: int, default_rate: int) -> tuple:
        if v.get("srate"):
            rate = v["srate"]
        elif "sampler" in v:
            rate = self.sampler_rates.get((ip, domain, v["sampler"]), default_rate)
        else:
            rate = default_rate
        nbytes = v.get("bytes", v.get("obytes", 0))
        v6 = (v["ipver"] == 6) if "ipver" in v else tpl.v6_default
        return (
            v.get("in_if", 0),
            v.get("out_if", 0),
            v.get("src_as", 0),
            v.get("dst_as", 0),
            v.get("src_peer"),
            v.get("dst_peer"),
            nbytes,
            v6,
            rate,
            v.get("src6") if v6 else v.get("src4"),
            v.get("dst6") if v6 else v.get("dst4"),
        )

    def _options_data(self, tpl: Template, data: bytes, pos: int, end: int, ip: str, domain: int) -> None:
        min_len = 1 if tpl.varlen else max(tpl.fixed_len, 1)
        while end - pos >= min_len:
            try:
                record, pos = _parse_record_slow(tpl, data, pos, end)
            except DecodeError:
                return
            ifindex = None
            ifname = None
            ifdesc = None
            sampler = None
            rate = None
            pkt_interval = None
            pkt_space = None
            for ftype, ent, scope, raw in record:
                if ent:
                    continue
                if scope and not tpl.ipfix:
                    # NetFlow v9 scope field namespace
                    if ftype == V9_SCOPE_INTERFACE:
                        ifindex = int.from_bytes(raw, "big")
                    elif ftype == IE_SAMPLER_ID:
                        sampler = int.from_bytes(raw, "big")
                    continue
                if ftype in (IE_IN_IF, IE_OUT_IF) and ifindex is None:
                    ifindex = int.from_bytes(raw, "big")
                elif ftype == IE_IF_NAME:
                    ifname = raw.split(b"\x00", 1)[0].decode("utf-8", "replace").strip()
                elif ftype == IE_IF_DESC:
                    ifdesc = raw.split(b"\x00", 1)[0].decode("utf-8", "replace").strip()
                elif ftype in (IE_SAMPLER_ID, IE_SELECTOR_ID):
                    sampler = int.from_bytes(raw, "big")
                elif ftype in (IE_SAMPLING_INTERVAL, IE_SAMPLER_RANDOM_INTERVAL):
                    value = int.from_bytes(raw, "big")
                    if value:
                        rate = value
                elif ftype == IE_SAMPLING_PACKET_INTERVAL:
                    pkt_interval = int.from_bytes(raw, "big")
                elif ftype == IE_SAMPLING_PACKET_SPACE:
                    pkt_space = int.from_bytes(raw, "big")
            if rate is None and pkt_interval:
                rate = max(1, round((pkt_interval + (pkt_space or 0)) / pkt_interval))
            if rate and rate > MAX_RATE:
                log.warning("exporter %s announced an implausible sampling rate %d in options data, ignoring it", ip, rate)
                rate = None
            if rate:
                if sampler is not None:
                    if self.sampler_rates.get((ip, domain, sampler)) != rate:
                        self.sampler_rates[(ip, domain, sampler)] = rate
                        self.templates_dirty = True
                elif self.default_rates.get((ip, domain)) != rate:
                    self.default_rates[(ip, domain)] = rate
                    self.templates_dirty = True
            name = ifname or ifdesc
            if ifindex is not None and name:
                self.iface_names[(ip, ifindex)] = name[:255]
