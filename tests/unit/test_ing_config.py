"""Ingestion config validation and the connector interface contract."""

from __future__ import annotations

import inspect

import pytest

from ingestion.config import load_config
from ingestion.connectors import CONNECTORS, SourceConnector, build_connector
from ingestion.errors import ConfigError
from ingestion.retry import RetryPolicy
from tests.ing_support import mock_source

pytestmark = pytest.mark.unit


def test_unknown_keys_are_rejected(write_ingest_config) -> None:
    src = mock_source("mock_sis")
    src["suprise"] = 1
    with pytest.raises(ConfigError, match="suprise"):
        load_config(write_ingest_config({"s": src}))


def test_unknown_connector_type_rejected(write_ingest_config) -> None:
    with pytest.raises(ConfigError, match="type"):
        load_config(write_ingest_config({"s": {**mock_source("mock_sis"), "type": "ftp"}}))


def test_entity_must_match_its_mapping(write_ingest_config) -> None:
    src = mock_source("mock_sis")
    src["entities"][0]["name"] = "wrong_name"
    with pytest.raises(ConfigError, match="points at a mapping"):
        load_config(write_ingest_config({"s": src}))


def test_missing_mapping_file(write_ingest_config) -> None:
    src = mock_source("mock_sis")
    src["entities"][0]["mapping"] = "/nonexistent/map.yaml"
    with pytest.raises(ConfigError, match="cannot read mapping"):
        load_config(write_ingest_config({"s": src}))


def test_duplicate_entity_names(write_ingest_config) -> None:
    src = mock_source("mock_sis")
    src["entities"].append(dict(src["entities"][0]))
    with pytest.raises(ConfigError, match="duplicate entity"):
        load_config(write_ingest_config({"s": src}))


def test_retry_bounds_and_overrides(write_ingest_config) -> None:
    src = mock_source("mock_sis")
    src["retry"] = {"max_attempts": 0}
    with pytest.raises(ConfigError, match="max_attempts"):
        load_config(write_ingest_config({"s": src}))
    cfg = load_config(
        write_ingest_config({"s": mock_source("mock_sis")}),
        overrides=["sources.s.retry={max_attempts: 2}"],
    )
    assert cfg.retry_for("s").max_attempts == 2
    with pytest.raises(ConfigError, match="does not exist"):
        load_config(write_ingest_config({"s": mock_source("mock_sis")}), overrides=["a.b.c=1"])
    with pytest.raises(ConfigError, match="dotted"):
        load_config(write_ingest_config({"s": mock_source("mock_sis")}), overrides=["novalue"])


def test_bearer_auth_requires_env_var_name(write_ingest_config) -> None:
    src = {
        "type": "rest_api",
        "system": {"system": "X"},
        "base_url": "https://x.example.org",
        "auth": {"type": "bearer"},
        "entities": [],
    }
    with pytest.raises(ConfigError, match="token_env"):
        load_config(write_ingest_config({"s": src}))


def test_yaml_with_python_tags_is_not_executed(tmp_path) -> None:
    bad = tmp_path / "c.yaml"
    bad.write_text("sources: !!python/object/apply:os.system ['echo pwned']\n")
    with pytest.raises(ConfigError):
        load_config(bad)


# ----------------------------------------------------------------------------- connector contract
ABSTRACT = {
    "discover",
    "extract",
    "validate_schema",
    "plan_incremental",
    "advance_watermark",
    "handle_error",
}


def test_interface_declares_the_five_responsibilities() -> None:
    assert SourceConnector.__abstractmethods__ >= ABSTRACT
    with pytest.raises(TypeError):
        SourceConnector("x", None, retry_policy=RetryPolicy())  # type: ignore[abstract]


def test_all_six_connectors_registered_and_concrete() -> None:
    assert set(CONNECTORS) == {
        "file_drop",
        "rest_api",
        "mock_sis",
        "mock_lms",
        "mock_assessment",
        "mock_attendance_intervention",
    }
    for cls in CONNECTORS.values():
        assert not inspect.isabstract(cls)
        assert issubclass(cls, SourceConnector) and cls.extraction_method != "unspecified"


@pytest.mark.parametrize(
    "source_type", ["mock_sis", "mock_lms", "mock_assessment", "mock_attendance_intervention"]
)
def test_connector_metadata_discovery_and_incremental_plan(
    source_type, write_ingest_config
) -> None:
    cfg = load_config(write_ingest_config({"s": mock_source(source_type)}))
    connector = build_connector("s", cfg.source("s"), retry_policy=RetryPolicy())
    meta = connector.metadata
    assert meta.source_name == "s" and meta.connector_type == source_type
    assert meta.data_origin == "synthetic" and meta.vendor == "Mock Vendor"
    found = connector.discover()
    assert [d.name for d in found] == [e.name for e in cfg.source("s").entities]
    mapping = cfg.load_mapping(cfg.source("s").entities[0])
    plan = connector.plan_incremental(cfg.source("s").entities[0], mapping, "2026-01-02T00:00:00Z")
    assert plan.strategy == "watermark" and plan.since == "2026-01-02T00:00:00Z"


def test_default_error_classification() -> None:
    from ingestion.errors import (
        AuthenticationError,
        PermanentSourceError,
        SchemaError,
        TransientSourceError,
    )
    from ingestion.models import ErrorAction, ErrorContext

    ctx = ErrorContext("s", "e", "extract")
    decide = SourceConnector.default_error_decision
    assert decide(AuthenticationError("x"), ctx).action is ErrorAction.ABORT_SOURCE
    assert decide(TransientSourceError("x"), ctx).action is ErrorAction.RETRY
    assert decide(SchemaError("x"), ctx).action is ErrorAction.SKIP_STREAM
    assert decide(PermanentSourceError("x"), ctx).action is ErrorAction.FAIL_ENTITY
    assert decide(ValueError("x"), ctx).action is ErrorAction.FAIL_ENTITY


def test_watermark_is_monotonic() -> None:
    adv = SourceConnector.default_advance_watermark
    assert adv(None, None) is None
    assert adv("2026-01-05T00:00:00Z", "2026-01-03T00:00:00Z") == "2026-01-05T00:00:00Z"
    assert adv(None, "2026-01-03T00:00:00Z") == "2026-01-03T00:00:00Z"
