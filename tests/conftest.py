import os

import pytest

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
        Base.metadata.drop_all(eng)
    else:
        eng = db._make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(eng)
    db.set_engine(eng)
    yield eng
    eng.dispose()
