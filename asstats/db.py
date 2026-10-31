"""Engine/session handling and the few dialect specific statements we need.

SQLite (default) and MariaDB/MySQL are supported. PostgreSQL works as well
since only standard SQL plus ON CONFLICT upserts are used.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Iterable, Iterator, Sequence

from sqlalchemy import Table, create_engine, event, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_config
from .models import Base

log = logging.getLogger(__name__)

_engine: Engine | None = None
_sessionmaker: sessionmaker | None = None


def _make_engine(url: str) -> Engine:
    kwargs: dict = {}
    if url.startswith("sqlite"):
        if ":memory:" not in url and url.startswith("sqlite:///"):
            from pathlib import Path

            Path(url[len("sqlite:///") :]).parent.mkdir(parents=True, exist_ok=True)
        kwargs["connect_args"] = {"timeout": 60, "check_same_thread": False}
    else:
        kwargs.update(pool_pre_ping=True, pool_recycle=1800, pool_size=5, max_overflow=10)
    engine = create_engine(url, **kwargs)

    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA busy_timeout=60000")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


def get_engine() -> Engine:
    global _engine, _sessionmaker
    if _engine is None:
        _engine = _make_engine(get_config().database_url)
        _sessionmaker = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def set_engine(engine: Engine) -> None:
    """Used by tests to plug in a temporary database."""
    global _engine, _sessionmaker
    _engine = engine
    _sessionmaker = sessionmaker(bind=engine, expire_on_commit=False)


def new_session() -> Session:
    get_engine()
    assert _sessionmaker is not None
    return _sessionmaker()


@contextmanager
def session_scope() -> Iterator[Session]:
    session = new_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def wait_for_db(timeout: float = 120.0) -> None:
    """Block until the database accepts connections (MariaDB may start slower than us)."""
    deadline = time.monotonic() + timeout
    delay = 1.0
    while True:
        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except Exception as exc:  # pragma: no cover - depends on environment
            if time.monotonic() > deadline:
                raise
            log.warning("database not reachable yet (%s), retrying in %.0fs", exc, delay)
            time.sleep(delay)
            delay = min(delay * 2, 10.0)


# Columns added after the first release: (table, column, SQL type and default).
# create_all() only creates missing tables, so existing databases get these here.
ADDED_COLUMNS = [
    ("exporters", "as_source", "VARCHAR(8) NOT NULL DEFAULT 'auto'"),
    ("links", "capacity_mbit", "INTEGER NULL"),
]


def migrate(engine: Engine) -> None:
    from sqlalchemy import inspect

    insp = inspect(engine)
    tables = set(insp.get_table_names())
    for table, column, ddl in ADDED_COLUMNS:
        if table not in tables:
            continue
        if column in {c["name"] for c in insp.get_columns(table)}:
            continue
        log.info("adding column %s.%s", table, column)
        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
        except Exception:
            # another process (web/collector start together) may have added it
            if column not in {c["name"] for c in inspect(engine).get_columns(table)}:
                raise


def init_db() -> None:
    wait_for_db()
    Base.metadata.create_all(get_engine())
    migrate(get_engine())


# ---------------------------------------------------------------- dialect helpers


def _insert_for(conn: Connection):
    name = conn.dialect.name
    if name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    elif name in ("mysql", "mariadb"):
        from sqlalchemy.dialects.mysql import insert
    elif name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:  # pragma: no cover
        raise RuntimeError(f"unsupported database dialect: {name}")
    return insert, name


def upsert(
    conn: Connection,
    table: Table,
    rows: Sequence[dict],
    keys: Iterable[str],
    add: Iterable[str] = (),
    replace: Iterable[str] = (),
    coalesce: Iterable[str] = (),
) -> None:
    """INSERT rows; on key conflict add to `add` columns, overwrite `replace`
    columns and overwrite `coalesce` columns only when the new value is not NULL."""
    if not rows:
        return
    from sqlalchemy import func

    insert, dialect = _insert_for(conn)
    stmt = insert(table)
    keys = list(keys)
    if dialect in ("mysql", "mariadb"):
        new = stmt.inserted
    else:
        new = stmt.excluded
    updates = {}
    for col in add:
        updates[col] = table.c[col] + new[col]
    for col in replace:
        updates[col] = new[col]
    for col in coalesce:
        updates[col] = func.coalesce(new[col], table.c[col])
    if dialect in ("mysql", "mariadb"):
        stmt = stmt.on_duplicate_key_update(updates) if updates else stmt.prefix_with("IGNORE")
    else:
        if updates:
            stmt = stmt.on_conflict_do_update(index_elements=keys, set_=updates)
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=keys)
    conn.execute(stmt, list(rows))


def insert_ignore(conn: Connection, table: Table, rows: Sequence[dict], keys: Iterable[str]) -> None:
    upsert(conn, table, rows, keys)
