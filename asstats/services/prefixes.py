"""Optional prefix statistics: hourly compaction, retention and queries.

The collector adds bytes per (hour, link, AS, remote prefix) straight into
prefix_traffic_1h while the hour is running. Once an hour is complete, this
module keeps the biggest prefixes of every AS (setting prefix_max_per_as) that
moved at least prefix_min_mb_hour, and folds everything else into one "other"
row per link and address family, so the table stays small.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict

from sqlalchemy import delete, func, insert, select
from sqlalchemy.engine import Connection, Engine

from .. import appsettings
from ..models import PREFIX_TRAFFIC
from .timeseries import floor_to

log = logging.getLogger(__name__)

STEP = 3600
# Prefix values of the rows that hold folded traffic. Only these start with "*".
OTHER_V4 = "*4"
OTHER_V6 = "*6"


def is_other(prefix: str) -> bool:
    return prefix.startswith("*")


def is_v6(prefix: str) -> bool:
    return prefix == OTHER_V6 or ":" in prefix


def _limits(conn: Connection) -> tuple[int, int]:
    s = appsettings.get_many(conn, ["prefix_max_per_as", "prefix_min_mb_hour"])
    return int(s["prefix_max_per_as"]), int(float(s["prefix_min_mb_hour"]) * 1_000_000)


def compact_hour(conn: Connection, hour: int, max_per_as: int, min_bytes: int) -> tuple[int, int]:
    """Fold the small prefixes of one finished hour. Returns (rows before, rows after)."""
    t = PREFIX_TRAFFIC
    rows = conn.execute(
        select(t.c.asn, t.c.link_id, t.c.prefix, t.c.bytes_in, t.c.bytes_out).where(t.c.ts == hour)
    ).all()
    if not rows:
        return 0, 0
    # total per (asn, prefix) over all links decides what is kept
    totals: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in rows:
        if not is_other(r.prefix):
            totals[r.asn][r.prefix] += r.bytes_in + r.bytes_out
    keep: set[tuple[int, str]] = set()
    for asn, by_prefix in totals.items():
        ranked = sorted(by_prefix.items(), key=lambda kv: (-kv[1], kv[0]))
        for prefix, total in ranked[:max_per_as]:
            if total >= min_bytes:
                keep.add((asn, prefix))
    if len(keep) == sum(1 for r in rows if not is_other(r.prefix)):
        return len(rows), len(rows)
    out: dict[tuple[int, int, str], list[int]] = {}
    for r in rows:
        prefix = r.prefix
        if not is_other(prefix) and (r.asn, prefix) not in keep:
            prefix = OTHER_V6 if is_v6(prefix) else OTHER_V4
        c = out.setdefault((r.asn, r.link_id, prefix), [0, 0])
        c[0] += r.bytes_in
        c[1] += r.bytes_out
    conn.execute(delete(t).where(t.c.ts == hour))
    conn.execute(
        insert(t),
        [
            {"asn": asn, "ts": hour, "link_id": link, "prefix": prefix, "bytes_in": c[0], "bytes_out": c[1]}
            for (asn, link, prefix), c in out.items()
        ],
    )
    return len(rows), len(out)


def run_compaction(engine: Engine, now: int | None = None, grace: int = 600, max_hours: int = 48) -> int:
    """Compact every finished hour not compacted yet. Returns the number of hours processed."""
    now = int(now or time.time())
    limit = floor_to(now - grace, STEP)  # the hour before `limit` may still receive a late flush
    with engine.connect() as conn:
        wm = appsettings.get_value(conn, appsettings.PREFIX_COMPACT_WM, 0)
        if not wm:
            first = conn.execute(select(func.min(PREFIX_TRAFFIC.c.ts))).scalar()
            if first is None:
                return 0
            wm = int(first)
        max_per_as, min_bytes = _limits(conn)
    done = 0
    while wm + STEP <= limit and done < max_hours:
        with engine.begin() as conn:
            before, after = compact_hour(conn, wm, max_per_as, min_bytes)
            appsettings.set_value(conn, appsettings.PREFIX_COMPACT_WM, wm + STEP)
        if before != after:
            log.debug("prefix data for %d: %d rows compacted to %d", wm, before, after)
        wm += STEP
        done += 1
    return done


def apply_retention(engine: Engine, now: int | None = None) -> None:
    now = int(now or time.time())
    with engine.begin() as conn:
        days = int(appsettings.get_value(conn, "prefix_retention_days"))
        conn.execute(delete(PREFIX_TRAFFIC).where(PREFIX_TRAFFIC.c.ts < now - days * 86400))


def purge(engine: Engine) -> int:
    """Delete all prefix data (the "Delete prefix data" button)."""
    with engine.begin() as conn:
        n = conn.execute(delete(PREFIX_TRAFFIC)).rowcount
        appsettings.set_value(conn, appsettings.PREFIX_COMPACT_WM, 0)
    return n or 0


def first_hour(conn: Connection) -> int | None:
    v = conn.execute(select(func.min(PREFIX_TRAFFIC.c.ts))).scalar()
    return int(v) if v is not None else None


def table_rows(conn: Connection) -> int:
    return int(conn.execute(select(func.count()).select_from(PREFIX_TRAFFIC)).scalar_one())


def query_as(
    conn: Connection, asn: int, start: int, end: int, ip: str = "all", links: list[int] | None = None
) -> list[dict]:
    """Per-prefix totals of one AS, with the split per link. Unsorted."""
    t = PREFIX_TRAFFIC
    q = (
        select(t.c.prefix, t.c.link_id, func.sum(t.c.bytes_in).label("i"), func.sum(t.c.bytes_out).label("o"))
        .where(t.c.asn == asn, t.c.ts >= floor_to(start, STEP), t.c.ts < end)
        .group_by(t.c.prefix, t.c.link_id)
    )
    if links:
        q = q.where(t.c.link_id.in_(links))
    out: dict[str, dict] = {}
    for r in conn.execute(q):
        if ip == "4" and is_v6(r.prefix) or ip == "6" and not is_v6(r.prefix):
            continue
        e = out.setdefault(r.prefix, {"prefix": r.prefix, "in": 0, "out": 0, "links": {}})
        e["in"] += int(r.i or 0)
        e["out"] += int(r.o or 0)
        e["links"][str(r.link_id)] = {"in": int(r.i or 0), "out": int(r.o or 0)}
    return list(out.values())
