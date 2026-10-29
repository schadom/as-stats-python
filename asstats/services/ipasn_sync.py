"""Download the IP-to-AS dataset into the data directory once a day."""

from __future__ import annotations

import asyncio
import gzip
import logging
import time
from pathlib import Path

import httpx
from sqlalchemy.engine import Engine

from .. import __version__, appsettings
from ..ipasn import DEFAULT_URL, FILENAME, parse_gzip, save_binary

log = logging.getLogger(__name__)

REFRESH_INTERVAL = 86400
RETRY_INTERVAL = 3600
MIN_RANGES = 1000


def _convert(download: Path, target: Path):
    """Parse the downloaded dataset (gzip or plain text) and store it in binary form."""
    with open(download, "rb") as fh:
        gz = fh.read(2) == b"\x1f\x8b"
    if not gz:
        plain = download.read_bytes()
        with gzip.open(download, "wb") as fh:
            fh.write(plain)
    table = parse_gzip(download)
    if len(table) < MIN_RANGES:
        raise ValueError(f"the file has only {len(table)} usable ranges, keeping the previous one")
    save_binary(table, target)
    return table


async def refresh(engine: Engine, data_dir: Path, force: bool = False, client: httpx.AsyncClient | None = None) -> dict:
    with engine.connect() as conn:
        cfg = appsettings.get_many(
            conn, ["ipasn_enabled", "ipasn_url", appsettings.IPASN_LAST_REFRESH, appsettings.IPASN_LAST_ATTEMPT]
        )
    if not cfg["ipasn_enabled"] and not force:
        return {"skipped": True}
    now = time.time()
    target = data_dir / FILENAME
    if not force:
        fresh = target.exists() and int(cfg[appsettings.IPASN_LAST_REFRESH] or 0) > now - REFRESH_INTERVAL
        backoff = int(cfg[appsettings.IPASN_LAST_ATTEMPT] or 0) > now - RETRY_INTERVAL
        if fresh or backoff:
            return {"skipped": True}

    with engine.begin() as conn:
        appsettings.set_value(conn, appsettings.IPASN_LAST_ATTEMPT, int(now))
    url = cfg["ipasn_url"] or DEFAULT_URL
    tmp = data_dir / "ip2asn.download"
    own = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=120, follow_redirects=True, headers={"User-Agent": f"as-stats-python/{__version__}"})
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(tmp, "wb") as fh:
                async for chunk in resp.aiter_bytes(1 << 16):
                    fh.write(chunk)
        table = await asyncio.to_thread(_convert, tmp, target)
    except Exception as exc:
        with engine.begin() as conn:
            appsettings.set_value(conn, appsettings.IPASN_LAST_ERROR, f"{type(exc).__name__}: {exc}")
        raise
    finally:
        tmp.unlink(missing_ok=True)
        if own:
            await client.aclose()
    with engine.begin() as conn:
        appsettings.set_value(conn, appsettings.IPASN_LAST_REFRESH, int(time.time()))
        appsettings.set_value(conn, appsettings.IPASN_LAST_ERROR, None)
        appsettings.set_value(conn, appsettings.IPASN_RANGES, len(table))
    log.info("IP-to-AS dataset updated: %d ranges", len(table))
    return {"ranges": len(table), "skipped": False}
