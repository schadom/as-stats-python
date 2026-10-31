from sqlalchemy import select, update

from asstats.collector.core import Collector
from asstats.db import session_scope
from asstats.flowgen import Flow, build_ipfix, build_sflow, build_v9
from asstats.models import LINK_TRAFFIC, TRAFFIC, AsInfo, DiscoveredInterface, Exporter, ExporterStatus, Link, LinkInterface

IP = "192.0.2.1"


def setup_links(sampling=None, as_mode="origin"):
    with session_scope() as s:
        exp = Exporter(ip=IP, name="edge1", sampling_rate=sampling, as_mode=as_mode)
        transit = Link(tag="transit", name="Transit", color="#ff0000")
        ix = Link(tag="ix", name="IX", color="#00ff00")
        s.add_all([exp, transit, ix])
        s.flush()
        s.add_all(
            [
                LinkInterface(link_id=transit.id, exporter_id=exp.id, ifindex=10),
                LinkInterface(link_id=ix.id, exporter_id=exp.id, ifindex=11, sampling_rate=10),
            ]
        )
        return transit.id, ix.id


def traffic_rows(engine):
    with engine.connect() as conn:
        rows = conn.execute(select(TRAFFIC[300])).mappings().all()
    return {(r["link_id"], r["asn"]): (r["in_v4"], r["out_v4"], r["in_v6"], r["out_v6"]) for r in rows}


def test_classification_sampling_and_flush(engine):
    transit, ix = setup_links()
    col = Collector(engine)
    col.maybe_reload(force=True)
    col.handle_netflow(build_v9([], sampling=100), IP)  # templates + sampling option
    col.handle_netflow(
        build_v9(
            [
                Flow(10, 99, 15169, 0, 1000),  # inbound on transit from Google
                Flow(99, 10, 0, 13335, 500, v6=True),  # outbound on transit to Cloudflare (v6)
                Flow(11, 99, 2906, 0, 7),  # inbound on IX, interface override rate 10
                Flow(98, 99, 3356, 0, 1234),  # not on a known link
                Flow(10, 11, 1299, 6939, 2),  # transit -> IX, counts on both
            ],
            with_templates=False,
        ),
        IP,
    )
    snap, names = col.take_snapshot()
    col.flush(snap, names)

    rows = traffic_rows(engine)
    assert rows[(transit, 15169)] == (100_000, 0, 0, 0)
    assert rows[(transit, 13335)] == (0, 0, 0, 50_000)
    assert rows[(ix, 2906)] == (70, 0, 0, 0)
    assert rows[(transit, 1299)] == (200, 0, 0, 0)
    assert rows[(ix, 6939)] == (0, 20, 0, 0)
    assert all(asn != 3356 for _, asn in rows)

    with engine.connect() as conn:
        links = {r.link_id: r for r in conn.execute(select(LINK_TRAFFIC[300]))}
        assert links[transit].in_v4 == 100_200 and links[transit].out_v6 == 50_000
        disc = {r.ifindex: r for r in conn.execute(select(DiscoveredInterface))}
        assert set(disc) == {10, 11, 98, 99}
        assert conn.execute(select(ExporterStatus.reported_sampling)).scalar_one() == 100
        asns = set(conn.execute(select(AsInfo.asn)).scalars())
        assert asns == {15169, 13335, 2906, 1299, 6939}

    # a second flush into the same bucket adds up
    col.handle_netflow(build_v9([Flow(10, 99, 15169, 0, 1000)], with_templates=False), IP)
    snap, names = col.take_snapshot()
    col.flush(snap, names)
    assert traffic_rows(engine)[(transit, 15169)] == (200_000, 0, 0, 0)


def test_unknown_exporter_only_feeds_discovery(engine):
    setup_links()
    col = Collector(engine)
    col.maybe_reload(force=True)
    col.handle_netflow(build_ipfix([Flow(10, 1, 15169, 0, 100)], if_names={10: "uplink"}), "198.51.100.7")
    snap, names = col.take_snapshot()
    col.flush(snap, names)
    assert traffic_rows(engine) == {}
    with engine.connect() as conn:
        row = conn.execute(select(DiscoveredInterface).where(DiscoveredInterface.ifindex == 10)).one()
        assert row.exporter_ip == "198.51.100.7" and row.if_name == "uplink"


def test_exporter_sampling_override_and_peer_mode(engine):
    transit, _ = setup_links(sampling=4000, as_mode="peer")
    col = Collector(engine)
    col.maybe_reload(force=True)
    col.handle_sflow(build_sflow([Flow(10, 99, 15169, 0, 1500, src_peer=1299)], rate=1000), IP)
    snap, names = col.take_snapshot()
    col.flush(snap, names)
    assert traffic_rows(engine) == {(transit, 1299): (1500 * 4000, 0, 0, 0)}


def test_config_reload_picks_up_changes(engine):
    from asstats import appsettings

    col = Collector(engine)
    assert col.maybe_reload() is True
    assert col.maybe_reload() is False
    transit, _ = setup_links()
    with session_scope() as s:
        appsettings.bump_config_version(s)
    assert col.maybe_reload() is True
    assert col.linkmap.ifaces[(IP, 10)] == (transit, None)


def _v9_custom(fields, records, source_id=1):
    """v9 packet with one template (id 300) and data records given as value lists."""
    import struct

    tpl = struct.pack("!HH", 300, len(fields)) + b"".join(struct.pack("!HH", t, l) for t, l in fields)
    data = b"".join(b"".join(v.to_bytes(l, "big") for v, (_, l) in zip(rec, fields)) for rec in records)
    sets = struct.pack("!HH", 0, 4 + len(tpl)) + tpl + struct.pack("!HH", 300, 4 + len(data)) + data
    return struct.pack("!HHIIII", 9, 1 + len(records), 0, 0, 0, source_id) + sets


def test_implausible_values_do_not_break_the_flush(engine, caplog):
    transit, _ = setup_links()
    col = Collector(engine)
    col.maybe_reload(force=True)
    # in_if, out_if, src_as, dst_as, bytes, sampling interval (all 8 bytes wide on purpose)
    fields = [(10, 8), (14, 8), (16, 4), (17, 4), (1, 8), (34, 4)]
    pkt = _v9_custom(
        fields,
        [
            [2**64 - 1, 99, 15169, 0, 1000, 0],  # garbage ifIndex -> dropped
            [10, 99, 15169, 0, 2**62, 0],  # absurd byte count -> dropped
            [10, 99, 13335, 0, 500, 2**32 - 1],  # absurd sampling rate -> ignored, counted 1:1
            [10, 99, 2906, 0, 100, 0],  # normal
        ],
    )
    with caplog.at_level("INFO"):
        col.handle_netflow(pkt, IP)
        col.flush(*col.take_snapshot())
    rows = traffic_rows(engine)
    assert rows == {(transit, 13335): (500, 0, 0, 0), (transit, 2906): (100, 0, 0, 0)}
    with engine.connect() as conn:
        status = conn.execute(select(ExporterStatus)).one()
        assert status.errors == 2
        assert {r.ifindex for r in conn.execute(select(DiscoveredInterface))} == {10, 99}
    text = caplog.text
    assert "learned template 300 from 192.0.2.1 (NetFlow v9 source id 1): [in_if/8 out_if/8 src_as/4 dst_as/4 bytes/8 sampling_interval/4]" in text
    assert "implausible sampling rate" in text and "dropping implausible flow" in text


# The two data templates a VyOS 2026.03 router sent (from a real log), no AS fields.
VYOS_V4 = [(8, 4), (12, 4), (15, 4), (60, 1), (7, 2), (11, 2), (10, 2), (14, 2), (2, 4), (1, 8),
           (22, 4), (21, 4), (4, 1), (5, 1), (80, 6), (56, 6), (256, 2), (61, 1)]
VYOS_V6 = [(27, 16), (28, 16), (62, 16), (60, 1), (31, 3), (7, 2), (11, 2), (10, 2), (14, 2), (2, 4), (1, 8),
           (22, 4), (21, 4), (4, 1), (5, 1), (80, 6), (56, 6), (256, 2), (61, 1)]


def vyos_record(fields, in_if, out_if, src, dst, nbytes):
    import ipaddress

    vals = {8: src, 12: dst, 27: src, 28: dst, 10: in_if, 14: out_if, 1: nbytes, 2: 1, 60: ipaddress.ip_address(src).version}
    out = []
    for t, _l in fields:
        v = vals.get(t, 0)
        out.append(int(ipaddress.ip_address(v)) if isinstance(v, str) else v)
    return out


def ipasn_table(tmp_path):
    from asstats import ipasn

    lines = [
        "1.1.1.0\t1.1.1.255\t13335\tUS\tCLOUDFLARENET",
        "8.8.8.0\t8.8.8.255\t15169\tUS\tGOOGLE",
        "192.0.2.0\t192.0.2.255\t64500\tAT\tMY-NET",
        "2606:4700::\t2606:4700:ffff:ffff:ffff:ffff:ffff:ffff\t13335\tUS\tCLOUDFLARENET",
        "2001:db8::\t2001:db8:ffff:ffff:ffff:ffff:ffff:ffff\t64500\tAT\tMY-NET",
        "10.0.0.0\t10.255.255.255\t0\tNone\tNot routed",
    ]
    ipasn.save_binary(ipasn.parse_lines(lines), tmp_path / ipasn.FILENAME)


def test_ip_lookup_for_exporters_without_as(engine, tmp_path):
    """VyOS sends no AS numbers: the collector looks them up by address."""
    transit, _ = setup_links()
    ipasn_table(tmp_path)
    col = Collector(engine, tmp_path)
    col.maybe_reload(force=True)
    assert col.maybe_load_ipasn() is True and col.maybe_load_ipasn() is False

    pkt4 = _v9_custom(VYOS_V4, [
        vyos_record(VYOS_V4, 10, 99, "8.8.8.8", "192.0.2.7", 1000),   # inbound from Google
        vyos_record(VYOS_V4, 99, 10, "192.0.2.7", "1.1.1.1", 300),    # outbound to Cloudflare
        vyos_record(VYOS_V4, 10, 99, "193.203.0.9", "192.0.2.7", 50), # IX LAN, not announced -> AS 0
    ])
    col.handle_netflow(pkt4, IP)
    import struct
    # IPv6 template 301 with the 3-byte flow label field
    tpl = struct.pack("!HH", 301, len(VYOS_V6)) + b"".join(struct.pack("!HH", t, l) for t, l in VYOS_V6)
    rec = vyos_record(VYOS_V6, 10, 99, "2606:4700::1111", "2001:db8::7", 700)
    data = b"".join(v.to_bytes(l, "big") for v, (_, l) in zip(rec, VYOS_V6))
    pkt6 = struct.pack("!HHIIII", 9, 2, 0, 0, 0, 1) + struct.pack("!HH", 0, 4 + len(tpl)) + tpl + struct.pack("!HH", 301, 4 + len(data)) + data
    col.handle_netflow(pkt6, IP)
    col.flush(*col.take_snapshot())

    assert traffic_rows(engine) == {
        (transit, 15169): (1000, 0, 0, 0),
        (transit, 13335): (0, 300, 700, 0),
        (transit, 0): (50, 0, 0, 0),
    }
    from asstats import appsettings

    with engine.connect() as conn:
        assert appsettings.get_value(conn, appsettings.COLLECTOR_HEARTBEAT)["ipasn_ranges"] == 5


def test_as_source_modes(engine, tmp_path):
    from asstats import appsettings

    transit, _ = setup_links()
    ipasn_table(tmp_path)

    def run(mode):
        with session_scope() as s:
            s.execute(update(Exporter).values(as_source=mode))
            appsettings.bump_config_version(s)
        with engine.begin() as conn:
            conn.execute(TRAFFIC[300].delete())
        col = Collector(engine, tmp_path)
        col.maybe_reload(force=True)
        col.maybe_load_ipasn()
        # router says AS 2906 for 8.8.8.8 and AS 0 for 1.1.1.1
        col.handle_netflow(build_v9([
            Flow(10, 99, 2906, 0, 100, src_ip="8.8.8.8", dst_ip="192.0.2.1"),
            Flow(10, 99, 0, 0, 100, src_ip="1.1.1.1", dst_ip="192.0.2.1"),
        ]), IP)
        col.flush(*col.take_snapshot())
        return {asn for _link, asn in traffic_rows(engine)}

    assert run("auto") == {2906, 13335}      # router value kept, missing one looked up
    assert run("router") == {2906, 0}        # never looked up
    assert run("lookup") == {15169, 13335}   # always looked up


def test_migration_adds_as_source_column(tmp_path):
    from sqlalchemy import create_engine, inspect, text

    from asstats.db import migrate

    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE exporters (id INTEGER PRIMARY KEY, ip VARCHAR(45), name VARCHAR(128))"))
        conn.execute(text("INSERT INTO exporters (ip, name) VALUES ('192.0.2.1', 'r1')"))
    migrate(eng)
    migrate(eng)  # idempotent
    assert "as_source" in {c["name"] for c in inspect(eng).get_columns("exporters")}
    with eng.connect() as conn:
        assert conn.execute(text("SELECT as_source FROM exporters")).scalar_one() == "auto"


def test_migration_adds_link_capacity_column(tmp_path):
    from sqlalchemy import create_engine, inspect, text

    from asstats.db import migrate

    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE links (id INTEGER PRIMARY KEY, tag VARCHAR(32), name VARCHAR(128))"))
        conn.execute(text("INSERT INTO links (tag, name) VALUES ('t1', 'Transit')"))
    migrate(eng)
    migrate(eng)  # idempotent
    with eng.connect() as conn:
        assert conn.execute(text("SELECT capacity_mbit FROM links")).scalar_one() is None


def test_non_internet_addresses_are_ignored(engine, tmp_path):
    """Link-local, multicast and private remote addresses are router housekeeping, not AS traffic."""
    from asstats import appsettings

    transit, _ = setup_links()
    ipasn_table(tmp_path)

    def run():
        with engine.begin() as conn:
            conn.execute(TRAFFIC[300].delete())
        col = Collector(engine, tmp_path)
        col.maybe_reload(force=True)
        col.maybe_load_ipasn()
        col.handle_netflow(build_v9([
            Flow(10, 99, 0, 0, 1000, src_ip="8.8.8.8", dst_ip="10.0.0.5"),        # private LOCAL side: counted
            Flow(10, 99, 0, 0, 100, src_ip="10.9.9.9", dst_ip="192.0.2.7"),       # RFC 1918 remote
            Flow(10, 99, 0, 0, 100, src_ip="169.254.0.1", dst_ip="192.0.2.7"),    # link-local
            Flow(99, 10, 0, 0, 100, src_ip="192.0.2.7", dst_ip="224.0.0.5"),      # multicast (OSPF)
            Flow(99, 10, 0, 0, 100, src_ip="192.0.2.7", dst_ip="100.64.1.1"),     # CGNAT space
            Flow(10, 99, 0, 0, 7, src_ip="193.203.0.9", dst_ip="192.0.2.7"),      # IX LAN: public, still counted
            Flow(10, 99, 2906, 0, 5),                                              # no addresses at all: counted
            Flow(10, 99, 0, 0, 100, v6=True, src_ip="fe80::1", dst_ip="2001:db8::7"),   # link-local v6
            Flow(99, 10, 0, 0, 100, v6=True, src_ip="2001:db8::7", dst_ip="ff02::1"),   # multicast v6
            Flow(10, 99, 0, 0, 100, v6=True, src_ip="fd00::1", dst_ip="2001:db8::7"),   # ULA
            Flow(10, 99, 0, 0, 300, v6=True, src_ip="2606:4700::1111", dst_ip="fe80::2"),  # link-local LOCAL side: counted
        ]), IP)
        col.flush(*col.take_snapshot())
        with engine.connect() as conn:
            beat = appsettings.get_value(conn, appsettings.COLLECTOR_HEARTBEAT)
        return traffic_rows(engine), beat

    rows, beat = run()
    assert rows == {
        (transit, 15169): (1000, 0, 0, 0),
        (transit, 0): (7, 0, 0, 0),
        (transit, 2906): (5, 0, 0, 0),
        (transit, 13335): (0, 0, 300, 0),
    }
    assert beat["ignored_per_sec"] > 0

    with engine.begin() as conn:
        appsettings.set_value(conn, "ignore_non_internet", False)
    rows, _ = run()
    assert rows[(transit, 0)][0] == 7 + 200 and rows[(transit, 0)][1] == 200  # counted again as unknown
