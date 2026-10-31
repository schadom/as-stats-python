"""Delete all collected statistics, keeping the configuration (Settings, General, Danger zone)."""

from __future__ import annotations

import logging

from sqlalchemy import delete
from sqlalchemy.engine import Engine

from ..models import Base, Setting

log = logging.getLogger(__name__)

# Every table of collected traffic, in all resolutions: traffic_*, link_traffic_*,
# and optional ones such as prefix_traffic_* when present.
STATS_PREFIXES = ("traffic_", "link_traffic_", "prefix_traffic_")
# Bookkeeping that only makes sense together with the deleted data.
STATS_SETTING_PREFIXES = ("rollup_wm_", "prefix_compact_wm")


def stats_tables():
    return [t for t in Base.metadata.sorted_tables if t.name.startswith(STATS_PREFIXES)]


def reset_statistics(engine: Engine) -> dict[str, int]:
    """Delete all traffic data in one transaction. Returns deleted rows per table."""
    deleted: dict[str, int] = {}
    with engine.begin() as conn:
        for t in stats_tables():
            deleted[t.name] = conn.execute(delete(t)).rowcount or 0
        for p in STATS_SETTING_PREFIXES:
            conn.execute(delete(Setting).where(Setting.key.startswith(p)))
    log.warning("all statistics deleted: %d rows", sum(deleted.values()))
    return deleted
