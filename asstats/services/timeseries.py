"""Queries over the multi-resolution traffic tables.

The 5 minute table holds recent data at full resolution. The scheduler rolls
completed hours into the 1 hour table and completed days into the 1 day table
and records how far it got in a watermark per resolution. A query over an
arbitrary range is split into segments: coarse tables cover the middle of
the range up to their watermark, finer tables fill the edges and the most
recent, not yet rolled up part. That keeps "top AS over 30 days" cheap while
still including the last few minutes.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from sqlalchemy import Table, func, select, union_all
from sqlalchemy.engine import Connection

from .. import appsettings
from ..models import COUNTER_COLUMNS, STEPS

FINER = {86400: 3600, 3600: 300}


def floor_to(t: int, step: int) -> int:
    return t - t % step


def ceil_to(t: int, step: int) -> int:
    return -(-t // step) * step


def watermarks(conn: Connection) -> dict[int, int]:
    values = appsettings.get_many(conn, [appsettings.rollup_key(3600), appsettings.rollup_key(86400)])
    return {
        3600: int(values[appsettings.rollup_key(3600)] or 0),
        86400: int(values[appsettings.rollup_key(86400)] or 0),
    }


def plan(start: int, end: int, wm: dict[int, int], max_step: int = 86400) -> list[tuple[int, int, int]]:
    """Split [start, end) into (step, from, to) segments, coarsest possible."""
    if start >= end:
        return []
    if max_step == 300:
        return [(300, start, end)]
    finer = FINER[max_step]
    a = ceil_to(start, max_step)
    b = floor_to(min(end, wm.get(max_step, 0)), max_step)
    if a < b:
        return plan(start, a, wm, finer) + [(max_step, a, b)] + plan(b, end, wm, finer)
    return plan(start, end, wm, finer)


def _filtered(q, t: Table, a: int, b: int, links: Sequence[int] | None, asns: Sequence[int] | None):
    q = q.where(t.c.ts >= a, t.c.ts < b)
    if links:
        q = q.where(t.c.link_id.in_(list(links)))
    if asns is not None:
        q = q.where(t.c.asn.in_(list(asns)))
    return q


def _union(parts):
    if len(parts) == 1:
        return parts[0].subquery()
    return union_all(*parts).subquery()


def aggregate(
    conn: Connection,
    family: dict[int, Table],
    start: int,
    end: int,
    keys: Sequence[str],
    *,
    links: Sequence[int] | None = None,
    asns: Sequence[int] | None = None,
) -> list[dict]:
    """Sum counters over [start, end) grouped by `keys`."""
    if asns is not None and not asns:
        return []
    segs = plan(start, end, watermarks(conn))
    if not segs:
        return []
    parts = []
    for step, a, b in segs:
        t = family[step]
        q = select(*[t.c[k] for k in keys], *[t.c[c] for c in COUNTER_COLUMNS])
        parts.append(_filtered(q, t, a, b, links, asns))
    u = _union(parts)
    q = select(*[u.c[k] for k in keys], *[func.sum(u.c[c]).label(c) for c in COUNTER_COLUMNS])
    if keys:
        q = q.group_by(*[u.c[k] for k in keys])
    out = []
    for row in conn.execute(q).mappings():
        item = {k: row[k] for k in keys}
        for c in COUNTER_COLUMNS:
            item[c] = int(row[c] or 0)
        out.append(item)
    return out


def _directional(t: Table, ip: str):
    if ip == "4":
        return t.c.in_v4, t.c.out_v4
    if ip == "6":
        return t.c.in_v6, t.c.out_v6
    return t.c.in_v4 + t.c.in_v6, t.c.out_v4 + t.c.out_v6


def rank_asns(
    conn: Connection,
    family: dict[int, Table],
    start: int,
    end: int,
    *,
    links: Sequence[int] | None = None,
    ip: str = "all",
    sort: str = "total",
    limit: int = 20,
    offset: int = 0,
    asns: Sequence[int] | None = None,
) -> list[tuple[int, int, int]]:
    """Top ASes over the range. Returns [(asn, bytes_in, bytes_out)]."""
    if asns is not None and not asns:
        return []
    segs = plan(start, end, watermarks(conn))
    if not segs:
        return []
    parts = []
    for step, a, b in segs:
        t = family[step]
        i, o = _directional(t, ip)
        q = select(t.c.asn.label("asn"), i.label("i"), o.label("o"))
        parts.append(_filtered(q, t, a, b, links, asns))
    u = _union(parts)
    s_in = func.sum(u.c.i)
    s_out = func.sum(u.c.o)
    order = {"in": s_in, "out": s_out}.get(sort, s_in + s_out)
    q = (
        select(u.c.asn, s_in.label("bytes_in"), s_out.label("bytes_out"))
        .group_by(u.c.asn)
        .having(s_in + s_out > 0)
        .order_by(order.desc(), u.c.asn)
        .limit(limit)
        .offset(offset)
    )
    return [(int(r.asn), int(r.bytes_in or 0), int(r.bytes_out or 0)) for r in conn.execute(q)]


def series(
    conn: Connection,
    family: dict[int, Table],
    start: int,
    end: int,
    step: int,
    keys: Sequence[str],
    *,
    links: Sequence[int] | None = None,
    asns: Sequence[int] | None = None,
) -> dict:
    """Bucketed counters. Returns buckets plus one entry per key combination
    with an array per counter column (zero filled)."""
    start = floor_to(start, step)
    buckets = list(range(start, end, step))
    result = {"step": step, "start": start, "end": end, "buckets": buckets, "series": []}
    if not buckets or (asns is not None and not asns):
        return result
    segs = plan(start, end, watermarks(conn), max_step=step)
    parts = []
    for seg_step, a, b in segs:
        t = family[seg_step]
        bucket = (t.c.ts - (t.c.ts % step)) if seg_step < step else t.c.ts
        q = select(bucket.label("b"), *[t.c[k] for k in keys], *[t.c[c] for c in COUNTER_COLUMNS])
        parts.append(_filtered(q, t, a, b, links, asns))
    u = _union(parts)
    q = select(u.c.b, *[u.c[k] for k in keys], *[func.sum(u.c[c]).label(c) for c in COUNTER_COLUMNS]).group_by(
        u.c.b, *[u.c[k] for k in keys]
    )
    index = {b: i for i, b in enumerate(buckets)}
    n = len(buckets)
    entries: dict[tuple, dict] = {}
    for row in conn.execute(q).mappings():
        pos = index.get(int(row["b"]))
        if pos is None:
            continue
        key = tuple(row[k] for k in keys)
        entry = entries.get(key)
        if entry is None:
            entry = {k: row[k] for k in keys}
            for c in COUNTER_COLUMNS:
                entry[c] = [0] * n
            entries[key] = entry
        for c in COUNTER_COLUMNS:
            entry[c][pos] += int(row[c] or 0)
    result["series"] = list(entries.values())
    return result


def choose_step(start: int, end: int, now: int, retention_days: dict[int, int]) -> int:
    """Pick a resolution giving a sensible number of points that still exists."""
    span = end - start
    if span <= 2 * 86400:
        step = 300
    elif span <= 21 * 86400:
        step = 3600
    else:
        step = 86400
    if step == 300 and start < now - retention_days[300] * 86400:
        step = 3600
    if step == 3600 and start < now - retention_days[3600] * 86400:
        step = 86400
    return step


def retention(conn: Connection) -> dict[int, int]:
    v = appsettings.get_many(conn, ["retention_5m_days", "retention_1h_days", "retention_1d_days"])
    return {300: int(v["retention_5m_days"]), 3600: int(v["retention_1h_days"]), 86400: int(v["retention_1d_days"])}


__all__: Iterable[str] = (
    "STEPS",
    "aggregate",
    "choose_step",
    "plan",
    "rank_asns",
    "retention",
    "series",
    "watermarks",
)
