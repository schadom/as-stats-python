"""Process configuration from environment variables.

Only things needed before the database is reachable live here. Everything an
operator would want to change at runtime (links, exporters, retention, API
keys, ...) is stored in the database and edited in the web UI.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(f"ASSTATS_{name}")
    if value is None or value == "":
        return default
    return value


def _env_bool(name: str, default: bool) -> bool:
    value = _env(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    value = _env(name)
    return int(value) if value is not None else default


@dataclass(frozen=True)
class Config:
    data_dir: Path
    database_url: str
    listen_address: str
    netflow_port: int
    sflow_port: int
    web_host: str
    web_port: int
    cookie_secure: bool
    session_hours: int
    admin_user: str
    admin_password: str | None
    log_level: str
    static_dir: Path | None
    flush_interval: int
    scheduler_enabled: bool
    trusted_proxies: tuple[str, ...]


@lru_cache(maxsize=1)
def get_config() -> Config:
    data_dir = Path(_env("DATA_DIR", "./data")).resolve()
    database_url = _env("DATABASE_URL") or f"sqlite:///{data_dir / 'asstats.db'}"
    static_default = Path(__file__).parent / "static"
    static_dir = Path(_env("STATIC_DIR", str(static_default)))
    return Config(
        data_dir=data_dir,
        database_url=database_url,
        listen_address=_env("LISTEN_ADDRESS", "::"),
        netflow_port=_env_int("NETFLOW_PORT", 9000),
        sflow_port=_env_int("SFLOW_PORT", 6343),
        web_host=_env("WEB_HOST", "0.0.0.0"),
        web_port=_env_int("WEB_PORT", 8080),
        cookie_secure=_env_bool("COOKIE_SECURE", False),
        session_hours=_env_int("SESSION_HOURS", 24 * 7),
        admin_user=_env("ADMIN_USER", "admin"),
        admin_password=_env("ADMIN_PASSWORD"),
        log_level=_env("LOG_LEVEL", "INFO").upper(),
        static_dir=static_dir if static_dir.exists() else None,
        flush_interval=max(10, _env_int("FLUSH_INTERVAL", 60)),
        scheduler_enabled=_env_bool("SCHEDULER", True),
        trusted_proxies=tuple(
            p.strip() for p in (_env("TRUSTED_PROXIES", "") or "").split(",") if p.strip()
        ),
    )
