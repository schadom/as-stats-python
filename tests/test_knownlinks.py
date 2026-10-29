from sqlalchemy import select

from asstats import knownlinks
from asstats.db import session_scope
from asstats.models import Exporter, Link, LinkInterface

SAMPLE = """# AS-Stats knownlinks
192.0.2.1\t1\ttransit1\tTransit Cogent\tA6D000
192.0.2.1\t5\tix\tVIX peering\t#00ff00\t512
198.51.100.2 7 transit1 Transit Cogent backup FF8800
"""


def test_parse_and_apply(engine):
    entries = knownlinks.parse(SAMPLE)
    assert [(e.ip, e.ifindex, e.tag) for e in entries] == [
        ("192.0.2.1", 1, "transit1"),
        ("192.0.2.1", 5, "ix"),
        ("198.51.100.2", 7, "transit1"),
    ]
    assert entries[1].sampling == 512 and entries[1].color == "#00ff00"
    assert entries[2].description == "Transit Cogent backup"
    with session_scope() as db:
        stats = knownlinks.apply(db, entries)
    assert stats == {"exporters": 2, "links": 2, "interfaces": 3}
    with session_scope() as db:
        transit = db.execute(select(Link).where(Link.tag == "transit1")).scalar_one()
        assert len(transit.interfaces) == 2
        assert db.execute(select(Exporter)).scalars().all()[0].name == "192.0.2.1"
    # importing again is idempotent
    with session_scope() as db:
        assert knownlinks.apply(db, entries) == {"exporters": 0, "links": 0, "interfaces": 0}
    with session_scope() as db:
        assert len(db.execute(select(LinkInterface)).scalars().all()) == 3
