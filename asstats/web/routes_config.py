"""Configuration: settings, exporters, links, discovered interfaces, status."""

from __future__ import annotations

import ipaddress
import re
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from .. import __version__, appsettings
from ..models import (
    LINK_TRAFFIC,
    TRAFFIC,
    AsInfo,
    DiscoveredInterface,
    Exporter,
    ExporterStatus,
    Link,
    LinkInterface,
    PdbIx,
    User,
)
from ..services.timeseries import watermarks
from .deps import current_user, get_db, require_admin

router = APIRouter()

COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
TAG_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
RATE_UNITS = ("b/s", "bit/s")


# ---------------------------------------------------------------- meta / settings


@router.get("/meta")
def meta(user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = appsettings.get_many(db, ["site_name", "my_asn", "top_default_limit", "rate_unit"])
    links = db.execute(select(Link).order_by(Link.sort_order, Link.name)).scalars().all()
    return {
        "version": __version__,
        "site_name": s["site_name"],
        "my_asn": s["my_asn"],
        "top_default_limit": s["top_default_limit"],
        "rate_unit": s["rate_unit"] if s["rate_unit"] in RATE_UNITS else "b/s",
        "links": [
            {"id": l.id, "tag": l.tag, "name": l.name, "color": l.color, "enabled": l.enabled, "sort_order": l.sort_order}
            for l in links
        ],
        "has_ix": db.execute(select(func.count()).select_from(PdbIx)).scalar_one() > 0,
    }


@router.get("/settings")
def get_settings(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    values = appsettings.get_all_public(db)
    for key in appsettings.SECRET_KEYS:
        values[key] = "********" if values.get(key) else ""
    return values


class SettingsIn(BaseModel):
    site_name: str | None = Field(default=None, max_length=64)
    my_asn: int | None = Field(default=None, ge=0, le=4294967295)
    retention_5m_days: int | None = Field(default=None, ge=1, le=60)
    retention_1h_days: int | None = Field(default=None, ge=2, le=3650)
    retention_1d_days: int | None = Field(default=None, ge=30, le=36500)
    ripestat_enabled: bool | None = None
    ripestat_sourceapp: str | None = Field(default=None, max_length=64)
    asname_ttl_days: int | None = Field(default=None, ge=1, le=365)
    peeringdb_enabled: bool | None = None
    peeringdb_api_key: str | None = Field(default=None, max_length=128)
    top_default_limit: int | None = Field(default=None, ge=5, le=200)
    rate_unit: Literal["b/s", "bit/s"] | None = None
    ipasn_enabled: bool | None = None
    ipasn_url: str | None = Field(default=None, max_length=512, pattern=r"^https?://")


@router.put("/settings")
def put_settings(body: SettingsIn, request: Request, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    data = body.model_dump(exclude_unset=True)
    if "my_asn" in data and data["my_asn"] == 0:
        data["my_asn"] = None
    if data.get("peeringdb_api_key") == "********":
        data.pop("peeringdb_api_key")
    for key, value in data.items():
        appsettings.set_value(db, key, value)
    db.commit()
    sched = getattr(request.app.state, "scheduler", None)
    if sched is not None and "my_asn" in data:
        sched.pdb_now.set()
    return get_settings(_, db)


# ---------------------------------------------------------------- exporters


class ExporterIn(BaseModel):
    ip: str
    name: str = Field(min_length=1, max_length=128)
    sampling_rate: int | None = Field(default=None, ge=1, le=10_000_000)
    as_mode: str = Field(default="origin", pattern="^(origin|peer)$")
    as_source: str = Field(default="auto", pattern="^(auto|router|lookup)$")
    enabled: bool = True

    @field_validator("ip")
    @classmethod
    def _ip(cls, v: str) -> str:
        try:
            return str(ipaddress.ip_address(v.strip()))
        except ValueError as exc:
            raise ValueError("not a valid IP address") from exc


def exporter_out(e: Exporter) -> dict:
    return {
        "id": e.id,
        "ip": e.ip,
        "name": e.name,
        "sampling_rate": e.sampling_rate,
        "as_mode": e.as_mode,
        "as_source": e.as_source,
        "enabled": e.enabled,
    }


@router.get("/exporters")
def list_exporters(_: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(Exporter).order_by(Exporter.name)).scalars().all()
    status = {s.ip: s for s in db.execute(select(ExporterStatus)).scalars()}
    out = []
    for e in rows:
        item = exporter_out(e)
        st = status.get(e.ip)
        item["status"] = _status_out(st) if st else None
        out.append(item)
    return out


@router.post("/exporters", status_code=201)
def create_exporter(body: ExporterIn, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    if db.execute(select(Exporter).where(Exporter.ip == body.ip)).scalar_one_or_none():
        raise HTTPException(409, "an exporter with this IP already exists")
    exp = Exporter(**body.model_dump(), created_at=int(time.time()))
    db.add(exp)
    db.flush()
    appsettings.bump_config_version(db)
    return exporter_out(exp)


@router.put("/exporters/{exporter_id}")
def update_exporter(exporter_id: int, body: ExporterIn, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    exp = db.get(Exporter, exporter_id)
    if exp is None:
        raise HTTPException(404, "exporter not found")
    other = db.execute(select(Exporter).where(Exporter.ip == body.ip, Exporter.id != exporter_id)).scalar_one_or_none()
    if other:
        raise HTTPException(409, "an exporter with this IP already exists")
    for key, value in body.model_dump().items():
        setattr(exp, key, value)
    appsettings.bump_config_version(db)
    return exporter_out(exp)


@router.delete("/exporters/{exporter_id}")
def delete_exporter(exporter_id: int, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    exp = db.get(Exporter, exporter_id)
    if exp is None:
        raise HTTPException(404, "exporter not found")
    db.delete(exp)
    appsettings.bump_config_version(db)
    return {"ok": True}


# ---------------------------------------------------------------- links


class LinkInterfaceIn(BaseModel):
    exporter_id: int
    ifindex: int = Field(ge=0, le=4294967295)
    sampling_rate: int | None = Field(default=None, ge=1, le=10_000_000)


class LinkIn(BaseModel):
    tag: str
    name: str = Field(min_length=1, max_length=128)
    color: str = "#4f7cff"
    sort_order: int = 0
    enabled: bool = True
    interfaces: list[LinkInterfaceIn] = []

    @field_validator("tag")
    @classmethod
    def _tag(cls, v: str) -> str:
        if not TAG_RE.match(v):
            raise ValueError("tag may only contain letters, digits, '.', '_' and '-' (max 32)")
        return v

    @field_validator("color")
    @classmethod
    def _color(cls, v: str) -> str:
        if not COLOR_RE.match(v):
            raise ValueError("color must look like #12abef")
        return v.lower()


def link_out(link: Link, names: dict[tuple[str, int], str | None] | None = None) -> dict:
    names = names or {}
    return {
        "id": link.id,
        "tag": link.tag,
        "name": link.name,
        "color": link.color,
        "sort_order": link.sort_order,
        "enabled": link.enabled,
        "interfaces": [
            {
                "id": i.id,
                "exporter_id": i.exporter_id,
                "exporter_name": i.exporter.name,
                "exporter_ip": i.exporter.ip,
                "ifindex": i.ifindex,
                "if_name": names.get((i.exporter.ip, int(i.ifindex))),
                "sampling_rate": i.sampling_rate,
            }
            for i in link.interfaces
        ],
    }


def _iface_names(db: Session) -> dict[tuple[str, int], str | None]:
    return {
        (r.exporter_ip, int(r.ifindex)): r.if_name
        for r in db.execute(select(DiscoveredInterface.exporter_ip, DiscoveredInterface.ifindex, DiscoveredInterface.if_name))
    }


@router.get("/links")
def list_links(_: User = Depends(current_user), db: Session = Depends(get_db)):
    links = (
        db.execute(
            select(Link)
            .options(selectinload(Link.interfaces).selectinload(LinkInterface.exporter))
            .order_by(Link.sort_order, Link.name)
        )
        .scalars()
        .all()
    )
    names = _iface_names(db)
    return [link_out(l, names) for l in links]


def _apply_link(db: Session, link: Link, body: LinkIn) -> None:
    clash = db.execute(select(Link).where(Link.tag == body.tag, Link.id != (link.id or -1))).scalar_one_or_none()
    if clash:
        raise HTTPException(409, f"tag '{body.tag}' is already used")
    seen = set()
    for iface in body.interfaces:
        key = (iface.exporter_id, iface.ifindex)
        if key in seen:
            raise HTTPException(400, "the same interface is listed twice")
        seen.add(key)
        if db.get(Exporter, iface.exporter_id) is None:
            raise HTTPException(400, f"exporter {iface.exporter_id} does not exist")
        owner = db.execute(
            select(LinkInterface).where(
                LinkInterface.exporter_id == iface.exporter_id,
                LinkInterface.ifindex == iface.ifindex,
                LinkInterface.link_id != (link.id or -1),
            )
        ).scalar_one_or_none()
        if owner is not None:
            other = db.get(Link, owner.link_id)
            raise HTTPException(409, f"ifIndex {iface.ifindex} already belongs to link '{other.name}'")
    link.tag = body.tag
    link.name = body.name
    link.color = body.color
    link.sort_order = body.sort_order
    link.enabled = body.enabled
    existing = {(i.exporter_id, int(i.ifindex)): i for i in link.interfaces}
    wanted = {(i.exporter_id, i.ifindex): i for i in body.interfaces}
    for key, iface in existing.items():
        if key not in wanted:
            link.interfaces.remove(iface)
    for key, iface in wanted.items():
        if key in existing:
            existing[key].sampling_rate = iface.sampling_rate
        else:
            link.interfaces.append(
                LinkInterface(exporter_id=iface.exporter_id, ifindex=iface.ifindex, sampling_rate=iface.sampling_rate)
            )


def _get_link(db: Session, link_id: int) -> Link:
    link = db.execute(
        select(Link)
        .options(selectinload(Link.interfaces).selectinload(LinkInterface.exporter))
        .where(Link.id == link_id)
    ).scalar_one_or_none()
    if link is None:
        raise HTTPException(404, "link not found")
    return link


@router.post("/links", status_code=201)
def create_link(body: LinkIn, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    link = Link(created_at=int(time.time()), tag=body.tag, name=body.name)
    _apply_link(db, link, body)
    db.add(link)
    db.flush()
    appsettings.bump_config_version(db)
    return link_out(_get_link(db, link.id), _iface_names(db))


@router.put("/links/{link_id}")
def update_link(link_id: int, body: LinkIn, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    link = _get_link(db, link_id)
    _apply_link(db, link, body)
    db.flush()
    appsettings.bump_config_version(db)
    db.expire(link)
    return link_out(_get_link(db, link_id), _iface_names(db))


@router.delete("/links/{link_id}")
def delete_link(
    link_id: int, purge: bool = True, _: User = Depends(require_admin), db: Session = Depends(get_db)
):
    link = _get_link(db, link_id)
    db.delete(link)
    if purge:
        for family in (TRAFFIC, LINK_TRAFFIC):
            for table in family.values():
                db.execute(delete(table).where(table.c.link_id == link_id))
    appsettings.bump_config_version(db)
    return {"ok": True}


# ---------------------------------------------------------------- discovered interfaces


@router.get("/discovered")
def discovered(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    exporters = {e.ip: e for e in db.execute(select(Exporter)).scalars()}
    assigned = {
        (r.ip, int(r.ifindex)): (r.link_id, r.name)
        for r in db.execute(
            select(Exporter.ip, LinkInterface.ifindex, LinkInterface.link_id, Link.name)
            .join(Exporter, Exporter.id == LinkInterface.exporter_id)
            .join(Link, Link.id == LinkInterface.link_id)
        )
    }
    out = []
    for d in db.execute(
        select(DiscoveredInterface).order_by(DiscoveredInterface.exporter_ip, DiscoveredInterface.ifindex)
    ).scalars():
        exp = exporters.get(d.exporter_ip)
        link = assigned.get((d.exporter_ip, int(d.ifindex)))
        out.append(
            {
                "exporter_ip": d.exporter_ip,
                "exporter_id": exp.id if exp else None,
                "exporter_name": exp.name if exp else None,
                "ifindex": d.ifindex,
                "if_name": d.if_name,
                "first_seen": d.first_seen,
                "last_seen": d.last_seen,
                "bps_in": d.bps_in,
                "bps_out": d.bps_out,
                "flows": d.flows,
                "link_id": link[0] if link else None,
                "link_name": link[1] if link else None,
            }
        )
    return out


@router.delete("/discovered")
def clear_discovered(
    older_than_days: int = 0,
    exporter_ip: str | None = None,
    ifindex: int | None = None,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    q = delete(DiscoveredInterface)
    if exporter_ip is not None:
        q = q.where(DiscoveredInterface.exporter_ip == exporter_ip)
    if ifindex is not None:
        q = q.where(DiscoveredInterface.ifindex == ifindex)
    if older_than_days:
        q = q.where(DiscoveredInterface.last_seen < int(time.time()) - older_than_days * 86400)
    db.execute(q)
    return {"ok": True}


# ---------------------------------------------------------------- status


def _status_out(s: ExporterStatus) -> dict:
    return {
        "ip": s.ip,
        "protocol": s.protocol,
        "version": s.version,
        "last_seen": s.last_seen,
        "packets": s.packets,
        "flows": s.flows,
        "errors": s.errors,
        "flows_per_sec": s.flows_per_sec,
        "reported_sampling": s.reported_sampling,
        "templates": s.templates,
    }


@router.get("/status")
def status(request: Request, _: User = Depends(current_user), db: Session = Depends(get_db)):
    exporters = {e.ip: e for e in db.execute(select(Exporter)).scalars()}
    rows = []
    for s in db.execute(select(ExporterStatus).order_by(ExporterStatus.ip)).scalars():
        item = _status_out(s)
        exp = exporters.get(s.ip)
        item["exporter_id"] = exp.id if exp else None
        item["exporter_name"] = exp.name if exp else None
        rows.append(item)
    values = appsettings.get_many(
        db,
        [
            appsettings.COLLECTOR_HEARTBEAT,
            appsettings.PDB_LAST_REFRESH,
            appsettings.PDB_LAST_ERROR,
            appsettings.IPASN_LAST_REFRESH,
            appsettings.IPASN_LAST_ERROR,
            appsettings.IPASN_RANGES,
            "ipasn_enabled",
        ],
    )
    sched = getattr(request.app.state, "scheduler", None)
    pending_names = db.execute(select(func.count()).select_from(AsInfo).where(AsInfo.updated_at == 0)).scalar_one()
    return {
        "version": __version__,
        "database": db.get_bind().dialect.name,
        "collector": values[appsettings.COLLECTOR_HEARTBEAT],
        "exporters": rows,
        "rollup": {str(k): v for k, v in watermarks(db.connection()).items()},
        "peeringdb": {
            "last_refresh": values[appsettings.PDB_LAST_REFRESH],
            "last_error": values[appsettings.PDB_LAST_ERROR],
        },
        "ipasn": {
            "enabled": values["ipasn_enabled"],
            "last_refresh": values[appsettings.IPASN_LAST_REFRESH],
            "last_error": values[appsettings.IPASN_LAST_ERROR],
            "ranges": values[appsettings.IPASN_RANGES],
        },
        "asnames_pending": pending_names,
        "jobs": sched.last_run if sched else {},
        "now": int(time.time()),
    }


@router.post("/ipasn/refresh")
async def ipasn_refresh(_: User = Depends(require_admin)):
    from ..config import get_config
    from ..db import get_engine
    from ..services import ipasn_sync

    try:
        return await ipasn_sync.refresh(get_engine(), get_config().data_dir, force=True)
    except Exception as exc:
        raise HTTPException(502, f"download failed: {exc}") from exc
