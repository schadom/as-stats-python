"""Container health check (`asstats healthcheck`, used by the image's HEALTHCHECK).

The same image runs the web UI (`asstats web`), the collector (`asstats collector`)
or both (`asstats run`), so the check first finds out which service this container
runs, from the command of its main process:

- web: GET /api/health on the configured web address and port
- collector: its flush loop touches a heartbeat file after every successful write
  to the database; the file must be younger than three flush intervals
- run: both

ASSTATS_HEALTHCHECK=web|collector|run overrides the detection.
"""

from __future__ import annotations

import os
import tempfile
import time
import urllib.request
from pathlib import Path

ROLES = ("web", "collector", "run")


def heartbeat_file() -> Path:
    # inside the container, not on the shared data volume: it belongs to this collector only
    return Path(tempfile.gettempdir()) / "asstats-collector.alive"


def touch_heartbeat() -> None:
    try:
        heartbeat_file().touch()
    except OSError:  # pragma: no cover - read-only /tmp
        pass


def role_from_cmdline(raw: bytes) -> str | None:
    """`asstats <role>` from a NUL separated command line, as in /proc/<pid>/cmdline."""
    args = [a for a in raw.decode(errors="replace").split("\0") if a]
    for i, arg in enumerate(args):
        if os.path.basename(arg) == "asstats" or arg == "asstats.cli":
            nxt = args[i + 1] if i + 1 < len(args) else ""
            if nxt in ROLES:
                return nxt
    return None


def detect_role() -> str:
    forced = os.environ.get("ASSTATS_HEALTHCHECK", "").strip().lower()
    if forced in ROLES:
        return forced
    try:
        role = role_from_cmdline(Path("/proc/1/cmdline").read_bytes())
    except OSError:
        role = None
    return role or "run"


def check_web(host: str, port: int, timeout: float = 4.0) -> str | None:
    """None when healthy, otherwise the reason."""
    if host in ("", "0.0.0.0", "::"):
        host = "127.0.0.1"
    if ":" in host:
        host = f"[{host}]"
    url = f"http://{host}:{port}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            if resp.status != 200:
                return f"web: {url} answered {resp.status}"
    except Exception as exc:
        return f"web: {url} not reachable ({exc})"
    return None


def check_collector(flush_interval: int, now: float | None = None) -> str | None:
    path = heartbeat_file()
    try:
        age = (now or time.time()) - path.stat().st_mtime
    except FileNotFoundError:
        return "collector: no heartbeat yet"
    limit = 3 * flush_interval + 30
    if age > limit:
        return f"collector: last successful write {age:.0f}s ago (limit {limit}s)"
    return None


def run() -> int:
    from .config import get_config

    cfg = get_config()
    role = detect_role()
    problems = []
    if role in ("web", "run"):
        problems.append(check_web(cfg.web_host, cfg.web_port))
    if role in ("collector", "run"):
        problems.append(check_collector(cfg.flush_interval))
    problems = [p for p in problems if p]
    if problems:
        print("; ".join(problems))
        return 1
    print(f"healthy ({role})")
    return 0
