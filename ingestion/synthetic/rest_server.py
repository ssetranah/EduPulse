"""A tiny mock vendor REST API serving synthetic data (standard library only).

Routes (``<entity>`` is any entity known to ``native.py``, e.g. ``students``)::

    GET /v1/<entity>?page=1&page_size=100&updated_since=<ISO>   page-number pagination
    GET /v2/<entity>?cursor=<opaque>&page_size=100&updated_since=<ISO>   cursor pagination
    GET /v1/redirect       -> 302 (the connector must refuse redirects)
    GET /v1/badjson        -> 200 with a body that is not JSON

Behavior switches (for tests and demos): optional bearer ``token``, ``fail_first_n`` (HTTP 503),
``rate_limit_first_n`` (HTTP 429 with ``Retry-After``), ``as_of`` (the server's "now").
It binds to loopback only and serves synthetic data only.

CLI::

    python -m ingestion.synthetic.rest_server --port 8765
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import json
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import GeneratorConfig
from .generator import GeneratedData, generate
from .native import NATIVE_SPECS, build_native_records, native_spec_for_entity


class MockRestServer:
    def __init__(
        self,
        config: GeneratorConfig | None = None,
        *,
        port: int = 0,
        token: str | None = None,
        fail_first_n: int = 0,
        rate_limit_first_n: int = 0,
        as_of: datetime | None = None,
    ) -> None:
        self.data: GeneratedData = generate(
            config or GeneratorConfig(tenants=2, schools_per_tenant=3, students_per_school=60)
        )
        self.token = token
        self.fail_first_n = fail_first_n
        self.rate_limit_first_n = rate_limit_first_n
        self.as_of = as_of
        self.requests: list[tuple[str, dict[str, str]]] = []
        self._count = 0
        self._lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                outer._handle(self)

            def log_message(self, *_: Any) -> None:  # silence default stderr logging
                return

        self._httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ lifecycle
    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._httpd.server_address[1]}"

    def start(self) -> MockRestServer:
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread:
            self._thread.join(timeout=5)

    def __enter__(self) -> MockRestServer:
        return self.start()

    def __exit__(self, *_: object) -> None:
        self.stop()

    # ------------------------------------------------------------------ request handling
    def _send(
        self,
        h: BaseHTTPRequestHandler,
        status: int,
        body: bytes,
        headers: dict[str, str] | None = None,
    ) -> None:
        h.send_response(status)
        h.send_header("Content-Type", "application/json")
        h.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            h.send_header(key, value)
        h.end_headers()
        h.wfile.write(body)

    def _json(self, h: BaseHTTPRequestHandler, status: int, payload: Any, **kw: Any) -> None:
        self._send(h, status, json.dumps(payload).encode(), **kw)

    def _handle(self, h: BaseHTTPRequestHandler) -> None:
        parsed = urlparse(h.path)
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        with self._lock:
            self._count += 1
            call_no = self._count
            self.requests.append((parsed.path, query))
        if self.token is not None and h.headers.get("Authorization") != f"Bearer {self.token}":
            return self._json(h, 401, {"error": "unauthorized"})
        if call_no <= self.rate_limit_first_n:
            return self._json(h, 429, {"error": "rate limited"}, headers={"Retry-After": "0"})
        if call_no <= self.rate_limit_first_n + self.fail_first_n:
            return self._json(h, 503, {"error": "unavailable"})

        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) != 2 or parts[0] not in ("v1", "v2"):
            return self._json(h, 404, {"error": "not found"})
        version, entity = parts
        if entity == "redirect":
            return self._send(h, 302, b"", headers={"Location": "http://example.invalid/"})
        if entity == "badjson":
            return self._send(h, 200, b"<html>not json</html>")
        try:
            spec = native_spec_for_entity(entity)
        except KeyError:
            return self._json(h, 404, {"error": "unknown entity"})

        records = build_native_records(self.data, spec.system, spec.entity, as_of=self.as_of)
        if "updated_since" in query:
            try:
                since = datetime.fromisoformat(query["updated_since"]).astimezone(UTC)
            except ValueError:
                return self._json(h, 400, {"error": "bad updated_since"})
            records = [r for r in records if r[0] >= since]
        rows = [r[1] for r in records]
        try:
            size = max(1, min(int(query.get("page_size", "100")), 1000))
        except ValueError:
            return self._json(h, 400, {"error": "bad page_size"})

        if version == "v1":
            page = max(1, int(query.get("page", "1")))
            chunk = rows[(page - 1) * size : page * size]
            total_pages = max(1, -(-len(rows) // size))
            return self._json(h, 200, {"data": chunk, "page": page, "total_pages": total_pages})

        offset = 0
        if query.get("cursor"):
            try:
                offset = int(base64.urlsafe_b64decode(query["cursor"]).decode())
            except (ValueError, UnicodeDecodeError):
                return self._json(h, 400, {"error": "bad cursor"})
        chunk = rows[offset : offset + size]
        nxt = offset + size
        cursor = base64.urlsafe_b64encode(str(nxt).encode()).decode() if nxt < len(rows) else None
        return self._json(h, 200, {"data": chunk, "next_cursor": cursor})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Mock vendor REST API (synthetic data, loopback only)"
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--token", help="require 'Authorization: Bearer <token>'")
    args = parser.parse_args(argv)
    server = MockRestServer(port=args.port, token=args.token)
    entities = sorted(s.entity for s in NATIVE_SPECS.values())
    print(f"mock REST API on {server.base_url} (entities: {entities})")
    with contextlib.suppress(KeyboardInterrupt):
        server._httpd.serve_forever()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
