import pytest

from api.health import SERVICE_NAME, health_payload

pytestmark = pytest.mark.unit


def test_health_payload_defaults(monkeypatch):
    monkeypatch.delenv("EDUPULSE_DATA_MODE", raising=False)
    monkeypatch.delenv("EDUPULSE_ENV", raising=False)
    payload = health_payload()
    assert payload["status"] == "ok"
    assert payload["service"] == SERVICE_NAME
    assert payload["data_mode"] == "synthetic"
    assert payload["environment"] == "local"


def test_health_payload_reflects_environment(monkeypatch):
    monkeypatch.setenv("EDUPULSE_ENV", "ci")
    assert health_payload()["environment"] == "ci"
