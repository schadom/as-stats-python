import time

import pytest
from fastapi.testclient import TestClient

from asstats.collector.core import Collector
from asstats.flowgen import Flow, build_ipfix
from asstats.web.app import create_app

H = {"X-Requested-With": "XMLHttpRequest"}


@pytest.fixture()
def client(engine):
    with TestClient(create_app()) as c:
        yield c


def login(c, user="admin", password="admin-password"):
    r = c.post("/api/auth/login", json={"username": user, "password": password}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


def test_everything_requires_login(client):
    for path in ("/api/meta", "/api/top", "/api/links", "/api/status", "/api/series?asns=1", "/api/ix"):
        assert client.get(path).status_code == 401, path
    assert client.get("/api/health").status_code == 200


def test_csrf_header_required(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin-password"})
    assert r.status_code == 403


def test_login_logout_and_bad_password(client):
    assert client.post("/api/auth/login", json={"username": "admin", "password": "nope"}, headers=H).status_code == 401
    me = login(client)
    assert me["role"] == "admin"
    assert client.get("/api/auth/me").json()["username"] == "admin"
    assert client.post("/api/auth/logout", headers=H).status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_readonly_user_cannot_change_config(client):
    login(client)
    r = client.post("/api/users", json={"username": "viewer", "password": "viewer-pass", "role": "readonly"}, headers=H)
    assert r.status_code == 201
    assert r.json()["must_change_password"] is True
    client.post("/api/auth/logout", headers=H)

    login(client, "viewer", "viewer-pass")
    assert client.get("/api/meta").json() == {"detail": "password_change_required"}
    r = client.post("/api/auth/password", json={"current_password": "viewer-pass", "new_password": "viewer-pass-2"}, headers=H)
    assert r.status_code == 200
    assert client.get("/api/meta").status_code == 200
    assert client.get("/api/top").status_code == 200
    assert client.get("/api/settings").status_code == 403
    assert client.post("/api/exporters", json={"ip": "192.0.2.1", "name": "x"}, headers=H).status_code == 403
    assert client.get("/api/users").status_code == 403


def test_last_admin_is_protected(client):
    me = login(client)
    r = client.patch(f"/api/users/{me['id']}", json={"role": "readonly"}, headers=H)
    assert r.status_code == 400
    assert client.delete(f"/api/users/{me['id']}", headers=H).status_code == 400


def test_config_flow_and_stats(client, engine):
    login(client)
    # collector sees an unknown exporter: interfaces show up as discovered
    col = Collector(engine)
    col.maybe_reload(force=True)
    col.handle_netflow(build_ipfix([Flow(10, 99, 15169, 0, 1000)], sampling=100, if_names={10: "xe-0/0/1"}), "192.0.2.1")
    col.flush(*col.take_snapshot())
    disc = client.get("/api/discovered").json()
    assert {(d["exporter_ip"], d["ifindex"]) for d in disc} == {("192.0.2.1", 10), ("192.0.2.1", 99)}
    assert next(d for d in disc if d["ifindex"] == 10)["if_name"] == "xe-0/0/1"

    # configure exporter + link from the UI
    exp = client.post("/api/exporters", json={"ip": "192.0.2.1", "name": "edge1"}, headers=H).json()
    assert client.post("/api/exporters", json={"ip": "192.0.2.1", "name": "dup"}, headers=H).status_code == 409
    link = client.post(
        "/api/links",
        json={"tag": "transit1", "name": "Transit 1", "color": "#FF8800", "interfaces": [{"exporter_id": exp["id"], "ifindex": 10}]},
        headers=H,
    )
    assert link.status_code == 201, link.text
    link = link.json()
    assert link["color"] == "#ff8800"
    assert link["interfaces"][0]["if_name"] == "xe-0/0/1"
    clash = client.post(
        "/api/links", json={"tag": "other", "name": "Other", "interfaces": [{"exporter_id": exp["id"], "ifindex": 10}]}, headers=H
    )
    assert clash.status_code == 409

    # collector picks it up and accounts traffic
    assert col.maybe_reload() is True
    col.handle_netflow(build_ipfix([Flow(10, 99, 15169, 0, 1000), Flow(99, 10, 0, 13335, 10, v6=True)], with_templates=False), "192.0.2.1")
    snap, names = col.take_snapshot()
    col.flush(snap, names, now=time.time() - 300)  # previous bucket, so it is inside the queried range
    # the flows were bucketed by arrival time; move them one bucket back
    from sqlalchemy import update

    from asstats.models import LINK_TRAFFIC, TRAFFIC

    with engine.begin() as conn:
        for t in (TRAFFIC[300], LINK_TRAFFIC[300]):
            conn.execute(update(t).values(ts=t.c.ts - 300))

    top = client.get("/api/top?range=1h").json()
    assert [i["asn"] for i in top["items"]] == [15169, 13335]
    g = top["items"][0]
    assert g["in"] == 100_000 and g["links"][str(link["id"])] == {"in": 100_000, "out": 0}
    assert client.get("/api/top?range=1h&ip=6").json()["items"][0]["asn"] == 13335

    s = client.get(f"/api/series?asns=15169,13335&range=1h").json()
    assert s["step"] == 300 and len(s["buckets"]) == 12
    by_asn = {e["asn"]: e for e in s["series"]}
    assert sum(by_asn[15169]["in_v4"]) == 100_000
    assert sum(by_asn[13335]["out_v6"]) == 1_000

    ls = client.get("/api/links/series?range=1h").json()
    assert sum(ls["series"][0]["in_v4"]) == 100_000
    usage = client.get("/api/links/usage?range=1h").json()
    assert usage["links"][0]["top"][0]["asn"] == 15169

    detail = client.get("/api/as/15169?range=1h").json()
    assert detail["links"][0]["in_v4"] == 100_000
    assert client.patch("/api/as/15169", json={"custom_name": "Big G"}, headers=H).status_code == 200
    assert client.get("/api/search?q=big").json()[0]["asn"] == 15169
    assert client.get("/api/search?q=AS1516").json()[1]["asn"] == 15169

    status = client.get("/api/status").json()
    assert status["exporters"][0]["exporter_name"] == "edge1"
    assert status["collector"]["exporters"] == 1

    # settings
    st = client.put("/api/settings", json={"my_asn": 64500, "peeringdb_api_key": "secret"}, headers=H).json()
    assert st["my_asn"] == 64500 and st["peeringdb_api_key"] == "********"
    assert client.put("/api/settings", json={"retention_5m_days": 0}, headers=H).status_code == 422

    # deleting the link purges its traffic
    assert client.delete(f"/api/links/{link['id']}", headers=H).status_code == 200
    assert client.get("/api/top?range=1h&links=999").json()["items"] == []


def test_ranges_validated(client):
    login(client)
    assert client.get("/api/top?range=5y").status_code == 400
    assert client.get("/api/top?start=200&end=100").status_code == 400
    assert client.get("/api/series?asns=" + ",".join(str(i) for i in range(101))).status_code == 400


def test_short_ranges_use_five_minute_buckets(client):
    login(client)
    for rng, buckets in (("1h", 12), ("3h", 36)):
        s = client.get(f"/api/links/series?range={rng}").json()
        assert s["step"] == 300 and len(s["buckets"]) == buckets
        assert client.get(f"/api/top?range={rng}").status_code == 200


def test_rate_unit_setting(client):
    login(client)
    # existing installations keep the short form
    assert client.get("/api/meta").json()["rate_unit"] == "b/s"
    assert client.get("/api/settings").json()["rate_unit"] == "b/s"
    assert client.put("/api/settings", json={"rate_unit": "Mbps"}, headers=H).status_code == 422
    assert client.put("/api/settings", json={"rate_unit": "bit/s"}, headers=H).json()["rate_unit"] == "bit/s"
    assert client.get("/api/meta").json()["rate_unit"] == "bit/s"
