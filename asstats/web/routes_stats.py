"""Read-only statistics endpoints."""

from __future__ import annotations

import re
import threading
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import appsettings
from ..models import LINK_TRAFFIC, TRAFFIC, AsInfo, Link, PdbIx, PdbIxMember, User
from ..services import asnames, prefixes
from ..services.timeseries import aggregate, ceil_to, choose_step, floor_to, rank_asns, retention, series
from .deps import current_user, get_db, require_admin

router = APIRouter()

RANGES = {
    "1h": 3600,
    "3h": 3 * 3600,
    "6h": 6 * 3600,
    "12h": 12 * 3600,
    "24h": 86400,
    "48h": 2 * 86400,
    "7d": 7 * 86400,
    "14d": 14 * 86400,
    "30d": 30 * 86400,
    "90d": 90 * 86400,
    "180d": 180 * 86400,
    "365d": 365 * 86400,
    "730d": 730 * 86400,
}
MAX_SERIES_ASNS = 100


# ---------------------------------------------------------------- helpers


def resolve_range(range_: str | None, start: int | None, end: int | None) -> tuple[int, int]:
    now_end = floor_to(int(time.time()), 300)
    if start is not None:
        if end is not None and int(end) <= int(start):
            raise HTTPException(400, "start must be before end")
        s = floor_to(int(start), 300)
        e = min(ceil_to(int(end), 300), now_end) if end is not None else now_end
    else:
        span = RANGES.get(range_ or "24h")
        if span is None:
            raise HTTPException(400, f"unknown range, use one of {', '.join(RANGES)}")
        e = now_end
        s = e - span
    if s >= e:
        raise HTTPException(400, "start must be before end")
    return s, e


def parse_ints(value: str | None, name: str, limit: int | None = None) -> list[int] | None:
    if value is None or value.strip() == "":
        return None
    try:
        items = [int(v.strip().upper().removeprefix("AS")) for v in value.split(",") if v.strip()]
    except ValueError as exc:
        raise HTTPException(400, f"{name} must be a comma separated list of numbers") from exc
    if limit is not None and len(items) > limit:
        raise HTTPException(400, f"at most {limit} values allowed for {name}")
    return items


def as_meta(db: Session, asns: list[int]) -> dict[int, dict]:
    infos: dict[int, AsInfo] = {}
    for chunk_start in range(0, len(asns), 500):
        chunk = asns[chunk_start : chunk_start + 500]
        for info in db.execute(select(AsInfo).where(AsInfo.asn.in_(chunk))).scalars():
            infos[int(info.asn)] = info
    out = {}
    for asn in asns:
        info = infos.get(asn)
        out[asn] = {
            "name": asnames.display_name(info, asn),
            "handle": info.handle if info else None,
            "country": info.country if info else None,
        }
    return out


def _dir(row: dict, ip: str) -> tuple[int, int]:
    if ip == "4":
        return row["in_v4"], row["out_v4"]
    if ip == "6":
        return row["in_v6"], row["out_v6"]
    return row["in_v4"] + row["in_v6"], row["out_v4"] + row["out_v6"]


class _TTLCache:
    def __init__(self, ttl: float, size: int = 256):
        self.ttl, self.size = ttl, size
        self.data: dict[Any, tuple[float, Any]] = {}
        self.lock = threading.Lock()

    def get(self, key):
        with self.lock:
            hit = self.data.get(key)
            if hit and hit[0] > time.monotonic():
                return hit[1]
        return None

    def put(self, key, value):
        with self.lock:
            if len(self.data) >= self.size:
                self.data.clear()
            self.data[key] = (time.monotonic() + self.ttl, value)


_top_cache = _TTLCache(ttl=60)


def _ip_param(ip: str) -> str:
    if ip not in ("all", "4", "6"):
        raise HTTPException(400, "ip must be all, 4 or 6")
    return ip


# ---------------------------------------------------------------- top AS


def top_data(
    db: Session,
    start: int,
    end: int,
    links: list[int] | None,
    ip: str,
    sort: str,
    limit: int,
    offset: int,
    asns: list[int] | None = None,
) -> dict:
    conn = db.connection()
    ranked = rank_asns(
        conn, TRAFFIC, start, end, links=links, ip=ip, sort=sort, limit=limit + 1, offset=offset, asns=asns
    )
    has_more = len(ranked) > limit
    ranked = ranked[:limit]
    top_asns = [a for a, _, _ in ranked]
    per_link: dict[int, dict[str, dict[str, int]]] = {a: {} for a in top_asns}
    if top_asns:
        for row in aggregate(conn, TRAFFIC, start, end, ["asn", "link_id"], links=links, asns=top_asns):
            i, o = _dir(row, ip)
            per_link[int(row["asn"])][str(row["link_id"])] = {"in": i, "out": o}
    meta = as_meta(db, top_asns)
    items = [
        {"asn": a, **meta[a], "in": i, "out": o, "links": per_link[a]}
        for a, i, o in ranked
    ]
    return {"start": start, "end": end, "ip": ip, "sort": sort, "items": items, "has_more": has_more}


@router.get("/top")
def top(
    range: str | None = "24h",
    start: int | None = None,
    end: int | None = None,
    links: str | None = None,
    ip: str = "all",
    sort: str = Query("total", pattern="^(total|in|out)$"),
    limit: int = Query(20, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    s, e = resolve_range(range, start, end)
    link_ids = parse_ints(links, "links")
    ip = _ip_param(ip)
    key = ("top", s, e, tuple(link_ids or ()), ip, sort, limit, offset)
    cached = _top_cache.get(key)
    if cached is not None:
        return cached
    data = top_data(db, s, e, link_ids, ip, sort, limit, offset)
    _top_cache.put(key, data)
    return data


# ---------------------------------------------------------------- time series


def _step(db: Session, s: int, e: int, step: int | None) -> int:
    if step is not None:
        if step not in (300, 3600, 86400):
            raise HTTPException(400, "step must be 300, 3600 or 86400")
        return step
    return choose_step(s, e, int(time.time()), retention(db.connection()))


@router.get("/series")
def as_series(
    asns: str,
    range: str | None = "24h",
    start: int | None = None,
    end: int | None = None,
    links: str | None = None,
    step: int | None = None,
    _: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    asn_list = parse_ints(asns, "asns", MAX_SERIES_ASNS) or []
    s, e = resolve_range(range, start, end)
    st = _step(db, s, e, step)
    return series(db.connection(), TRAFFIC, s, e, st, ["asn", "link_id"], links=parse_ints(links, "links"), asns=asn_list)


@router.get("/links/series")
def links_series(
    range: str | None = "24h",
    start: int | None = None,
    end: int | None = None,
    links: str | None = None,
    step: int | None = None,
    _: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    s, e = resolve_range(range, start, end)
    st = _step(db, s, e, step)
    return series(db.connection(), LINK_TRAFFIC, s, e, st, ["link_id"], links=parse_ints(links, "links"))


@router.get("/links/usage")
def links_usage(
    range: str | None = "24h",
    start: int | None = None,
    end: int | None = None,
    ip: str = "all",
    top_n: int = Query(10, ge=1, le=50),
    _: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    s, e = resolve_range(range, start, end)
    ip = _ip_param(ip)
    key = ("usage", s, e, ip, top_n)
    cached = _top_cache.get(key)
    if cached is not None:
        return cached
    conn = db.connection()
    totals = {int(r["link_id"]): _dir(r, ip) for r in aggregate(conn, LINK_TRAFFIC, s, e, ["link_id"])}
    out = []
    all_asns: set[int] = set()
    for link in db.execute(select(Link).order_by(Link.sort_order, Link.name)).scalars():
        t_in, t_out = totals.get(link.id, (0, 0))
        ranked = rank_asns(conn, TRAFFIC, s, e, links=[link.id], ip=ip, limit=top_n)
        all_asns.update(a for a, _, _ in ranked)
        out.append(
            {
                "link_id": link.id,
                "in": t_in,
                "out": t_out,
                "top": [{"asn": a, "in": i, "out": o} for a, i, o in ranked],
            }
        )
    meta = as_meta(db, sorted(all_asns))
    for item in out:
        for t in item["top"]:
            t.update(meta[t["asn"]])
    data = {"start": s, "end": e, "ip": ip, "links": out}
    _top_cache.put(key, data)
    return data


# ---------------------------------------------------------------- single AS


@router.get("/as/{asn}")
def as_detail(
    asn: int,
    range: str | None = "24h",
    start: int | None = None,
    end: int | None = None,
    _: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    s, e = resolve_range(range, start, end)
    info = db.get(AsInfo, asn)
    totals = aggregate(db.connection(), TRAFFIC, s, e, ["link_id"], asns=[asn])
    ixs = db.execute(
        select(PdbIx.id, PdbIx.name, PdbIxMember.speed, PdbIxMember.is_rs_peer, PdbIxMember.ipaddr4, PdbIxMember.ipaddr6)
        .join(PdbIx, PdbIx.id == PdbIxMember.ix_id)
        .where(PdbIxMember.asn == asn)
        .order_by(PdbIx.name)
    ).all()
    return {
        "asn": asn,
        "name": asnames.display_name(info, asn),
        "handle": info.handle if info else None,
        "description": info.description if info else None,
        "country": info.country if info else None,
        "pdb_name": info.pdb_name if info else None,
        "pdb_id": info.pdb_id if info else None,
        "custom_name": info.custom_name if info else None,
        "updated_at": info.updated_at if info else 0,
        "start": s,
        "end": e,
        "links": [
            {"link_id": int(r["link_id"]), **{k: r[k] for k in ("in_v4", "out_v4", "in_v6", "out_v6")}} for r in totals
        ],
        "ixs": [
            {
                "ix_id": r.id,
                "name": r.name,
                "speed": r.speed,
                "is_rs_peer": r.is_rs_peer,
                "ipaddr4": r.ipaddr4,
                "ipaddr6": r.ipaddr6,
            }
            for r in ixs
        ],
    }


@router.get("/as/{asn}/prefixes")
def as_prefixes(
    asn: int,
    range: str | None = "24h",
    start: int | None = None,
    end: int | None = None,
    ip: str = "all",
    links: str | None = None,
    sort: str = Query("total", pattern="^(total|in|out)$"),
    limit: int = Query(10, ge=1, le=1000),
    _: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Remote prefixes of one AS, from the optional prefix statistics."""
    s, e = resolve_range(range, start, end)
    ip = _ip_param(ip)
    link_ids = parse_ints(links, "links")
    conn = db.connection()
    enabled = bool(appsettings.get_value(conn, "prefix_enabled"))
    items = prefixes.query_as(conn, asn, s, e, ip, link_ids)
    other = {"in": 0, "out": 0, "links": {}}
    listed = []
    for it in items:
        if prefixes.is_other(it["prefix"]):
            other["in"] += it["in"]
            other["out"] += it["out"]
            for k, v in it["links"].items():
                o = other["links"].setdefault(k, {"in": 0, "out": 0})
                o["in"] += v["in"]
                o["out"] += v["out"]
        else:
            listed.append(it)
    keyf = {"total": lambda x: x["in"] + x["out"], "in": lambda x: x["in"], "out": lambda x: x["out"]}[sort]
    listed.sort(key=lambda x: (-keyf(x), x["prefix"]))
    first = prefixes.first_hour(conn)
    return {
        "enabled": enabled,
        "start": s,
        "end": e,
        "step": prefixes.STEP,
        "first": first,
        "count": len(listed),
        "total": {"in": sum(x["in"] for x in items), "out": sum(x["out"] for x in items)},
        "other": other,
        "prefixes": listed[:limit],
    }


class AsUpdate(BaseModel):
    custom_name: str | None = Field(default=None, max_length=255)


@router.patch("/as/{asn}")
def as_update(asn: int, body: AsUpdate, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    info = db.get(AsInfo, asn)
    if info is None:
        info = AsInfo(asn=asn, updated_at=0)
        db.add(info)
    info.custom_name = (body.custom_name or "").strip() or None
    return {"ok": True}


@router.post("/as/{asn}/refresh")
async def as_refresh(asn: int, request: Request, _: User = Depends(require_admin)):
    from ..db import get_engine, insert_ignore

    engine = get_engine()
    with engine.begin() as conn:
        insert_ignore(conn, AsInfo.__table__, [{"asn": asn, "updated_at": 0}], ["asn"])
    found = await asnames.refresh(engine, asns=[asn])
    return {"ok": bool(found)}


# ---------------------------------------------------------------- search


@router.get("/search")
def search(q: str, limit: int = Query(20, ge=1, le=100), _: User = Depends(current_user), db: Session = Depends(get_db)):
    q = q.strip()
    if not q:
        return []
    m = re.fullmatch(r"(?i)(?:as)?\s*(\d{1,10})", q)
    asns: list[int] = []
    if m:
        asn = int(m.group(1))
        asns.append(asn)
        prefix = select(AsInfo.asn).where(AsInfo.asn != asn).limit(limit - 1)
        # cheap prefix match on the decimal representation
        lo, hi = asn * 10, asn * 10 + 9
        found = list(db.execute(prefix.where(AsInfo.asn.between(lo, hi)).order_by(AsInfo.asn)).scalars())
        asns += [int(a) for a in found]
    else:
        like = f"%{q.replace('%', '').replace('_', '')}%"
        fields = (AsInfo.handle, AsInfo.description, AsInfo.pdb_name, AsInfo.custom_name)
        rows = db.execute(
            select(AsInfo.asn)
            .where(or_(*[func.lower(f).like(like.lower()) for f in fields]))
            .order_by(AsInfo.asn)
            .limit(limit)
        ).scalars()
        asns = [int(a) for a in rows]
    meta = as_meta(db, asns)
    return [{"asn": a, **meta[a]} for a in asns[:limit]]


# ---------------------------------------------------------------- IX


@router.get("/ix")
def ix_list(_: User = Depends(current_user), db: Session = Depends(get_db)):
    counts = dict(
        db.execute(select(PdbIxMember.ix_id, func.count(func.distinct(PdbIxMember.asn))).group_by(PdbIxMember.ix_id)).all()
    )
    values = appsettings.get_many(db, ["my_asn", appsettings.PDB_LAST_REFRESH, appsettings.PDB_LAST_ERROR])
    return {
        "my_asn": values["my_asn"],
        "last_refresh": values[appsettings.PDB_LAST_REFRESH],
        "last_error": values[appsettings.PDB_LAST_ERROR],
        "ixs": [
            {"id": ix.id, "name": ix.name, "name_long": ix.name_long, "city": ix.city, "country": ix.country, "members": counts.get(ix.id, 0)}
            for ix in db.execute(select(PdbIx).order_by(PdbIx.name)).scalars()
        ],
    }


@router.get("/ix/{ix_id}")
def ix_detail(
    ix_id: int,
    range: str | None = "24h",
    start: int | None = None,
    end: int | None = None,
    links: str | None = None,
    ip: str = "all",
    _: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    ix = db.get(PdbIx, ix_id)
    if ix is None:
        raise HTTPException(404, "IX not found")
    s, e = resolve_range(range, start, end)
    ip = _ip_param(ip)
    link_ids = parse_ints(links, "links")
    members: dict[int, dict] = {}
    for m in db.execute(select(PdbIxMember).where(PdbIxMember.ix_id == ix_id)).scalars():
        entry = members.setdefault(
            int(m.asn), {"asn": int(m.asn), "speed": 0, "is_rs_peer": False, "ipaddr4": [], "ipaddr6": []}
        )
        entry["speed"] += m.speed or 0
        entry["is_rs_peer"] = entry["is_rs_peer"] or m.is_rs_peer
        if m.ipaddr4:
            entry["ipaddr4"].append(m.ipaddr4)
        if m.ipaddr6:
            entry["ipaddr6"].append(m.ipaddr6)
    asns = sorted(members)
    key = ("ix", ix_id, s, e, ip, tuple(link_ids or ()))
    traffic = _top_cache.get(key)
    if traffic is None:
        traffic = {}
        for row in aggregate(db.connection(), TRAFFIC, s, e, ["asn"], links=link_ids, asns=asns):
            traffic[int(row["asn"])] = _dir(row, ip)
        _top_cache.put(key, traffic)
    meta = as_meta(db, asns)
    out = []
    for asn in asns:
        i, o = traffic.get(asn, (0, 0))
        out.append({**members[asn], **meta[asn], "in": i, "out": o})
    out.sort(key=lambda m: (-(m["in"] + m["out"]), m["asn"]))
    return {
        "ix": {"id": ix.id, "name": ix.name, "name_long": ix.name_long, "city": ix.city, "country": ix.country},
        "start": s,
        "end": e,
        "ip": ip,
        "members": out,
    }


@router.post("/ix/refresh")
async def ix_refresh(request: Request, _: User = Depends(require_admin)):
    from ..db import get_engine
    from ..services.peeringdb import PeeringDBError
    from ..services.scheduler import Scheduler

    sched = getattr(request.app.state, "scheduler", None) or Scheduler(get_engine())
    try:
        return await sched.refresh_peeringdb()
    except PeeringDBError as exc:
        raise HTTPException(502, str(exc)) from exc
