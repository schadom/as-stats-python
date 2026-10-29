"""Run the web app and the collector side by side (used as the container command).

Children inherit stdout/stderr, so logs go straight to `docker logs`. A child
that exits is restarted with backoff; SIGTERM/SIGINT stop both cleanly.
"""

from __future__ import annotations

import logging
import signal
import subprocess
import sys
import time

log = logging.getLogger("asstats.launcher")

SERVICES = ("web", "collector")
STABLE_AFTER = 60  # seconds a child must run before its backoff resets
STOP_TIMEOUT = 30


class Child:
    def __init__(self, name: str):
        self.name = name
        self.proc: subprocess.Popen | None = None
        self.started = 0.0
        self.failures = 0
        self.next_start = 0.0

    def start(self) -> None:
        self.proc = subprocess.Popen([sys.executable, "-m", "asstats.cli", self.name])
        self.started = time.monotonic()
        log.info("started %s (pid %d)", self.name, self.proc.pid)


def run() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [launcher] %(message)s")
    children = [Child(name) for name in SERVICES]
    stopping = False

    def stop(signum, _frame):
        nonlocal stopping
        if not stopping:
            log.info("received %s, stopping", signal.Signals(signum).name)
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    for child in children:
        child.start()

    while not stopping:
        now = time.monotonic()
        for child in children:
            if child.proc is not None and child.proc.poll() is not None:
                code = child.proc.returncode
                ran = now - child.started
                child.failures = 0 if ran > STABLE_AFTER else child.failures + 1
                delay = min(2 ** child.failures, 60) if child.failures else 1
                log.warning("%s exited with code %s after %.0fs, restarting in %ds", child.name, code, ran, delay)
                child.proc = None
                child.next_start = now + delay
            if child.proc is None and now >= child.next_start:
                child.start()
        time.sleep(0.5)

    for child in children:
        if child.proc is not None and child.proc.poll() is None:
            child.proc.terminate()
    deadline = time.monotonic() + STOP_TIMEOUT
    for child in children:
        if child.proc is None:
            continue
        try:
            child.proc.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            log.warning("%s did not stop in time, killing it", child.name)
            child.proc.kill()
            child.proc.wait()
    log.info("stopped")
    return 0
