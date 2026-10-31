import os

import pytest
from sqlalchemy import event

os.environ.setdefault("ASSTATS_SCHEDULER", "0")
os.environ.setdefault("ASSTATS_ADMIN_PASSWORD", "admin-password")

from asstats import db  # noqa: E402
from asstats.models import Base  # noqa: E402

# Run the suite against MariaDB/MySQL/PostgreSQL by pointing this at an empty database.
EXTERNAL_DB = os.environ.get("ASSTATS_TEST_DATABASE_URL")


@pytest.fixture()
def engine(tmp_path):
    if EXTERNAL_DB:
        eng = db._make_engine(EXTERNAL_DB)
        if eng.dialect.name in ("mysql", "mariadb"):
            # A leaked connection holds metadata locks and DROP TABLE would wait for it
            # practically forever (the default is a year); fail within seconds instead.
            @event.listens_for(eng, "connect")
            def _short_lock_wait(dbapi_conn, _record):
                with dbapi_conn.cursor() as cur:
                    cur.execute("SET SESSION lock_wait_timeout = 20")

        Base.metadata.drop_all(eng)
    else:
        eng = db._make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(eng)
    db.set_engine(eng)
    yield eng
    leaked = eng.pool.checkedout()
    eng.dispose()
    # Use `with engine.connect() as conn:`; an unclosed connection keeps a transaction
    # open, which blocks the next test's DROP TABLE on MariaDB.
    assert leaked == 0, f"the test left {leaked} database connection(s) open"
