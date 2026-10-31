"""Startup with web and collector in separate containers: setup must be safe to run concurrently."""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from sqlalchemy import func, inspect, select

from asstats.models import Base, User

from conftest import EXTERNAL_DB

ROOT = Path(__file__).resolve().parent.parent


def _env(tmp_path, url):
    env = dict(os.environ, ASSTATS_DATA_DIR=str(tmp_path), ASSTATS_LOG_LEVEL="ERROR", ASSTATS_DATABASE_URL=url)
    env.pop("ASSTATS_ADMIN_PASSWORD", None)
    return env


# Imports take a while; processes wait for a common start signal so they really run at the same moment.
_CODE = """
import os, sys, time
from asstats.cli import main
from asstats.web import app  # noqa: F401  (import everything before the start signal)
go = os.environ.get("GO_FILE")
while go and not os.path.exists(go):
    time.sleep(0.002)
sys.exit(main(["init", *sys.argv[1:]]))
"""


def _init(env, *args):
    return subprocess.Popen([sys.executable, "-c", _CODE, *args], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def _users(engine):
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(User)).scalar_one()


def test_concurrent_init_on_empty_database(engine, tmp_path):
    """Several containers starting at the same moment: no crash, one schema, one admin."""
    url = EXTERNAL_DB or f"sqlite:///{tmp_path / 'asstats.db'}"
    if EXTERNAL_DB:
        Base.metadata.drop_all(engine)
        target = engine
    else:
        from asstats import db

        target = db._make_engine(url)
    try:
        for n in range(4):
            if EXTERNAL_DB:
                Base.metadata.drop_all(target)
            else:
                target.dispose()
                for f in tmp_path.glob("asstats.db*"):
                    f.unlink()
            go = tmp_path / f"go{n}"
            env = {**_env(tmp_path, url), "GO_FILE": str(go)}
            # web-like starts (with admin) and collector-like starts (schema only), released together
            procs = [_init(env) for _ in range(3)] + [_init(env, "--schema-only") for _ in range(3)]
            time.sleep(3)
            go.touch()
            for p in procs:
                out, _ = p.communicate(timeout=120)
                assert p.returncode == 0, out
            assert set(Base.metadata.tables) <= set(inspect(target).get_table_names())
            assert _users(target) == 1
    finally:
        if not EXTERNAL_DB:
            target.dispose()


def test_schema_only_creates_no_admin(tmp_path):
    from asstats import db

    url = f"sqlite:///{tmp_path / 'asstats.db'}"
    p = _init(_env(tmp_path, url), "--schema-only")
    out, _ = p.communicate(timeout=60)
    assert p.returncode == 0, out
    eng = db._make_engine(url)
    try:
        assert "users" in inspect(eng).get_table_names()
        assert _users(eng) == 0
        # the web side creates it later
        p = _init(_env(tmp_path, url))
        out, _ = p.communicate(timeout=60)
        assert p.returncode == 0 and _users(eng) == 1, out
    finally:
        eng.dispose()


@pytest.mark.skipif(os.geteuid() == 0, reason="the entrypoint drops root privileges to a user that only exists in the image")
@pytest.mark.parametrize(
    "command, init_args",
    [
        (["asstats", "collector"], "init --schema-only"),
        (["asstats", "web"], "init"),
        (["asstats", "run"], "init"),
    ],
)
def test_entrypoint_role(tmp_path, command, init_args):
    """The entrypoint prepares the schema for every service, but only web/run create the admin."""
    calls = tmp_path / "calls"
    fake = tmp_path / "bin" / "asstats"
    fake.parent.mkdir()
    fake.write_text(f'#!/bin/sh\necho "$*" >> {calls}\n')
    fake.chmod(0o755)
    env = dict(os.environ, PATH=f"{fake.parent}:{os.environ['PATH']}")
    sh = shutil.which("sh")
    subprocess.run([sh, str(ROOT / "docker" / "entrypoint.sh"), *command], env=env, check=True)
    assert calls.read_text().splitlines() == [init_args, " ".join(command[1:])]
