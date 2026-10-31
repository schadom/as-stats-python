"""Routing symmetry: compare which links an AS's traffic arrives on with the links it leaves on.

Every link has a type (transit, ix, pni or other). For each AS we compute the share of
its inbound and outbound bytes per link. The interesting case for a peering coordinator
is when one direction mostly uses peering (IX or PNI) and the other mostly transit: that
usually means a missing or filtered session, a local-pref mistake or a missing
announcement on one side.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.engine import Connection

from ..models import TRAFFIC

PEERING = {"ix", "pni"}
TRANSIT = {"transit"}
# below this share of overlap (1 = identical link mix both ways) an AS counts as asymmetric
ASYMMETRIC_BELOW = 0.7
# each direction needs at least this share of the minimum volume to be compared at all
MIN_DIRECTION_SHARE = 0.05
# hourly history used to tell since when a mismatch exists
SINCE_HOURS = 14 * 24
SINCE_MIN_BYTES = 100_000


@dataclass
class Result:
    asn: int
    inbound: int
    outbound: int
    in_links: dict[int, int]
    out_links: dict[int, int]
    score: float
    kind: str  # "in_peer_out_transit", "in_transit_out_peer", "asymmetric" or "ok"
    peering_in: float
    peering_out: float


def _share(by_link: dict[int, int], total: int, types: dict[int, str], group: set[str]) -> float:
    if total <= 0:
        return 0.0
    return sum(v for k, v in by_link.items() if types.get(k, "other") in group) / total


def classify(in_links: dict[int, int], out_links: dict[int, int], types: dict[int, str]) -> tuple[str, float, float, float]:
    """(kind, score, peering share in, peering share out) for one AS."""
    tin, tout = sum(in_links.values()), sum(out_links.values())
    score = 1.0
    if tin > 0 and tout > 0:
        diff = sum(abs(in_links.get(k, 0) / tin - out_links.get(k, 0) / tout) for k in set(in_links) | set(out_links))
        score = 1 - diff / 2
    p_in, p_out = _share(in_links, tin, types, PEERING), _share(out_links, tout, types, PEERING)
    t_in, t_out = _share(in_links, tin, types, TRANSIT), _share(out_links, tout, types, TRANSIT)
    if p_in >= 0.5 and t_out >= 0.5:
        kind = "in_peer_out_transit"
    elif t_in >= 0.5 and p_out >= 0.5:
        kind = "in_transit_out_peer"
    elif score < ASYMMETRIC_BELOW:
        kind = "asymmetric"
    else:
        kind = "ok"
    return kind, round(score, 3), round(p_in, 3), round(p_out, 3)


def analyze(rows: list[tuple[int, int, int, int]], types: dict[int, str], min_bytes: int) -> list[Result]:
    """rows: (asn, link_id, bytes in, bytes out). ASes below the minimum are skipped."""
    ins: dict[int, dict[int, int]] = defaultdict(dict)
    outs: dict[int, dict[int, int]] = defaultdict(dict)
    for asn, link, b_in, b_out in rows:
        if b_in:
            ins[asn][link] = ins[asn].get(link, 0) + b_in
        if b_out:
            outs[asn][link] = outs[asn].get(link, 0) + b_out
    floor = max(1, int(min_bytes * MIN_DIRECTION_SHARE))
    out = []
    for asn in set(ins) | set(outs):
        i, o = ins.get(asn, {}), outs.get(asn, {})
        tin, tout = sum(i.values()), sum(o.values())
        if tin + tout < min_bytes or tin < floor or tout < floor:
            continue  # too little traffic, or only one direction: nothing to compare
        kind, score, p_in, p_out = classify(i, o, types)
        out.append(Result(asn, tin, tout, i, o, score, kind, p_in, p_out))
    rank = {"in_peer_out_transit": 0, "in_transit_out_peer": 0, "asymmetric": 1, "ok": 2}
    out.sort(key=lambda r: (rank[r.kind], -(r.inbound + r.outbound), r.asn))
    return out


def mismatch_since(conn: Connection, asn: int, kind: str, types: dict[int, str], ip: str, now: int) -> int | None:
    """First hour of the current, uninterrupted run of this mismatch (hourly data, up to 14 days back)."""
    t = TRAFFIC[3600]
    if ip == "4":
        cin, cout = t.c.in_v4, t.c.out_v4
    elif ip == "6":
        cin, cout = t.c.in_v6, t.c.out_v6
    else:
        cin, cout = t.c.in_v4 + t.c.in_v6, t.c.out_v4 + t.c.out_v6
    q = select(t.c.ts, t.c.link_id, cin.label("i"), cout.label("o")).where(t.c.asn == asn, t.c.ts >= now - SINCE_HOURS * 3600)
    hours: dict[int, tuple[dict[int, int], dict[int, int]]] = defaultdict(lambda: ({}, {}))
    for r in conn.execute(q):
        h = hours[int(r.ts)]
        h[0][r.link_id] = int(r.i or 0)
        h[1][r.link_id] = int(r.o or 0)
    since = None
    for ts in sorted(hours, reverse=True):
        i, o = hours[ts]
        if sum(i.values()) < SINCE_MIN_BYTES or sum(o.values()) < SINCE_MIN_BYTES:
            continue  # quiet hour, says nothing either way
        if classify(i, o, types)[0] != kind:
            break
        since = ts
    return since
