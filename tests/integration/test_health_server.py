"""Runs the real health server on an ephemeral port and probes it over HTTP."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from api.health import make_server

pytestmark = pytest.mark.integration


@pytest.fixture
def base_url():
    server = make_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def fetch(url):
    with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 - loopback test server
        return resp.status, dict(resp.headers), json.loads(resp.read())


def test_healthz(base_url):
    status, headers, body = fetch(base_url + "/healthz")
    assert status == 200
    assert body["status"] == "ok"
    assert body["data_mode"] == "synthetic"
    assert headers["Content-Type"] == "application/json"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Cache-Control"] == "no-store"


def test_version(base_url):
    status, _, body = fetch(base_url + "/version")
    assert status == 200
    assert body["service"] == "edupulse-api"
    assert body["version"]


def test_query_string_is_ignored(base_url):
    assert fetch(base_url + "/healthz?probe=1")[0] == 200


def test_unknown_path_is_404_json(base_url):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        fetch(base_url + "/students")
    assert excinfo.value.code == 404
    assert json.loads(excinfo.value.read()) == {"error": "not_found"}
