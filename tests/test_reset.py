"""Danger zone: reset all statistics, keep the configuration."""

from fastapi.testclient import TestClient
from sqlalchemy import func, insert, select

from asstats import appsettings
from asstats.models import LINK_TRAFFIC, TRAFFIC, Exporter, User
from asstats.services.reset import stats_tables
from asstats.web.app import create_app

H = {"X-Requested-With": "XMLHttpRequest"}


def count(engine, table):
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(table)).scalar_one()


def test_reset_deletes_traffic_and_keeps_config(engine):
    row = {"ts": 3600, "link_id": 1, "in_v4": 1, "out_v4": 2, "in_v6": 3, "out_v6": 4}
    with engine.begin() as conn:
        for step in (300, 3600, 86400):
            conn.execute(insert(TRAFFIC[step]), [{**row, "asn": 15169}])
            conn.execute(insert(LINK_TRAFFIC[step]), [row])
        appsettings.set_value(conn, appsettings.rollup_key(3600), 7200)
        appsettings.set_value(conn, "site_name", "Vienna")
    names = {t.name for t in stats_tables()}
    assert {"traffic_5m", "traffic_1h", "traffic_1d", "link_traffic_5m", "link_traffic_1h", "link_traffic_1d"} <= names
    assert "users" not in names and "exporters" not in names

    with TestClient(create_app()) as c:
        assert c.post("/api/auth/login", json={"username": "admin", "password": "admin-password"}, headers=H).status_code == 200
        c.post("/api/exporters", json={"ip": "192.0.2.1", "name": "edge1"}, headers=H)
        # needs the confirmation word, and an admin
        assert c.post("/api/statistics/reset", json={"confirm": "yes"}, headers=H).status_code == 400
        assert count(engine, TRAFFIC[300]) == 1
        r = c.post("/api/statistics/reset", json={"confirm": "DELETE"}, headers=H)
        assert r.status_code == 200 and r.json()["deleted"] == 6
        assert c.get("/api/top?range=24h").json()["items"] == []

        c.post("/api/users", json={"username": "viewer", "password": "viewer-pass", "role": "readonly"}, headers=H)
        c.post("/api/auth/logout", headers=H)
        c.post("/api/auth/login", json={"username": "viewer", "password": "viewer-pass"}, headers=H)
        c.post("/api/auth/password", json={"current_password": "viewer-pass", "new_password": "viewer-pass-2"}, headers=H)
        assert c.post("/api/statistics/reset", json={"confirm": "DELETE"}, headers=H).status_code == 403

    for t in stats_tables():
        assert count(engine, t) == 0, t.name
    assert count(engine, Exporter.__table__) == 1 and count(engine, User.__table__) == 2
    with engine.connect() as conn:
        assert appsettings.get_value(conn, "site_name") == "Vienna"
        assert not appsettings.get_value(conn, appsettings.rollup_key(3600))
