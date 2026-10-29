"""Import an AS-Stats `knownlinks` file into the database.

Format (one link per line, fields separated by tabs or whitespace):

    <router IP>  <ifIndex>  <tag>  <description>  <color, 6 hex digits>  [<sampling rate>]

The description may contain spaces when the file is tab separated.
"""

from __future__ import annotations

import ipaddress
import re
import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import appsettings
from .models import Exporter, Link, LinkInterface

TAG_CLEAN = re.compile(r"[^A-Za-z0-9_.-]")


@dataclass
class Entry:
    ip: str
    ifindex: int
    tag: str
    description: str
    color: str
    sampling: int | None


def parse(text: str) -> list[Entry]:
    entries = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("\t") if p.strip()] if "\t" in line else line.split()
        if len(parts) < 5:
            raise ValueError(f"line {lineno}: expected at least 5 fields, got {len(parts)}")
        if "\t" not in line and len(parts) > 6:
            # whitespace separated with spaces in the description
            color_idx = next(
                (i for i in range(len(parts) - 1, 3, -1) if re.fullmatch(r"#?[0-9A-Fa-f]{6}", parts[i])), None
            )
            if color_idx is None:
                raise ValueError(f"line {lineno}: no color found")
            parts = parts[:3] + [" ".join(parts[3:color_idx])] + parts[color_idx:]
        ip, ifindex, tag, desc, color = parts[:5]
        sampling = int(parts[5]) if len(parts) > 5 and parts[5].isdigit() else None
        try:
            ip = str(ipaddress.ip_address(ip))
        except ValueError as exc:
            raise ValueError(f"line {lineno}: invalid router IP {ip!r}") from exc
        color = color.lstrip("#")
        if not re.fullmatch(r"[0-9A-Fa-f]{6}", color):
            raise ValueError(f"line {lineno}: invalid color {color!r}")
        entries.append(
            Entry(ip, int(ifindex), TAG_CLEAN.sub("_", tag)[:32], desc[:128], f"#{color.lower()}", sampling or None)
        )
    return entries


def apply(db: Session, entries: list[Entry]) -> dict[str, int]:
    """Create exporters, links and interfaces. Existing links (by tag) are updated."""
    now = int(time.time())
    stats = {"exporters": 0, "links": 0, "interfaces": 0}
    exporters = {e.ip: e for e in db.execute(select(Exporter)).scalars()}
    links = {l.tag: l for l in db.execute(select(Link)).scalars()}
    order = len(links) * 10
    for e in entries:
        exp = exporters.get(e.ip)
        if exp is None:
            exp = Exporter(ip=e.ip, name=e.ip, created_at=now)
            db.add(exp)
            db.flush()
            exporters[e.ip] = exp
            stats["exporters"] += 1
        link = links.get(e.tag)
        if link is None:
            link = Link(tag=e.tag, name=e.description or e.tag, color=e.color, sort_order=order, created_at=now)
            order += 10
            db.add(link)
            db.flush()
            links[e.tag] = link
            stats["links"] += 1
        else:
            link.name = e.description or link.name
            link.color = e.color
        existing = db.execute(
            select(LinkInterface).where(LinkInterface.exporter_id == exp.id, LinkInterface.ifindex == e.ifindex)
        ).scalar_one_or_none()
        if existing is None:
            db.add(LinkInterface(link_id=link.id, exporter_id=exp.id, ifindex=e.ifindex, sampling_rate=e.sampling))
            stats["interfaces"] += 1
        else:
            existing.link_id = link.id
            existing.sampling_rate = e.sampling
    appsettings.bump_config_version(db)
    return stats
