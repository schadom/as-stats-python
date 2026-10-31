"""Background jobs running inside the web process."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

from sqlalchemy.engine import Engine

from .. import appsettings
from ..config import get_config
from . import asnames, ipasn_sync, peeringdb, prefixes
from .rollup import apply_retention, run_rollups

log = logging.getLogger(__name__)

PDB_INTERVAL = 86400
PDB_RETRY = 3600


class Scheduler:
    def __init__(self, engine: Engine):
        self.engine = engine
        self.tasks: list[asyncio.Task] = []
        self.pdb_now = asyncio.Event()
        self.names_now = asyncio.Event()
        self.last_run: dict[str, int] = {}

    def start(self) -> None:
        self.tasks = [
            asyncio.create_task(self._every("rollup", 120, 5, self._rollup)),
            asyncio.create_task(self._every("retention", 3600, 60, self._retention)),
            asyncio.create_task(self._every("asnames", 30, 10, self._asnames, self.names_now)),
            asyncio.create_task(self._every("peeringdb", 300, 20, self._peeringdb, self.pdb_now)),
            asyncio.create_task(self._every("ipasn", 600, 3, self._ipasn)),
            asyncio.create_task(self._every("prefixes", 300, 30, self._prefixes)),
        ]

    async def stop(self) -> None:
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def _every(
        self,
        name: str,
        interval: float,
        initial_delay: float,
        job: Callable[[], Awaitable[None]],
        wake: asyncio.Event | None = None,
    ) -> None:
        await asyncio.sleep(initial_delay)
        while True:
            try:
                await job()
                self.last_run[name] = int(time.time())
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("background job %s failed", name)
            if wake is None:
                await asyncio.sleep(interval)
            else:
                try:
                    await asyncio.wait_for(wake.wait(), timeout=interval)
                except asyncio.TimeoutError:
                    pass
                wake.clear()

    async def _ipasn(self) -> None:
        await ipasn_sync.refresh(self.engine, get_config().data_dir)

    async def _rollup(self) -> None:
        await asyncio.to_thread(run_rollups, self.engine)

    async def _retention(self) -> None:
        await asyncio.to_thread(apply_retention, self.engine)
        await asyncio.to_thread(prefixes.apply_retention, self.engine)

    async def _prefixes(self) -> None:
        # also runs while the feature is off, so data collected before switching it off is still compacted
        await asyncio.to_thread(prefixes.run_compaction, self.engine)

    async def _asnames(self) -> None:
        # keep going while there is a backlog (first start sees thousands of ASes)
        for _ in range(20):
            if await asnames.refresh(self.engine, batch=100) < 50:
                break

    async def _peeringdb(self) -> None:
        def state():
            with self.engine.connect() as conn:
                return appsettings.get_many(
                    conn,
                    ["my_asn", "peeringdb_enabled", appsettings.PDB_LAST_REFRESH, appsettings.PDB_LAST_ATTEMPT, "pdb_synced_asn"],
                )

        s = await asyncio.to_thread(state)
        if not s["my_asn"] or not s["peeringdb_enabled"]:
            return
        now = time.time()
        stale = s["pdb_synced_asn"] != s["my_asn"] or int(s[appsettings.PDB_LAST_REFRESH] or 0) < now - PDB_INTERVAL
        backoff_over = int(s[appsettings.PDB_LAST_ATTEMPT] or 0) < now - PDB_RETRY
        due = self.pdb_now.is_set() or (stale and backoff_over)
        if not due:
            return
        await self.refresh_peeringdb()

    async def refresh_peeringdb(self) -> dict:
        with self.engine.begin() as conn:
            appsettings.set_value(conn, appsettings.PDB_LAST_ATTEMPT, int(time.time()))
        try:
            result = await peeringdb.refresh(self.engine)
            with self.engine.begin() as conn:
                appsettings.set_value(conn, "pdb_synced_asn", appsettings.get_value(conn, "my_asn"))
            return result
        except Exception as exc:
            with self.engine.begin() as conn:
                appsettings.set_value(conn, appsettings.PDB_LAST_ERROR, str(exc))
            raise
