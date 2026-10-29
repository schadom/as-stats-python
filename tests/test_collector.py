from sqlalchemy import select

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
