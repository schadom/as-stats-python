"""Runtime settings stored in the `settings` table as JSON values."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from .db import upsert
from .models import Setting

# Settings an admin can edit in the UI, with defaults.
DEFAULTS: dict[str, Any] = {
    "site_name": "AS-Stats",
    "my_asn": None,
    "retention_5m_days": 3,
    "retention_1h_days": 90,
    "retention_1d_days": 1825,
    "ripestat_enabled": True,
    "ripestat_sourceapp": "as-stats-python",
    "asname_ttl_days": 30,
    "peeringdb_enabled": True,
    "peeringdb_api_key": "",
    "top_default_limit": 20,
    # How bit rates are written: "b/s" (12.4 Mb/s) or "bit/s" (12.4 Mbit/s).
    "rate_unit": "b/s",
    "ipasn_enabled": True,
    "ipasn_url": "https://iptoasn.com/data/ip2asn-combined.tsv.gz",
}

SECRET_KEYS = {"peeringdb_api_key"}

# Internal bookkeeping keys, not editable.
CONFIG_VERSION = "config_version"
COLLECTOR_HEARTBEAT = "collector_heartbeat"
PDB_LAST_REFRESH = "pdb_last_refresh"
PDB_LAST_ERROR = "pdb_last_error"
PDB_LAST_ATTEMPT = "pdb_last_attempt"
IPASN_LAST_REFRESH = "ipasn_last_refresh"
IPASN_LAST_ATTEMPT = "ipasn_last_attempt"
IPASN_LAST_ERROR = "ipasn_last_error"
IPASN_RANGES = "ipasn_ranges"


def rollup_key(step: int) -> str:
    return f"rollup_wm_{step}"


def _decode(raw: str | None, default: Any) -> Any:
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except ValueError:
        return default


def get_value(db: Session | Connection, key: str, default: Any = None) -> Any:
    if default is None:
        default = DEFAULTS.get(key)
    raw = db.execute(select(Setting.value).where(Setting.key == key)).scalar_one_or_none()
    return _decode(raw, default)


def get_many(db: Session | Connection, keys: list[str]) -> dict[str, Any]:
    rows = dict(db.execute(select(Setting.key, Setting.value).where(Setting.key.in_(keys))).all())
    return {k: _decode(rows.get(k), DEFAULTS.get(k)) for k in keys}


def get_all_public(db: Session | Connection) -> dict[str, Any]:
    values = get_many(db, list(DEFAULTS))
    return values


def set_value(db: Session | Connection, key: str, value: Any) -> None:
    conn = db.connection() if isinstance(db, Session) else db
    upsert(conn, Setting.__table__, [{"key": key, "value": json.dumps(value)}], ["key"], replace=["value"])


def bump_config_version(db: Session | Connection) -> None:
    import time

    set_value(db, CONFIG_VERSION, time.time())
