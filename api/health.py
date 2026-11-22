"""Minimal, dependency-free health/version server (Phase 1 placeholder).

Intentionally stdlib-only: it exposes no student data and has no business logic. It is replaced
by the real API application (framework chosen in the phase that adds the first data endpoint);
``/healthz`` and ``/version`` are expected to stay as the liveness/diagnostic contract.
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__

SERVICE_NAME = "edupulse-api"


def health_payload() -> dict[str, str]:
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "version": __version__,
        # Phase 1 only ever runs against synthetic data; make that visible to every consumer.
        "data_mode": os.environ.get("EDUPULSE_DATA_MODE", "synthetic"),
        "environment": os.environ.get("EDUPULSE_ENV", "local"),
    }


class HealthHandler(BaseHTTPRequestHandler):
    server_version = "EduPulseHealth"
    sys_version = ""  # do not advertise the Python version

    def _send(self, status: int, body: dict[str, str]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            self._send(200, health_payload())
        elif path == "/version":
            self._send(200, {"service": SERVICE_NAME, "version": __version__})
        else:
            self._send(404, {"error": "not_found"})

    def log_message(self, format: str, *args: object) -> None:
        # One line per request on stderr, no query strings or client payloads.
        super().log_message(format, *[str(a).split("?", 1)[0] for a in args])


def make_server(host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), HealthHandler)


def main() -> None:
    # Bind address is configurable; containers set 0.0.0.0 so the published port is reachable.
    host = os.environ.get("EDUPULSE_API_HOST", "127.0.0.1")
    port = int(os.environ.get("EDUPULSE_API_PORT", "8000"))
    server = make_server(host, port)
    print(f"{SERVICE_NAME} {__version__} listening on {host}:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":  # pragma: no cover
    main()
