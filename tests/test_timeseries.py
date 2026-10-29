import random

from sqlalchemy import insert

from asstats.models import LINK_TRAFFIC, TRAFFIC
from asstats.services import timeseries as ts
from asstats.services.rollup import apply_retention, run_rollups

DAY = 86400
NOW = 1_800_000_000 - (1_800_000_000 % DAY) + 13 * 3600 + 17 * 60  # 13:17 UTC


def test_plan_covers_range_without_gaps():
    wm = {3600: NOW - NOW % 3600 - 3600, 86400: NOW - NOW % DAY}
    for _ in range(200):
        s = random.randrange(NOW - 40 * DAY, NOW) // 300 * 300
        e = random.randrange(s, NOW + 1) // 300 * 300
        segs = ts.plan(s, e, wm)
        pos = s
        for step, a, b in segs:
            assert a == pos and a < b
            assert a % step == 0 and b % step == 0
            if step > 300:
                assert b <= wm[step]
            pos = b
        assert pos == e or s == e


def fill(engine, start, end, links=(1, 2), asns=(15169, 13335, 2906)):
    rng = random.Random(42)
    rows, link_rows, raw = [], [], []
    for t in range(start, end, 300):
        for link in links:
            tot = [0, 0, 0, 0]
            for asn in asns:
                vals = [rng.randrange(0, 10_000) for _ in range(4)]
                rows.append(dict(ts=t, link_id=link, asn=asn, in_v4=vals[0], out_v4=vals[1], in_v6=vals[2], out_v6=vals[3]))
                raw.append((t, link, asn, vals))
                tot = [a + b for a, b in zip(tot, vals)]
            link_rows.append(dict(ts=t, link_id=link, in_v4=tot[0], out_v4=tot[1], in_v6=tot[2], out_v6=tot[3]))
    with engine.begin() as conn:
        conn.execute(insert(TRAFFIC[300]), rows)
        conn.execute(insert(LINK_TRAFFIC[300]), link_rows)
    return raw


def raw_sum(raw, s, e, link=None, asn=None):
    tot = [0, 0, 0, 0]
    for t, l, a, vals in raw:
        if s <= t < e and (link is None or l == link) and (asn is None or a == asn):
            tot = [x + y for x, y in zip(tot, vals)]
    return tot


def test_rollup_then_queries_match_raw_data(engine):
    start = NOW - NOW % 300 - 3 * DAY - 7 * 300
    end = NOW - NOW % 300
    raw = fill(engine, start, end)
    assert run_rollups(engine, now=NOW) > 0
    with engine.connect() as conn:
        wm = ts.watermarks(conn)
        assert wm[3600] == ts.floor_to(NOW - 420, 3600)
        assert wm[86400] == ts.floor_to(wm[3600], DAY)

        for s, e in [(start, end), (end - DAY, end), (start + 123 * 300, end - 7 * 300), (end - 900, end)]:
            rows = {(r["asn"], r["link_id"]): r for r in ts.aggregate(conn, TRAFFIC, s, e, ["asn", "link_id"])}
            r = rows[(15169, 2)]
            assert [r["in_v4"], r["out_v4"], r["in_v6"], r["out_v6"]] == raw_sum(raw, s, e, link=2, asn=15169)
            links = {r["link_id"]: r for r in ts.aggregate(conn, LINK_TRAFFIC, s, e, ["link_id"])}
            assert links[1]["in_v4"] == raw_sum(raw, s, e, link=1)[0]

        top = ts.rank_asns(conn, TRAFFIC, start, end, ip="6", sort="in", limit=2)
        expected = sorted(((a, raw_sum(raw, start, end, asn=a)) for a in (15169, 13335, 2906)), key=lambda x: -x[1][2])
        assert [t[0] for t in top] == [a for a, _ in expected[:2]]
        assert top[0][1] == expected[0][1][2]

        for step in (300, 3600, DAY):
            s = ts.floor_to(start, step) + step
            res = ts.series(conn, TRAFFIC, s, end, step, ["link_id"], asns=[13335])
            assert res["buckets"][0] == s
            series = {e["link_id"]: e for e in res["series"]}
            for i, b in enumerate(res["buckets"]):
                assert series[1]["out_v6"][i] == raw_sum(raw, b, min(b + step, end), link=1, asn=13335)[3]


def test_retention_keeps_unrolled_data(engine):
    start = NOW - NOW % 300 - 10 * DAY
    fill(engine, start, start + 3600)
    with engine.begin() as conn:
        from asstats import appsettings

        appsettings.set_value(conn, "retention_5m_days", 1)
    apply_retention(engine, now=NOW)  # nothing rolled up yet -> nothing deleted
    with engine.connect() as conn:
        assert ts.aggregate(conn, TRAFFIC, start, NOW, ["asn"])
    run_rollups(engine, now=NOW)
    apply_retention(engine, now=NOW)
    with engine.connect() as conn:
        assert conn.execute(TRAFFIC[300].select()).first() is None
        # still answerable from the hourly table
        assert ts.aggregate(conn, TRAFFIC, start, NOW, ["asn"])


def test_choose_step():
    ret = {300: 3, 3600: 90, 86400: 1825}
    assert ts.choose_step(NOW - DAY, NOW, NOW, ret) == 300
    assert ts.choose_step(NOW - 7 * DAY, NOW, NOW, ret) == 3600
    assert ts.choose_step(NOW - 90 * DAY, NOW, NOW, ret) == 86400
    assert ts.choose_step(NOW - 6 * DAY, NOW - 5 * DAY, NOW, ret) == 3600
