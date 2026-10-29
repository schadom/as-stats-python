"""AS name lookups through the RIPEstat Data API, cached in `as_info`.

The collector inserts a placeholder row (updated_at = 0) for every AS it sees,
this module fills in names in the background and refreshes them after the TTL.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

import httpx
from sqlalchemy import or_, select, update
from sqlalchemy.engine import Engine

from .. import __version__, appsettings
from ..models import AsInfo

log = logging.getLogger(__name__)

RIPESTAT_URL = "https://stat.ripe.net/data/as-overview/data.json"
RIPESTAT_COUNTRY_URL = "https://stat.ripe.net/data/rir-stats-country/data.json"
RETRY_FAILED_AFTER = 3600

_HOLDER_RE = re.compile(r"^(?P<rest>.*?),\s*(?P<cc>[A-Z]{2})$")
_HANDLE_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]*$")


def special_name(asn: int) -> str | None:
    """Names for reserved ranges, which have no registry entry."""
    if asn == 0:
        return "Unknown / local"
    if asn == 23456:
        return "AS_TRANS"
    if 64496 <= asn <= 64511 or 65536 <= asn <= 65551:
        return "Documentation AS"
    if 64512 <= asn <= 65534 or 4200000000 <= asn <= 4294967294:
        return "Private AS"
    if asn in (65535, 4294967295):
        return "Reserved AS"
    return None


def parse_holder(holder: str | None) -> tuple[str | None, str | None, str | None]:
    """Split a RIPEstat holder string into (handle, description, country).

    "GOOGLE - Google LLC, US" -> ("GOOGLE", "Google LLC", "US")
    """
    if not holder:
        return None, None, None
    holder = holder.strip()
    country = None
    m = _HOLDER_RE.match(holder)
    if m:
        holder, country = m.group("rest").strip(), m.group("cc")
    parts = holder.split(None, 1)
    if parts and _HANDLE_RE.match(parts[0]):
        handle = parts[0]
        desc = parts[1].strip().lstrip("-").strip() if len(parts) > 1 else None
    else:
        handle, desc = None, holder
    return handle[:128] if handle else None, (desc[:255] if desc else None), country


def display_name(info: AsInfo | None, asn: int) -> str:
    if info is not None:
        if info.custom_name:
            return info.custom_name
        if info.description:
            return info.description
        if info.pdb_name:
            return info.pdb_name
        if info.handle:
            return info.handle
    return special_name(asn) or ""


async def _country(client: httpx.AsyncClient, asn: int, sourceapp: str) -> str | None:
    try:
        resp = await client.get(RIPESTAT_COUNTRY_URL, params={"resource": f"AS{asn}", "sourceapp": sourceapp})
        resp.raise_for_status()
        located = (resp.json().get("data") or {}).get("located_resources") or []
        cc = located[0].get("location") if located else None
        return cc.upper()[:2] if isinstance(cc, str) and len(cc) == 2 else None
    except (httpx.HTTPError, ValueError):
        return None


async def lookup_ripestat(client: httpx.AsyncClient, asn: int, sourceapp: str) -> tuple[str | None, str | None, str | None]:
    params = {"resource": f"AS{asn}", "sourceapp": sourceapp}
    resp, country = await asyncio.gather(client.get(RIPESTAT_URL, params=params), _country(client, asn, sourceapp))
    resp.raise_for_status()
    data = resp.json().get("data") or {}
    handle, desc, cc = parse_holder(data.get("holder"))
    return handle, desc, cc or country


def _pending(engine: Engine, batch: int, ttl_days: int, now: int, asns: list[int] | None = None) -> list[int]:
    with engine.connect() as conn:
        q = select(AsInfo.asn)
        if asns is not None:
            q = q.where(AsInfo.asn.in_(asns))
        else:
            q = q.where(
                AsInfo.updated_at < now - ttl_days * 86400,
                or_(AsInfo.failed_at.is_(None), AsInfo.failed_at < now - RETRY_FAILED_AFTER),
            )
        q = q.order_by(AsInfo.updated_at, AsInfo.asn).limit(batch)
        return [int(a) for a in conn.execute(q).scalars()]


def _store(engine: Engine, results: dict[int, tuple | None], now: int) -> None:
    with engine.begin() as conn:
        for asn, res in results.items():
            if res is None:
                conn.execute(update(AsInfo).where(AsInfo.asn == asn).values(failed_at=now))
            else:
                handle, desc, cc = res
                conn.execute(
                    update(AsInfo)
                    .where(AsInfo.asn == asn)
                    .values(handle=handle, description=desc, country=cc, updated_at=now, failed_at=None)
                )


async def refresh(
    engine: Engine,
    *,
    batch: int = 200,
    concurrency: int = 4,
    asns: list[int] | None = None,
    client: httpx.AsyncClient | None = None,
) -> int:
    """Look up names for ASes that were never looked up or are stale. Returns the count."""
    now = int(time.time())
    with engine.connect() as conn:
        cfg = appsettings.get_many(conn, ["ripestat_enabled", "ripestat_sourceapp", "asname_ttl_days"])
    if not cfg["ripestat_enabled"] and asns is None:
        return 0
    pending = await asyncio.to_thread(_pending, engine, batch, int(cfg["asname_ttl_days"]), now, asns)
    if not pending:
        return 0

    results: dict[int, tuple | None] = {}
    remote = []
    for asn in pending:
        name = special_name(asn)
        if name is not None:
            results[asn] = (None, name, None)
        else:
            remote.append(asn)

    sourceapp = cfg["ripestat_sourceapp"] or "as-stats-python"
    own_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=20, headers={"User-Agent": f"as-stats-python/{__version__}"})
    sem = asyncio.Semaphore(concurrency)
    throttled = False

    async def one(asn: int) -> None:
        nonlocal throttled
        if throttled:
            return
        async with sem:
            try:
                results[asn] = await lookup_ripestat(client, asn, sourceapp)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    throttled = True
                    log.warning("RIPEstat rate limit hit, pausing lookups")
                    return
                log.debug("RIPEstat lookup for AS%d failed: %s", asn, exc)
                results[asn] = None
            except (httpx.HTTPError, ValueError) as exc:
                log.debug("RIPEstat lookup for AS%d failed: %s", asn, exc)
                results[asn] = None

    try:
        await asyncio.gather(*(one(a) for a in remote))
    finally:
        if own_client:
            await client.aclose()
    await asyncio.to_thread(_store, engine, results, now)
    ok = sum(1 for r in results.values() if r is not None)
    if ok:
        log.info("looked up %d AS name(s)", ok)
    return ok
