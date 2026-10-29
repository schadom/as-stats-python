"""asyncio UDP listeners plus the periodic flush and config reload loops."""

from __future__ import annotations

import asyncio
import logging
import signal
import socket
from typing import Callable

from ..config import get_config
from ..db import get_engine, init_db
from .core import Collector, normalize_ip

log = logging.getLogger(__name__)

RELOAD_INTERVAL = 10


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, handler: Callable[[bytes, str], None], name: str):
        self.handler = handler
        self.name = name

    def datagram_received(self, data: bytes, addr) -> None:  # type: ignore[override]
        try:
            self.handler(data, normalize_ip(addr[0]))
        except Exception:  # pragma: no cover - never let one packet kill the listener
            log.exception("error handling %s datagram from %s", self.name, addr[0])

    def error_received(self, exc: Exception) -> None:  # pragma: no cover
        log.warning("%s socket error: %s", self.name, exc)


def _make_socket(address: str, port: int) -> socket.socket:
    if ":" in address:
        sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except OSError:  # pragma: no cover
            pass
    else:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    for size in (32 << 20, 8 << 20, 4 << 20):
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, size)
            break
        except OSError:  # pragma: no cover
            continue
    sock.bind((address, port))
    sock.setblocking(False)
    return sock


def _bind(address: str, port: int) -> socket.socket:
    try:
        return _make_socket(address, port)
    except OSError as exc:
        if address == "::":
            log.warning("cannot bind [::]:%d (%s), listening on 0.0.0.0:%d instead", port, exc, port)
            return _make_socket("0.0.0.0", port)
        raise


async def run() -> None:
    cfg = get_config()
    init_db()
    engine = get_engine()
    collector = Collector(engine, cfg.data_dir)
    collector.load_known_asns()
    collector.maybe_reload(force=True)

    loop = asyncio.get_running_loop()
    transports = []
    for port, handler, name in (
        (cfg.netflow_port, collector.handle_netflow, "netflow"),
        (cfg.sflow_port, collector.handle_sflow, "sflow"),
    ):
        if port <= 0:
            continue
        sock = _bind(cfg.listen_address, port)
        transport, _ = await loop.create_datagram_endpoint(lambda h=handler, n=name: _Protocol(h, n), sock=sock)
        transports.append(transport)
        host, bound_port = sock.getsockname()[:2]
        log.info("listening for %s on udp [%s]:%d", name, host, bound_port)

    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover
            pass

    flush_lock = asyncio.Lock()

    async def flush() -> None:
        async with flush_lock:
            snap, names = collector.take_snapshot()
            try:
                await asyncio.to_thread(collector.flush, snap, names)
            except Exception:
                log.exception("flush failed, will retry with the next flush")
                collector.restore(snap)

    async def flush_loop() -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=cfg.flush_interval)
            except asyncio.TimeoutError:
                pass
            await flush()

    async def reload_loop() -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=RELOAD_INTERVAL)
            except asyncio.TimeoutError:
                pass
            try:
                await asyncio.to_thread(collector.maybe_reload)
            except Exception:
                log.exception("config reload failed")

    tasks = [asyncio.create_task(flush_loop()), asyncio.create_task(reload_loop())]
    await stop.wait()
    log.info("shutting down collector")
    for t in transports:
        t.close()
    await asyncio.gather(*tasks, return_exceptions=True)


def main() -> None:
    cfg = get_config()
    logging.basicConfig(level=cfg.log_level, format="%(asctime)s %(levelname)s [collector] %(name)s: %(message)s")
    asyncio.run(run())
