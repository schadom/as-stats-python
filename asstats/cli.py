"""Command line entry point: `asstats <command>`."""

from __future__ import annotations

import argparse
import getpass
import logging
import sys
import time


def _users(args) -> int:
    from sqlalchemy import select

    from .db import init_db, session_scope
    from .models import ROLES, User
    from .security import hash_password

    init_db()
    with session_scope() as db:
        if args.action == "list":
            for u in db.execute(select(User).order_by(User.username)).scalars():
                print(f"{u.username:24} {u.role:9} {'active' if u.is_active else 'disabled'}")
            return 0
        password = args.password or getpass.getpass("Password: ")
        if len(password) < 8:
            print("password must have at least 8 characters", file=sys.stderr)
            return 1
        user = db.execute(select(User).where(User.username == args.username)).scalar_one_or_none()
        if args.action == "create":
            if user is not None:
                print("user exists, use reset-password", file=sys.stderr)
                return 1
            if args.role not in ROLES:
                print(f"role must be one of {ROLES}", file=sys.stderr)
                return 1
            db.add(User(username=args.username, password_hash=hash_password(password), role=args.role, created_at=int(time.time())))
        else:
            if user is None:
                print("no such user", file=sys.stderr)
                return 1
            user.password_hash = hash_password(password)
            user.is_active = True
            user.must_change_password = False
    print("ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="asstats", description="Per-AS traffic statistics")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="run web UI and collector together (container default)")
    sub.add_parser("web", help="run the web UI/API and background jobs")
    sub.add_parser("collector", help="run the NetFlow/IPFIX/sFlow collector")
    sub.add_parser("rollup", help="run rollups and retention once")
    sub.add_parser("init", help="create the database schema and the first admin user")
    k = sub.add_parser("import-knownlinks", help="import links from an AS-Stats knownlinks file")
    k.add_argument("file")

    u = sub.add_parser("user", help="manage users")
    u.add_argument("action", choices=["create", "reset-password", "list"])
    u.add_argument("username", nargs="?")
    u.add_argument("--role", default="readonly")
    u.add_argument("--password", help="omit to be prompted")

    g = sub.add_parser("flowgen", help="send synthetic flows to a collector for testing")
    g.add_argument("target", nargs="?", default="127.0.0.1", help="collector host[:port]")
    g.add_argument("--protocol", choices=["v5", "v9", "ipfix", "sflow"], default="ipfix")
    g.add_argument("--port", type=int, default=None, help="default 9000, or 6343 for sflow")
    g.add_argument("--links", default="1,2,3", help="ifIndexes that act as external links")
    g.add_argument("--local", default="100,101", help="ifIndexes facing your network")
    g.add_argument("--flows", type=int, default=20, help="flows per packet")
    g.add_argument("--pps", type=float, default=10, help="packets per second")
    g.add_argument("--sampling", type=int, default=1000)

    args = parser.parse_args(argv)
    if args.command == "run":
        from .launcher import run

        return run()
    if args.command == "web":
        from .web.app import main as web_main

        web_main()
    elif args.command == "collector":
        from .collector.server import main as collector_main

        collector_main()
    elif args.command == "rollup":
        from .config import get_config
        from .db import get_engine, init_db
        from .services.rollup import apply_retention, run_rollups

        logging.basicConfig(level=get_config().log_level)
        init_db()
        print(f"rolled up {run_rollups(get_engine())} period(s)")
        apply_retention(get_engine())
    elif args.command == "init":
        from .config import get_config
        from .db import init_db
        from .web.app import bootstrap_admin

        logging.basicConfig(level=get_config().log_level, format="%(asctime)s %(levelname)s [init] %(message)s")
        init_db()
        bootstrap_admin()
    elif args.command == "import-knownlinks":
        from . import knownlinks
        from .db import init_db, session_scope

        init_db()
        with open(args.file, encoding="utf-8") as fh:
            entries = knownlinks.parse(fh.read())
        with session_scope() as db:
            stats = knownlinks.apply(db, entries)
        print(
            f"imported {len(entries)} line(s): {stats['exporters']} new exporter(s), "
            f"{stats['links']} new link(s), {stats['interfaces']} new interface(s)"
        )
    elif args.command == "user":
        if args.action != "list" and not args.username:
            parser.error("username required")
        return _users(args)
    elif args.command == "flowgen":
        from .flowgen import run

        port = args.port or (6343 if args.protocol == "sflow" else 9000)
        links = [int(x) for x in args.links.split(",") if x]
        local = [int(x) for x in args.local.split(",") if x]
        run(args.target, args.protocol, port, links, local, args.flows, args.pps, args.sampling)
    return 0


if __name__ == "__main__":
    sys.exit(main())
