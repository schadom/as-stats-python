"""Database schema.

All timestamps are integer Unix epoch seconds (UTC). Traffic counters are
bytes, already multiplied by the sampling rate.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------- users

ROLE_ADMIN = "admin"
ROLE_READONLY = "readonly"
ROLES = (ROLE_ADMIN, ROLE_READONLY)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default=ROLE_READONLY)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_login: Mapped[int | None] = mapped_column(Integer, nullable=True)


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    last_seen: Mapped[int] = mapped_column(Integer, nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)


# ---------------------------------------------------------------- settings


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


# ---------------------------------------------------------------- exporters & links


class Exporter(Base):
    __tablename__ = "exporters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ip: Mapped[str] = mapped_column(String(45), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # None = use the rate the exporter reports (or 1 if it reports none)
    sampling_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # "origin" = origin AS (default), "peer" = neighbour AS where the exporter provides it
    as_mode: Mapped[str] = mapped_column(String(8), nullable=False, default="origin")
    # Where AS numbers come from: "auto" = from the flow, looked up by IP when the
    # flow has none (AS 0); "router" = only from the flow; "lookup" = always by IP
    as_source: Mapped[str] = mapped_column(String(8), nullable=False, default="auto", server_default="auto")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    interfaces: Mapped[list["LinkInterface"]] = relationship(
        back_populates="exporter", cascade="all, delete-orphan", passive_deletes=True
    )


class Link(Base):
    """A logical link (transit, peering, IX port...). Made of one or more interfaces."""

    __tablename__ = "links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tag: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    color: Mapped[str] = mapped_column(String(7), nullable=False, default="#4f7cff")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Optional, in Mbit/s per direction. Only used to show how full the link is.
    capacity_mbit: Mapped[int | None] = mapped_column(Integer, nullable=True)

    interfaces: Mapped[list["LinkInterface"]] = relationship(
        back_populates="link",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="LinkInterface.id",
    )


class LinkInterface(Base):
    __tablename__ = "link_interfaces"
    __table_args__ = (UniqueConstraint("exporter_id", "ifindex", name="uq_link_iface"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    link_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("links.id", ondelete="CASCADE"), nullable=False, index=True
    )
    exporter_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("exporters.id", ondelete="CASCADE"), nullable=False
    )
    ifindex: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Overrides the exporter's sampling rate for this interface only
    sampling_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)

    link: Mapped[Link] = relationship(back_populates="interfaces")
    exporter: Mapped[Exporter] = relationship(back_populates="interfaces")


class DiscoveredInterface(Base):
    """Every (exporter, ifIndex) the collector has seen traffic on, configured or not."""

    __tablename__ = "discovered_interfaces"

    exporter_ip: Mapped[str] = mapped_column(String(45), primary_key=True)
    ifindex: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    if_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_seen: Mapped[int] = mapped_column(Integer, nullable=False)
    last_seen: Mapped[int] = mapped_column(Integer, nullable=False)
    bps_in: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    bps_out: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    flows: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class ExporterStatus(Base):
    __tablename__ = "exporter_status"

    ip: Mapped[str] = mapped_column(String(45), primary_key=True)
    protocol: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_seen: Mapped[int] = mapped_column(Integer, nullable=False)
    packets: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    flows: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    errors: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    flows_per_sec: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reported_sampling: Mapped[int | None] = mapped_column(Integer, nullable=True)
    templates: Mapped[int | None] = mapped_column(Integer, nullable=True)


# ---------------------------------------------------------------- AS names & PeeringDB


class AsInfo(Base):
    __tablename__ = "as_info"

    asn: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    handle: Mapped[str | None] = mapped_column(String(128), nullable=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    pdb_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pdb_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    custom_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 0 = never looked up
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)
    failed_at: Mapped[int | None] = mapped_column(Integer, nullable=True)


class PdbIx(Base):
    __tablename__ = "pdb_ix"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    name_long: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class PdbIxMember(Base):
    __tablename__ = "pdb_ix_members"
    __table_args__ = (Index("ix_pdb_ix_members_ix", "ix_id"), Index("ix_pdb_ix_members_asn", "asn"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ix_id: Mapped[int] = mapped_column(Integer, nullable=False)
    asn: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ipaddr4: Mapped[str | None] = mapped_column(String(45), nullable=True)
    ipaddr6: Mapped[str | None] = mapped_column(String(45), nullable=True)
    speed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_rs_peer: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


# ---------------------------------------------------------------- traffic
#
# Two families of tables, each in three resolutions (5 minutes, 1 hour, 1 day):
#   traffic_*      bytes per (bucket, link, AS)
#   link_traffic_* bytes per (bucket, link), i.e. the same data summed over all ASes
#
# The collector writes the 5 minute tables. The scheduler rolls them up into the
# coarser ones and applies retention.

STEPS = (300, 3600, 86400)
STEP_SUFFIX = {300: "5m", 3600: "1h", 86400: "1d"}
COUNTER_COLUMNS = ("in_v4", "out_v4", "in_v6", "out_v6")


def _counter_columns() -> list[Column]:
    return [Column(name, BigInteger, nullable=False, default=0) for name in COUNTER_COLUMNS]


def _traffic_table(step: int) -> Table:
    name = f"traffic_{STEP_SUFFIX[step]}"
    return Table(
        name,
        Base.metadata,
        Column("ts", Integer, primary_key=True, autoincrement=False),
        Column("link_id", Integer, primary_key=True, autoincrement=False),
        Column("asn", BigInteger, primary_key=True, autoincrement=False),
        *_counter_columns(),
        Index(f"ix_{name}_asn_ts", "asn", "ts"),
    )


def _link_traffic_table(step: int) -> Table:
    name = f"link_traffic_{STEP_SUFFIX[step]}"
    return Table(
        name,
        Base.metadata,
        Column("ts", Integer, primary_key=True, autoincrement=False),
        Column("link_id", Integer, primary_key=True, autoincrement=False),
        *_counter_columns(),
    )


TRAFFIC: dict[int, Table] = {step: _traffic_table(step) for step in STEPS}
LINK_TRAFFIC: dict[int, Table] = {step: _link_traffic_table(step) for step in STEPS}

# Optional prefix statistics (setting "prefix_enabled", off by default): bytes per
# (hour, link, AS, remote prefix). The collector writes it directly in hourly
# buckets; after each hour the scheduler folds small prefixes into one "other"
# row per family (see services/prefixes.py). Older versions simply ignore it.
PREFIX_TRAFFIC = Table(
    "prefix_traffic_1h",
    Base.metadata,
    Column("asn", BigInteger, primary_key=True, autoincrement=False),
    Column("ts", Integer, primary_key=True, autoincrement=False),
    Column("link_id", Integer, primary_key=True, autoincrement=False),
    Column("prefix", String(43), primary_key=True),
    Column("bytes_in", BigInteger, nullable=False, default=0),
    Column("bytes_out", BigInteger, nullable=False, default=0),
    Index("ix_prefix_traffic_1h_ts", "ts"),
)
