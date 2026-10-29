from asstats.collector.netflow import NetflowDecoder, Template
from asstats.collector.sflow import SflowDecoder
from asstats.flowgen import Flow, build_ipfix, build_sflow, build_v5, build_v9

IP = "192.0.2.10"


def test_v5_decode_with_sampling():
    dec = NetflowDecoder()
    version, flows = dec.decode(build_v5([Flow(1, 2, 15169, 64500, 1500), Flow(2, 1, 64500, 13335, 400)], sampling=100), IP)
    assert version == 5
    assert flows == [
        (1, 2, 15169, 64500, None, None, 1500, False, 100),
        (2, 1, 64500, 13335, None, None, 400, False, 100),
    ]


def test_v9_templates_options_and_data():
    dec = NetflowDecoder()
    pkt = build_v9(
        [Flow(10, 20, 15169, 0, 1000), Flow(10, 20, 13335, 0, 2000, v6=True)],
        sampling=512,
        if_names={10: "xe-0/0/0", 20: "ae1"},
    )
    version, flows = dec.decode(pkt, IP)
    assert version == 9
    assert (10, 20, 15169, 0, None, None, 1000, False, 512) in flows
    assert (10, 20, 13335, 0, None, None, 2000, True, 512) in flows
    assert dec.iface_names == {(IP, 10): "xe-0/0/0", (IP, 20): "ae1"}
    assert dec.reported_rate(IP) == 512


def test_v9_data_without_template_is_dropped_until_template_arrives():
    dec = NetflowDecoder()
    _, flows = dec.decode(build_v9([Flow(1, 2, 3, 4, 5)], with_templates=False), IP)
    assert flows == [] and dec.missing_template == 1
    dec.decode(build_v9([], with_templates=True), IP)
    _, flows = dec.decode(build_v9([Flow(1, 2, 3, 4, 5)], with_templates=False), IP)
    assert flows == [(1, 2, 3, 4, None, None, 5, False, 0)]


def test_templates_are_per_exporter_and_domain():
    dec = NetflowDecoder()
    dec.decode(build_v9([], source_id=1), IP)
    _, flows = dec.decode(build_v9([Flow(1, 2, 3, 4, 5)], source_id=2, with_templates=False), IP)
    assert flows == []
    _, flows = dec.decode(build_v9([Flow(1, 2, 3, 4, 5)], source_id=1, with_templates=False), "198.51.100.1")
    assert flows == []


def test_ipfix_with_varlen_options_and_peer_as():
    dec = NetflowDecoder()
    pkt = build_ipfix(
        [Flow(5, 6, 15169, 0, 900, src_peer=1299), Flow(6, 5, 0, 2906, 300, v6=True, dst_peer=174)],
        sampling=1000,
        peer_fields=True,
        if_names={5: "et-1/0/0"},
    )
    version, flows = dec.decode(pkt, IP)
    assert version == 10
    assert (5, 6, 15169, 0, 1299, 0, 900, False, 1000) in flows
    assert (6, 5, 0, 2906, 0, 174, 300, True, 1000) in flows
    assert dec.iface_names == {(IP, 5): "et-1/0/0"}


def test_template_cache_roundtrip(tmp_path):
    path = tmp_path / "templates.json"
    dec = NetflowDecoder(path)
    dec.decode(build_ipfix([], sampling=64), IP)
    dec.save_templates()
    dec2 = NetflowDecoder(path)
    _, flows = dec2.decode(build_ipfix([Flow(1, 2, 3, 4, 5)], with_templates=False), IP)
    assert flows == [(1, 2, 3, 4, None, None, 5, False, 64)]
    assert all(isinstance(t, Template) for t in dec2.templates.values())


def test_sflow_gateway_origin_and_peer():
    dec = SflowDecoder()
    pkt = build_sflow(
        [
            Flow(3, 7, 15169, 0, 1514, src_peer=1299),  # inbound, destination is local
            Flow(7, 3, 0, 13335, 800, v6=True, dst_peer=174),  # outbound, source is local
        ],
        rate=2048,
        router_as=64500,
    )
    version, flows = dec.decode(pkt, IP)
    assert version == 5
    assert flows[0] == (3, 7, 15169, 64500, 1299, 64500, 1514, False, 2048)
    assert flows[1] == (7, 3, 64500, 13335, 64500, 174, 800, True, 2048)
