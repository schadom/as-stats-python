"""Optional prefix statistics: off by default, collection, compaction, API, upgrade."""

import time

from sqlalchemy import create_engine, inspect, insert, select, text
from fastapi.testclient import TestClient

from asstats import appsettings
from asstats.collector.core import Collector
from asstats.db import session_scope
from asstats.flowgen import Flow, build_v9
from asstats.models import PREFIX_TRAFFIC
from asstats.services import prefixes
from asstats.web.app import create_app

from test_collector import IP, ipasn_table, setup_links

H = {"X-Requested-With": "XMLHttpRequest"}

FLOWS = [
    Flow(10, 99, 0, 0, 1000, src_ip="8.8.8.8", dst_ip="192.0.2.7"),   # in from Google, /24 from the table
    Flow(10, 99, 0, 0, 500, src_ip="8.8.8.9", dst_ip="192.0.2.8"),    # same prefix
    Flow(99, 10, 0, 0, 300, src_ip="192.0.2.7", dst_ip="1.1.1.1"),    # out to Cloudflare
    Flow(10, 99, 0, 0, 40, src_ip="193.203.0.9", dst_ip="192.0.2.7"),  # IX LAN, not in the table: /24 fallback, AS 0
]


def prefix_rows(engine):
    with engine.connect() as conn:
        rows = conn.execute(select(PREFIX_TRAFFIC)).mappings().all()
    return {(r["link_id"], r["asn"], r["prefix"]): (r["bytes_in"], r["bytes_out"]) for r in rows}


def run_collector(engine, tmp_path, flows=FLOWS):
    col = Collector(engine, tmp_path)
    col.maybe_reload(force=True)
    col.maybe_load_ipasn()
    with session_scope() as s:
        from asstats.models import Exporter
        from sqlalchemy import update

        s.execute(update(Exporter).values(as_source="lookup"))
        appsettings.bump_config_version(s)
    col.maybe_reload()
    col.handle_netflow(build_v9(flows), IP)
    col.flush(*col.take_snapshot())
    return col


def test_off_by_default_stores_nothing(engine, tmp_path):
    setup_links()
    ipasn_table(tmp_path)
    col = run_collector(engine, tmp_path)
    assert col.prefix_enabled is False
    assert prefix_rows(engine) == {}


def test_collects_prefixes_when_enabled(engine, tmp_path):
    transit, _ = setup_links()
    ipasn_table(tmp_path)
    with engine.begin() as conn:
        appsettings.set_value(conn, "prefix_enabled", True)
    run_collector(engine, tmp_path)
    assert prefix_rows(engine) == {
        (transit, 15169, "8.8.8.0/24"): (1500, 0),
        (transit, 13335, "1.1.1.0/24"): (0, 300),
        (transit, 0, "193.203.0.0/24"): (40, 0),
    }
    hours = {r[0] for r in engine.connect().execute(select(PREFIX_TRAFFIC.c.ts))}
    assert all(h % 3600 == 0 for h in hours)

    # switching it off stops collection without a restart
    with engine.begin() as conn:
        appsettings.set_value(conn, "prefix_enabled", False)
        conn.execute(PREFIX_TRAFFIC.delete())
    run_collector(engine, tmp_path)
    assert prefix_rows(engine) == {}


def test_prefix_boundaries_follow_ranges():
    import ipaddress

    from asstats.ipasn import parse_lines, prefix_for

    t = parse_lines(["142.250.0.0\t142.251.255.255\t15169\tUS\tGOOGLE", "10.0.0.0\t10.0.20.255\t64500\tAT\tX"])
    a = int(ipaddress.ip_address("142.250.12.7"))
    assert prefix_for(a, False, t.range_v4(a)) == "142.250.0.0/15"
    b = int(ipaddress.ip_address("10.0.12.7"))
    assert prefix_for(b, False, t.range_v4(b)) == "10.0.0.0/20"  # range is not one CIDR block
    c = int(ipaddress.ip_address("10.0.20.1"))
    assert prefix_for(c, False, t.range_v4(c)) == "10.0.20.0/24"
    v6 = int(ipaddress.ip_address("2a00:1450:4001::1"))
    assert prefix_for(v6, True, None) == "2a00:1450:4001::/48"


def _insert(engine, hour, rows):
    with engine.begin() as conn:
        conn.execute(
            insert(PREFIX_TRAFFIC),
            [{"asn": a, "ts": hour, "link_id": l, "prefix": p, "bytes_in": i, "bytes_out": o} for a, l, p, i, o in rows],
        )


def test_compaction_keeps_biggest_and_folds_the_rest(engine):
    hour = (int(time.time()) // 3600 - 3) * 3600
    mb = 1_000_000
    _insert(engine, hour, [
        (15169, 1, "142.250.0.0/15", 50 * mb, 1 * mb),
        (15169, 2, "142.250.0.0/15", 10 * mb, 0),
        (15169, 1, "172.217.0.0/16", 20 * mb, 0),
        (15169, 1, "74.125.0.0/16", 5 * mb, 0),          # beyond max_per_as=2
        (15169, 1, "2a00:1450::/32", 3 * mb, 0),         # beyond max_per_as=2, v6
        (13335, 1, "1.1.1.0/24", 200_000, 0),            # below 1 MB
    ])
    with engine.begin() as conn:
        appsettings.set_value(conn, "prefix_max_per_as", 2)
    assert prefixes.run_compaction(engine) >= 1
    assert prefix_rows(engine) == {
        (1, 15169, "142.250.0.0/15"): (50 * mb, 1 * mb),
        (2, 15169, "142.250.0.0/15"): (10 * mb, 0),
        (1, 15169, "172.217.0.0/16"): (20 * mb, 0),
        (1, 15169, prefixes.OTHER_V4): (5 * mb, 0),
        (1, 15169, prefixes.OTHER_V6): (3 * mb, 0),
        (1, 13335, prefixes.OTHER_V4): (200_000, 0),
    }
    # the running hour is left alone, and hours are only compacted once
    now_hour = int(time.time()) // 3600 * 3600
    _insert(engine, now_hour, [(13335, 1, "1.1.1.0/24", 10, 0)])
    prefixes.run_compaction(engine)
    assert prefix_rows(engine)[(1, 13335, "1.1.1.0/24")] == (10, 0)


def test_retention_and_purge(engine):
    now = int(time.time())
    old = (now // 3600 - 24 * 40) * 3600
    recent = (now // 3600 - 2) * 3600
    _insert(engine, old, [(1, 1, "192.0.2.0/24", 1, 1)])
    _insert(engine, recent, [(1, 1, "192.0.2.0/24", 1, 1)])
    prefixes.apply_retention(engine)
    assert len(prefix_rows(engine)) == 1 and prefixes.first_hour(engine.connect()) == recent
    assert prefixes.purge(engine) == 1
    assert prefix_rows(engine) == {}


def test_api(engine):
    hour = (int(time.time()) // 3600 - 2) * 3600
    _insert(engine, hour, [
        (15169, 1, "142.250.0.0/15", 500, 50),
        (15169, 2, "142.250.0.0/15", 100, 0),
        (15169, 1, "2a00:1450::/32", 300, 400),
        (15169, 1, prefixes.OTHER_V4, 20, 0),
        (13335, 1, "1.1.1.0/24", 999, 0),
    ])
    with TestClient(create_app()) as c:
        assert c.post("/api/auth/login", json={"username": "admin", "password": "admin-password"}, headers=H).status_code == 200
        assert c.get("/api/meta").json()["prefix_enabled"] is False
        r = c.get("/api/as/15169/prefixes?range=24h").json()
        assert r["enabled"] is False
        assert [p["prefix"] for p in r["prefixes"]] == ["2a00:1450::/32", "142.250.0.0/15"]
        assert r["prefixes"][1]["links"] == {"1": {"in": 500, "out": 50}, "2": {"in": 100, "out": 0}}
        assert r["other"]["in"] == 20 and r["total"] == {"in": 920, "out": 450}
        assert [p["prefix"] for p in c.get("/api/as/15169/prefixes?range=24h&sort=in").json()["prefixes"]] == [
            "142.250.0.0/15",
            "2a00:1450::/32",
        ]
        r4 = c.get("/api/as/15169/prefixes?range=24h&ip=4").json()
        assert [p["prefix"] for p in r4["prefixes"]] == ["142.250.0.0/15"] and r4["other"]["in"] == 20
        assert c.get("/api/as/15169/prefixes?range=24h&links=2").json()["total"] == {"in": 100, "out": 0}

        st = c.put("/api/settings", json={"prefix_enabled": True, "prefix_min_mb_hour": 0.5}, headers=H).json()
        assert st["prefix_enabled"] is True and st["prefix_retention_days"] == 30
        assert c.put("/api/settings", json={"prefix_max_per_as": 0}, headers=H).status_code == 422
        assert c.get("/api/meta").json()["prefix_enabled"] is True
        assert c.get("/api/prefixes/info").json()["rows"] == 5
        assert c.delete("/api/prefixes", headers=H).json() == {"deleted": 5}


def test_upgrade_creates_the_table(tmp_path):
    """An installation from before this feature gets the table on its next start; its data stays."""
    from asstats.db import migrate
    from asstats.models import TRAFFIC, Base

    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    old = [t for t in Base.metadata.sorted_tables if t.name != "prefix_traffic_1h"]
    Base.metadata.create_all(eng, tables=old)
    with eng.begin() as conn:
        conn.execute(insert(TRAFFIC[300]), [{"ts": 300, "link_id": 1, "asn": 15169, "in_v4": 5, "out_v4": 0, "in_v6": 0, "out_v6": 0}])
    assert "prefix_traffic_1h" not in inspect(eng).get_table_names()
    # what init_db() does on start
    Base.metadata.create_all(eng)
    migrate(eng)
    assert "prefix_traffic_1h" in inspect(eng).get_table_names()
    with eng.connect() as conn:
        assert conn.execute(text("SELECT in_v4 FROM traffic_5m")).scalar_one() == 5
        assert appsettings.get_value(conn, "prefix_enabled") is False
