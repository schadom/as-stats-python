"""PeeringDB sync: the IXs our ASN is present at and their members.

Uses a handful of bulk requests, so it stays well within the anonymous rate
limit. An API key can be configured for higher limits.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx
from sqlalchemy import delete, insert, update
from sqlalchemy.engine import Engine

from .. import __version__, appsettings
from ..db import insert_ignore
from ..models import AsInfo, PdbIx, PdbIxMember

log = logging.getLogger(__name__)

API = "https://www.peeringdb.com/api"
CHUNK = 400
PAUSE = 1.0  # seconds between requests, keeps us under the anonymous rate limit


class PeeringDBError(Exception):
    pass


async def _get(client: httpx.AsyncClient, path: str, params: dict) -> list[dict]:
    await asyncio.sleep(PAUSE)
    resp = await client.get(f"{API}/{path}", params=params)
    if resp.status_code == 429:
        raise PeeringDBError("PeeringDB rate limit reached, try again later or configure an API key")
    if resp.status_code in (401, 403):
        raise PeeringDBError("PeeringDB rejected the API key")
    resp.raise_for_status()
    return resp.json().get("data", [])


def _chunks(items: list, size: int = CHUNK):
    for i in range(0, len(items), size):
        yield items[i : i + size]


async def refresh(engine: Engine, client: httpx.AsyncClient | None = None) -> dict:
    """Fetch IX memberships for the configured ASN. Returns a small summary."""
    with engine.connect() as conn:
        cfg = appsettings.get_many(conn, ["my_asn", "peeringdb_api_key", "peeringdb_enabled"])
    my_asn = cfg["my_asn"]
    if not cfg["peeringdb_enabled"] or not my_asn:
        return {"ixs": 0, "members": 0, "skipped": True}

    headers = {"User-Agent": f"as-stats-python/{__version__}"}
    if cfg["peeringdb_api_key"]:
        headers["Authorization"] = f"Api-Key {cfg['peeringdb_api_key']}"
    own_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=60, headers=headers)
    else:
        client.headers.update(headers)
    now = int(time.time())
    try:
        mine = await _get(client, "netixlan", {"asn": int(my_asn)})
        ix_ids = sorted({row["ix_id"] for row in mine})
        ixs: list[dict] = []
        members: list[dict] = []
        for chunk in _chunks(ix_ids):
            ids = ",".join(map(str, chunk))
            ixs += await _get(client, "ix", {"id__in": ids})
            members += await _get(client, "netixlan", {"ix_id__in": ids})
        net_ids = sorted({m["net_id"] for m in members if m.get("net_id")})
        nets: list[dict] = []
        for chunk in _chunks(net_ids):
            nets += await _get(client, "net", {"id__in": ",".join(map(str, chunk))})
    except httpx.HTTPError as exc:
        raise PeeringDBError(f"PeeringDB request failed: {exc}") from exc
    finally:
        if own_client:
            await client.aclose()

    with engine.begin() as conn:
        conn.execute(delete(PdbIxMember))
        conn.execute(delete(PdbIx))
        if ixs:
            conn.execute(
                insert(PdbIx),
                [
                    {
                        "id": ix["id"],
                        "name": (ix.get("name") or f"IX {ix['id']}")[:255],
                        "name_long": (ix.get("name_long") or None),
                        "city": (ix.get("city") or None),
                        "country": (ix.get("country") or None),
                        "updated_at": now,
                    }
                    for ix in ixs
                ],
            )
        if members:
            conn.execute(
                insert(PdbIxMember),
                [
                    {
                        "ix_id": m["ix_id"],
                        "asn": m["asn"],
                        "ipaddr4": m.get("ipaddr4"),
                        "ipaddr6": m.get("ipaddr6"),
                        "speed": m.get("speed"),
                        "is_rs_peer": bool(m.get("is_rs_peer")),
                    }
                    for m in members
                    if m.get("operational", True)
                ],
            )
        if nets:
            insert_ignore(conn, AsInfo.__table__, [{"asn": n["asn"], "updated_at": 0} for n in nets], ["asn"])
            for n in nets:
                conn.execute(
                    update(AsInfo).where(AsInfo.asn == n["asn"]).values(pdb_name=(n.get("name") or None), pdb_id=n["id"])
                )
        appsettings.set_value(conn, appsettings.PDB_LAST_REFRESH, now)
        appsettings.set_value(conn, appsettings.PDB_LAST_ERROR, None)
    log.info("PeeringDB: %d IXs, %d member ports", len(ixs), len(members))
    return {"ixs": len(ixs), "members": len(members), "skipped": False}
