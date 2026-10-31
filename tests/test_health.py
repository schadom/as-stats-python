"""Container health check: role detection, web check, collector heartbeat."""

import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from asstats import health


@pytest.mark.parametrize(
    "cmdline, role",
    [
        (b"/usr/local/bin/python3.13\0/usr/local/bin/asstats\0web\0", "web"),
        (b"/usr/local/bin/python3.13\0/usr/local/bin/asstats\0collector\0", "collector"),
        (b"/usr/local/bin/python3.13\0/usr/local/bin/asstats\0run\0", "run"),
        (b"/usr/local/bin/python\0-m\0asstats.cli\0collector\0", "collector"),  # launcher children
        (b"/bin/sh\0/usr/local/bin/entrypoint.sh\0", None),
        (b"", None),
    ],
)
def test_role_from_cmdline(cmdline, role):
    assert health.role_from_cmdline(cmdline) == role


def test_detect_role_override(monkeypatch):
    monkeypatch.setenv("ASSTATS_HEALTHCHECK", "collector")
    assert health.detect_role() == "collector"
    monkeypatch.setenv("ASSTATS_HEALTHCHECK", "nonsense")
    assert health.detect_role() in health.ROLES


class _Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == "/api/health" else 404)
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

    def log_message(self, *args):
        pass


def test_check_web_uses_the_configured_port():
    srv = HTTPServer(("127.0.0.1", 0), _Health)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert health.check_web("0.0.0.0", port) is None  # wildcard bind -> checked on localhost
        assert health.check_web("127.0.0.1", port) is None
    finally:
        srv.shutdown()
        srv.server_close()
    problem = health.check_web("0.0.0.0", port, timeout=1)
    assert problem and "not reachable" in problem


def test_check_collector_heartbeat(monkeypatch, tmp_path):
    hb = tmp_path / "alive"
    monkeypatch.setattr(health, "heartbeat_file", lambda: hb)
    assert health.check_collector(60) == "collector: no heartbeat yet"
    health.touch_heartbeat()
    assert health.check_collector(60) is None
    old = time.time() - 300
    os.utime(hb, (old, old))
    assert "last successful write" in health.check_collector(60)   # 300 s > 3 * 60 + 30
    assert health.check_collector(120) is None                      # 300 s < 3 * 120 + 30


def test_run_exit_codes(monkeypatch, tmp_path, capsys):
    hb = tmp_path / "alive"
    monkeypatch.setattr(health, "heartbeat_file", lambda: hb)
    monkeypatch.setenv("ASSTATS_HEALTHCHECK", "collector")
    assert health.run() == 1 and "no heartbeat" in capsys.readouterr().out
    hb.touch()
    assert health.run() == 0 and "healthy (collector)" in capsys.readouterr().out
