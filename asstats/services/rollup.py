"""Roll 5 minute data up into hourly and daily buckets, and expire old data."""

from __future__ import annotations

import logging
import time

from sqlalchemy import delete, func, insert, literal, select
from sqlalchemy.engine import Engine

from .. import appsettings
from ..config import get_config
from ..models import COUNTER_COLUMNS, LINK_TRAFFIC, TRAFFIC, UserSession
from .timeseries import FINER, floor_to, retention

log = logging.getLogger(__name__)

FAMILIES = ((TRAFFIC, ("link_id", "asn")), (LINK_TRAFFIC, ("link_id",)))


def _grace() -> int:
    # a 5 minute bucket keeps receiving data until the flush after it ends
    return 300 + 2 * get_config().flush_interval


def _initial_watermark(engine: Engine, step: int, now: int) -> int:
    finer = FINER[step]
    with engine.connect() as conn:
        first = conn.execute(select(func.min(TRAFFIC[finer].c.ts))).scalar()
        first_link = conn.execute(select(func.min(LINK_TRAFFIC[finer].c.ts))).scalar()
    candidates = [t for t in (first, first_link) if t is not None]
    if not candidates:
        return floor_to(now, step)
    return floor_to(int(min(candidates)), step)


def rollup_period(engine: Engine, step: int, period: int) -> None:
    finer = FINER[step]
    with engine.begin() as conn:
        for family, keys in FAMILIES:
            src, dst = family[finer], family[step]
            conn.execute(delete(dst).where(dst.c.ts == period))
            sel = (
                select(
                    literal(period).label("ts"),
                    *[src.c[k] for k in keys],
                    *[func.sum(src.c[c]).label(c) for c in COUNTER_COLUMNS],
                )
                .where(src.c.ts >= period, src.c.ts < period + step)
                .group_by(*[src.c[k] for k in keys])
            )
            conn.execute(insert(dst).from_select(["ts", *keys, *COUNTER_COLUMNS], sel))
        appsettings.set_value(conn, appsettings.rollup_key(step), period + step)


def run_rollups(engine: Engine, now: int | None = None, max_periods: int = 24 * 60) -> int:
    """Roll up every completed period. Returns how many periods were processed."""
    now = int(now or time.time())
    done = 0
    for step in (3600, 86400):
        with engine.connect() as conn:
            wm = appsettings.get_value(conn, appsettings.rollup_key(step), 0)
            wm_hour = appsettings.get_value(conn, appsettings.rollup_key(3600), 0)
        if not wm:
            wm = _initial_watermark(engine, step, now)
            with engine.begin() as conn:
                appsettings.set_value(conn, appsettings.rollup_key(step), wm)
        if step == 3600:
            limit = floor_to(now - _grace(), 3600)
        else:
            limit = floor_to(int(wm_hour or 0), 86400)
        count = 0
        while wm + step <= limit and count < max_periods:
            rollup_period(engine, step, wm)
            wm += step
            count += 1
        if count:
            log.info("rolled up %d period(s) into %ds buckets", count, step)
        done += count
    return done


def apply_retention(engine: Engine, now: int | None = None) -> None:
    now = int(now or time.time())
    with engine.connect() as conn:
        keep = retention(conn)
        wm_hour = appsettings.get_value(conn, appsettings.rollup_key(3600), 0) or 0
        wm_day = appsettings.get_value(conn, appsettings.rollup_key(86400), 0) or 0
    cutoffs = {
        # never drop finer data that has not been rolled up yet
        300: min(now - keep[300] * 86400, int(wm_hour)),
        3600: min(now - keep[3600] * 86400, int(wm_day)),
        86400: now - keep[86400] * 86400,
    }
    with engine.begin() as conn:
        for step, cutoff in cutoffs.items():
            for family, _ in FAMILIES:
                t = family[step]
                conn.execute(delete(t).where(t.c.ts < cutoff))
        conn.execute(delete(UserSession).where(UserSession.expires_at < now))
