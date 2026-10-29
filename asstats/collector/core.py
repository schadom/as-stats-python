"""Flow classification, aggregation and flushing to the database."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.engine import Engine

from .. import appsettings
from ..db import insert_ignore, upsert
from ..models import (
    LINK_TRAFFIC,
    TRAFFIC,
    AsInfo,
    DiscoveredInterface,
    Exporter,
    ExporterStatus,
    Link,
    LinkInterface,
)
from .netflow import DecodeError, NetflowDecoder
from .sflow import SflowDecoder

log = logging.getLogger(__name__)

BUCKET = 300

# Sanity limits. A value outside them means the exporter sent something we
# misunderstand (or garbage); such flows are dropped with a warning instead of
# poisoning the counters.
MAX_RATE = 1_000_000          # 1:1,000,000 sampling
MAX_FLOW_BYTES = 1 << 40      # 1 TiB in a single (sampled) flow record
MAX_IFINDEX = 0xFFFFFFFF      # ifIndex is 32 bit
MAX_ASN = 0xFFFFFFFF
INT64_MAX = (1 << 63) - 1
WARN_INTERVAL = 600


@dataclass(frozen=True)
class ExporterCfg:
    id: int
    sampling_rate: int | None
    peer_mode: bool
    enabled: bool


@dataclass
class LinkMap:
    exporters: dict[str, ExporterCfg] = field(default_factory=dict)
    # (exporter ip, ifindex) -> (link id, sampling override or None)
    ifaces: dict[tuple[str, int], tuple[int, int | None]] = field(default_factory=dict)


def load_linkmap(engine: Engine) -> LinkMap:
    lm = LinkMap()
    with engine.connect() as conn:
        for row in conn.execute(select(Exporter.id, Exporter.ip, Exporter.sampling_rate, Exporter.as_mode, Exporter.enabled)):
            lm.exporters[row.ip] = ExporterCfg(row.id, row.sampling_rate or None, row.as_mode == "peer", bool(row.enabled))
        q = (
            select(Exporter.ip, LinkInterface.ifindex, LinkInterface.link_id, LinkInterface.sampling_rate)
            .join(Exporter, Exporter.id == LinkInterface.exporter_id)
            .join(Link, Link.id == LinkInterface.link_id)
            .where(Link.enabled.is_(True), Exporter.enabled.is_(True))
        )
        for row in conn.execute(q):
            lm.ifaces[(row.ip, int(row.ifindex))] = (row.link_id, row.sampling_rate or None)
    return lm


class Snapshot:
    """Counters accumulated between two flushes."""

    __slots__ = ("traffic", "links", "ifaces", "exporters", "started")

    def __init__(self) -> None:
        self.traffic: dict[tuple[int, int, int], list[int]] = {}
        self.links: dict[tuple[int, int], list[int]] = {}
        self.ifaces: dict[tuple[str, int], list[int]] = {}
        # ip -> [protocol, version, packets, flows, errors, rejected]
        self.exporters: dict[str, list] = {}
        self.started = time.time()


def normalize_ip(addr: str) -> str:
    if "%" in addr:
        addr = addr.split("%", 1)[0]
    if addr.startswith("::ffff:") and "." in addr:
        return addr[7:]
    return addr


class Collector:
    def __init__(self, engine: Engine, data_dir: Path | None = None):
        self.engine = engine
        template_file = (data_dir / "templates.json") if data_dir else None
        self.netflow = NetflowDecoder(template_file)
        self.sflow = SflowDecoder()
        self.linkmap = LinkMap()
        self.snap = Snapshot()
        self.known_asns: set[int] = set()
        self.config_version = None
        self.total_flows = 0
        self._warned: dict[tuple[str, str], float] = {}

    def _warn(self, ip: str, kind: str, msg: str, *args) -> None:
        """Log a problem with an exporter at most every WARN_INTERVAL seconds."""
        now = time.monotonic()
        if now - self._warned.get((ip, kind), -WARN_INTERVAL) >= WARN_INTERVAL:
            self._warned[(ip, kind)] = now
            log.warning("exporter %s: " + msg, ip, *args)

    # ------------------------------------------------------------ config

    def load_known_asns(self) -> None:
        with self.engine.connect() as conn:
            self.known_asns = set(conn.execute(select(AsInfo.asn)).scalars())

    def maybe_reload(self, force: bool = False) -> bool:
        with self.engine.connect() as conn:
            version = appsettings.get_value(conn, appsettings.CONFIG_VERSION, 0)
        if not force and version == self.config_version:
            return False
        self.linkmap = load_linkmap(self.engine)
        self.config_version = version
        log.info(
            "configuration loaded: %d exporters, %d link interfaces",
            len(self.linkmap.exporters),
            len(self.linkmap.ifaces),
        )
        return True

    # ------------------------------------------------------------ packets

    def _exp_counters(self, ip: str, protocol: str) -> list:
        c = self.snap.exporters.get(ip)
        if c is None:
            c = self.snap.exporters[ip] = [protocol, 0, 0, 0, 0, 0]
        return c

    def handle_netflow(self, data: bytes, ip: str) -> None:
        c = self._exp_counters(ip, "netflow")
        c[2] += 1
        try:
            version, flows = self.netflow.decode(data, ip)
        except DecodeError as exc:
            c[4] += 1
            log.debug("netflow decode error from %s: %s", ip, exc)
            return
        c[0] = "ipfix" if version == 10 else "netflow"
        c[1] = version
        c[3] += len(flows)
        c[5] += self.process(ip, flows)

    def handle_sflow(self, data: bytes, ip: str) -> None:
        c = self._exp_counters(ip, "sflow")
        c[2] += 1
        try:
            version, flows = self.sflow.decode(data, ip)
        except DecodeError as exc:
            c[4] += 1
            log.debug("sflow decode error from %s: %s", ip, exc)
            return
        c[1] = version
        c[3] += len(flows)
        c[5] += self.process(ip, flows)

    def process(self, ip: str, flows: list[tuple]) -> int:
        """Count flows. Returns how many were rejected as implausible."""
        if not flows:
            return 0
        self.total_flows += len(flows)
        rejected = 0
        exp = self.linkmap.exporters.get(ip)
        classify = exp is not None and exp.enabled
        exp_rate = exp.sampling_rate if exp else None
        peer_mode = exp.peer_mode if exp else False
        ifmap = self.linkmap.ifaces
        snap = self.snap
        ifaces = snap.ifaces
        traffic = snap.traffic
        links = snap.links
        bucket = int(time.time()) // BUCKET * BUCKET

        for flow in flows:
            in_if, out_if, src_as, dst_as, src_peer, dst_peer, nbytes, v6, rate = flow
            if rate > MAX_RATE:
                self._warn(ip, "rate", "ignoring implausible sampling rate %d (flow %r); set the rate on the exporter", rate, flow)
                rate = 0
            if (
                nbytes > MAX_FLOW_BYTES
                or in_if > MAX_IFINDEX
                or out_if > MAX_IFINDEX
                or src_as > MAX_ASN
                or dst_as > MAX_ASN
            ):
                rejected += 1
                self._warn(ip, "flow", "dropping implausible flow %r (in_if, out_if, src_as, dst_as, src_peer, dst_peer, bytes, v6, rate)", flow)
                continue
            if src_peer is not None and src_peer > MAX_ASN:
                src_peer = None
            if dst_peer is not None and dst_peer > MAX_ASN:
                dst_peer = None
            base = exp_rate or rate or 1
            scaled = nbytes * base
            if in_if:
                d = ifaces.get((ip, in_if))
                if d is None:
                    d = ifaces[(ip, in_if)] = [0, 0, 0]
                d[0] += scaled
                d[2] += 1
            if out_if:
                d = ifaces.get((ip, out_if))
                if d is None:
                    d = ifaces[(ip, out_if)] = [0, 0, 0]
                d[1] += scaled
                d[2] += 1
            if not classify:
                continue

            hit = ifmap.get((ip, in_if))
            if hit is not None:
                link_id, override = hit
                b = nbytes * override if override else scaled
                asn = src_peer if (peer_mode and src_peer is not None) else src_as
                col = 2 if v6 else 0
                key = (bucket, link_id, asn)
                c = traffic.get(key)
                if c is None:
                    c = traffic[key] = [0, 0, 0, 0]
                c[col] += b
                lkey = (bucket, link_id)
                c = links.get(lkey)
                if c is None:
                    c = links[lkey] = [0, 0, 0, 0]
                c[col] += b

            hit = ifmap.get((ip, out_if))
            if hit is not None:
                link_id, override = hit
                b = nbytes * override if override else scaled
                asn = dst_peer if (peer_mode and dst_peer is not None) else dst_as
                col = 3 if v6 else 1
                key = (bucket, link_id, asn)
                c = traffic.get(key)
                if c is None:
                    c = traffic[key] = [0, 0, 0, 0]
                c[col] += b
                lkey = (bucket, link_id)
                c = links.get(lkey)
                if c is None:
                    c = links[lkey] = [0, 0, 0, 0]
                c[col] += b
        return rejected

    # ------------------------------------------------------------ flushing

    def take_snapshot(self) -> tuple[Snapshot, dict[tuple[str, int], str]]:
        snap, self.snap = self.snap, Snapshot()
        names, self.netflow.iface_names = self.netflow.iface_names, {}
        return snap, names

    def restore(self, snap: Snapshot) -> None:
        """Put counters of a failed flush back so the next flush retries them."""
        if len(snap.traffic) > 2_000_000:
            log.error("dropping %d unflushed traffic entries", len(snap.traffic))
            return
        for store, old in ((self.snap.traffic, snap.traffic), (self.snap.links, snap.links)):
            for key, vals in old.items():
                cur = store.get(key)
                if cur is None:
                    store[key] = vals
                else:
                    for i in range(4):
                        cur[i] += vals[i]
        self.snap.started = min(self.snap.started, snap.started)

    def flush(self, snap: Snapshot, names: dict[tuple[str, int], str], now: float | None = None) -> None:
        now = now or time.time()
        now_i = int(now)
        window = max(1.0, now - snap.started)

        def cap(v: float) -> int:
            return min(int(v), INT64_MAX)

        traffic_rows = [
            {"ts": ts, "link_id": link, "asn": asn, "in_v4": cap(c[0]), "out_v4": cap(c[1]), "in_v6": cap(c[2]), "out_v6": cap(c[3])}
            for (ts, link, asn), c in snap.traffic.items()
        ]
        link_rows = [
            {"ts": ts, "link_id": link, "in_v4": cap(c[0]), "out_v4": cap(c[1]), "in_v6": cap(c[2]), "out_v6": cap(c[3])}
            for (ts, link), c in snap.links.items()
        ]
        iface_rows = []
        for (ip, ifindex), (b_in, b_out, flows) in snap.ifaces.items():
            iface_rows.append(
                {
                    "exporter_ip": ip,
                    "ifindex": ifindex,
                    "if_name": names.pop((ip, ifindex), None),
                    "first_seen": now_i,
                    "last_seen": now_i,
                    "bps_in": cap(b_in * 8 / window),
                    "bps_out": cap(b_out * 8 / window),
                    "flows": cap(flows),
                }
            )
        name_rows = [
            {
                "exporter_ip": ip,
                "ifindex": ifindex,
                "if_name": name,
                "first_seen": now_i,
                "last_seen": now_i,
                "bps_in": 0,
                "bps_out": 0,
                "flows": 0,
            }
            for (ip, ifindex), name in names.items()
            if 0 < ifindex <= MAX_IFINDEX
        ]
        status_rows = []
        for ip, (protocol, version, packets, flows, errors, rejected) in snap.exporters.items():
            if protocol == "sflow":
                reported = self.sflow.last_rate.get(ip)
                templates = None
            else:
                reported = self.netflow.reported_rate(ip)
                templates = self.netflow.template_count(ip)
            if reported is not None and not 0 < reported <= MAX_RATE:
                reported = None
            status_rows.append(
                {
                    "ip": ip,
                    "protocol": protocol,
                    "version": version,
                    "last_seen": now_i,
                    "packets": packets,
                    "flows": flows,
                    "errors": errors + rejected,
                    "flows_per_sec": int(flows / window),
                    "reported_sampling": reported,
                    "templates": templates,
                }
            )
        new_asns = {asn for (_ts, _link, asn) in snap.traffic} - self.known_asns

        with self.engine.begin() as conn:
            upsert(conn, TRAFFIC[300], traffic_rows, ["ts", "link_id", "asn"], add=("in_v4", "out_v4", "in_v6", "out_v6"))
            upsert(conn, LINK_TRAFFIC[300], link_rows, ["ts", "link_id"], add=("in_v4", "out_v4", "in_v6", "out_v6"))
            upsert(
                conn,
                DiscoveredInterface.__table__,
                iface_rows,
                ["exporter_ip", "ifindex"],
                replace=("last_seen", "bps_in", "bps_out", "flows"),
                coalesce=("if_name",),
            )
            upsert(conn, DiscoveredInterface.__table__, name_rows, ["exporter_ip", "ifindex"], replace=("if_name",))
            upsert(
                conn,
                ExporterStatus.__table__,
                status_rows,
                ["ip"],
                add=("packets", "flows", "errors"),
                replace=("protocol", "version", "last_seen", "flows_per_sec", "reported_sampling", "templates"),
            )
            if new_asns:
                insert_ignore(conn, AsInfo.__table__, [{"asn": a, "updated_at": 0} for a in sorted(new_asns)], ["asn"])
            appsettings.set_value(
                conn,
                appsettings.COLLECTOR_HEARTBEAT,
                {
                    "ts": now_i,
                    "flows_per_sec": int(sum(r["flows"] for r in status_rows) / window),
                    "exporters": len(status_rows),
                    "missing_template": self.netflow.missing_template,
                },
            )
        self.known_asns |= new_asns
        try:
            self.netflow.save_templates()
        except OSError:  # pragma: no cover
            log.exception("could not save template cache")
        log.debug(
            "flushed %d AS rows, %d link rows, %d interfaces, %d exporters",
            len(traffic_rows),
            len(link_rows),
            len(iface_rows),
            len(status_rows),
        )
