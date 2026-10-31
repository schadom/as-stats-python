"""Routing symmetry: classification, API, "since", link type upgrade."""

import time

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert, inspect, text

from asstats.models import TRAFFIC
from asstats.services.symmetry import analyze, classify
from asstats.web.app import create_app

H = {"X-Requested-With": "XMLHttpRequest"}
MB = 1_000_000
TYPES = {1: "ix", 2: "transit", 3: "transit", 4: "other"}


def test_classify():
    # arrives via the IX, answered via transit
    assert classify({1: 100}, {2: 100}, TYPES)[0] == "in_peer_out_transit"
    # the other way round
    assert classify({2: 95, 1: 5}, {1: 100}, TYPES)[0] == "in_transit_out_peer"
    # two transits, one per direction: asymmetric but not a peering problem
    kind, score, *_ = classify({2: 100}, {3: 100}, TYPES)
    assert kind == "asymmetric" and score == 0
    # same link mix both ways
    kind, score, p_in, p_out = classify({1: 80, 2: 20}, {1: 40, 2: 10}, TYPES)
    assert kind == "ok" and score == 1 and p_in == p_out == 0.8
    # links without a type never make an IX/transit mismatch
    assert classify({4: 100}, {2: 100}, TYPES)[0] == "asymmetric"


def test_analyze_skips_small_and_one_directional():
    rows = [
        (64496, 1, 500 * MB, 0), (64496, 2, 0, 400 * MB),   # mismatch
        (64500, 2, 50 * MB, 0), (64500, 3, 0, 40 * MB),     # below 100 MB
        (64498, 3, 900 * MB, 0), (64498, 3, 0, 1 * MB),     # outbound under 5 % of the minimum
    ]
    res = analyze(rows, TYPES, 100 * MB)
    assert [(r.asn, r.kind) for r in res] == [(64496, "in_peer_out_transit")]
    assert len(analyze(rows, TYPES, 0)) == 3


def _login(c):
    assert c.post("/api/auth/login", json={"username": "admin", "password": "admin-password"}, headers=H).status_code == 200


def _setup_links(c):
    exp = c.post("/api/exporters", json={"ip": "192.0.2.1", "name": "edge1"}, headers=H).json()
    ids = {}
    for tag, ifindex, ltype in (("ix-a", 1, "ix"), ("transit-a", 2, "transit"), ("transit-b", 3, "transit")):
        body = {"tag": tag, "name": tag.upper(), "interfaces": [{"exporter_id": exp["id"], "ifindex": ifindex}]}
        r = c.post("/api/links", json={**body, "link_type": ltype}, headers=H)
        assert r.status_code == 201, r.text
        ids[tag] = r.json()["id"]
    return ids


def _traffic(engine, rows, step=300, ts=None):
    ts = ts or (int(time.time()) // 300 * 300 - 600)
    with engine.begin() as conn:
        conn.execute(
            insert(TRAFFIC[step]),
            [{"ts": ts, "link_id": l, "asn": a, "in_v4": i, "out_v4": o, "in_v6": 0, "out_v6": 0} for a, l, i, o in rows],
        )


def test_api(engine):
    with TestClient(create_app()) as c:
        _login(c)
        assert c.get("/api/symmetry?range=1h").json()["types_set"] is False
        ids = _setup_links(c)
        ix_a, transit_a, transit_b = ids["ix-a"], ids["transit-a"], ids["transit-b"]
        _traffic(engine, [
            (64496, ix_a, 3000 * MB, 0), (64496, transit_a, 0, 2000 * MB),          # in via IX, out via transit
            (64497, transit_a, 500 * MB, 0), (64497, ix_a, 0, 20000 * MB),          # in via transit, out via IX
            (64498, transit_b, 20000 * MB, 0), (64498, transit_a, 0, 300 * MB),     # transit vs transit
            (64499, ix_a, 200 * MB, 50 * MB),                                       # symmetric via IX
            (64501, transit_a, 10 * MB, 10 * MB),                                   # too small
        ])
        r = c.get("/api/symmetry?range=1h").json()
        assert r["types_set"] is True
        assert [(i["asn"], i["kind"]) for i in r["items"]] == [
            (64497, "in_transit_out_peer"),
            (64496, "in_peer_out_transit"),
            (64498, "asymmetric"),
            (64499, "ok"),
        ]
        assert r["summary"] == {
            "mismatches": 2,
            "affected": 25500 * MB,
            "asymmetric": 1,
            "symmetric_peering": 1,
            "compared": 4,
        }
        item1 = r["items"][1]
        assert item1["in_links"] == {str(ix_a): 3000 * MB} and item1["out_links"] == {str(transit_a): 2000 * MB}
        assert "name" in item1
        # one AS only (used by the AS page), and a lower minimum
        assert [i["asn"] for i in c.get("/api/symmetry?range=1h&asn=64496").json()["items"]] == [64496]
        assert len(c.get("/api/symmetry?range=1h&min_mb=0").json()["items"]) == 5

        # link type: kept when omitted, validated
        link = c.get("/api/links").json()[0]
        body = {k: link[k] for k in ("tag", "name", "color", "sort_order", "enabled")}
        body["interfaces"] = [{"exporter_id": i["exporter_id"], "ifindex": i["ifindex"]} for i in link["interfaces"]]
        assert c.put(f"/api/links/{link['id']}", json=body, headers=H).json()["link_type"] == "ix"
        assert c.put(f"/api/links/{link['id']}", json={**body, "link_type": "peering"}, headers=H).status_code == 422
        assert c.get("/api/meta").json()["links"][0]["link_type"] == "ix"


def test_since(engine):
    now_hour = int(time.time()) // 3600 * 3600
    with TestClient(create_app()) as c:
        _login(c)
        ids = _setup_links(c)
        ix_a, transit_a = ids["ix-a"], ids["transit-a"]
        # symmetric 6 hours ago, a quiet hour, then the mismatch from 4 hours ago (no data 2 hours ago)
        _traffic(engine, [(64496, ix_a, 500 * MB, 400 * MB)], 3600, now_hour - 6 * 3600)
        _traffic(engine, [(64496, ix_a, 1000, 0)], 3600, now_hour - 5 * 3600)
        for h in (4, 3, 1):
            _traffic(engine, [(64496, ix_a, 500 * MB, 0), (64496, transit_a, 0, 400 * MB)], 3600, now_hour - h * 3600)
        _traffic(engine, [(64496, ix_a, 500 * MB, 0), (64496, transit_a, 0, 400 * MB)])  # current, 5 minute data
        item = c.get("/api/symmetry?range=1h").json()["items"][0]
        assert item["kind"] == "in_peer_out_transit"
        assert item["since"] == now_hour - 4 * 3600


def test_upgrade_adds_link_type(tmp_path):
    from asstats.db import migrate

    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE links (id INTEGER PRIMARY KEY, tag VARCHAR(32), name VARCHAR(128))"))
        conn.execute(text("INSERT INTO links (tag, name) VALUES ('t1', 'Transit')"))
    migrate(eng)
    migrate(eng)
    assert "link_type" in {c["name"] for c in inspect(eng).get_columns("links")}
    with eng.connect() as conn:
        assert conn.execute(text("SELECT link_type FROM links")).scalar_one() == "other"
    eng.dispose()
