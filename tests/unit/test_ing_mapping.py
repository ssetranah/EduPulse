"""Field mapping, type coercion, null handling and row-level validation."""

from __future__ import annotations

from typing import Any

import pytest

from ingestion.config import load_config
from ingestion.errors import ConfigError
from ingestion.mapping_spec import MappingSpec, load_mapping
from ingestion.transform import RowProcessor, record_hash
from tests.ing_support import MAPPINGS_DIR

pytestmark = pytest.mark.unit


def spec(*fields: dict[str, Any], **top: Any) -> MappingSpec:
    base = {"entity": "e", "bronze_table": "e", "schema_version": "1.0.0"}
    tenant = {"source": "t", "target": "tenant_id", "required": True}
    return MappingSpec.model_validate({**base, **top, "fields": [tenant, *fields]})


def run(mapping: MappingSpec, raw: dict[str, Any], **cfg: str):
    return RowProcessor(mapping, config_values=cfg).process({"t": "T1", **raw})


def codes(errors) -> list[tuple[str, str | None]]:
    return [(e.code, e.field) for e in errors]


# ----------------------------------------------------------------------------- coercion
@pytest.mark.parametrize(
    ("type_", "raw", "expected"),
    [
        ("integer", "42", 42),
        ("integer", " 7 ", 7),
        ("integer", 5.0, 5),
        ("float", "3.5", 3.5),
        ("boolean", "Y", True),
        ("boolean", "no", False),
        ("boolean", True, True),
        ("date", "2026-03-04", "2026-03-04"),
        ("datetime", "2026-03-04T05:06:07Z", "2026-03-04T05:06:07Z"),
        ("datetime", "2026-03-04T05:06:07+02:00", "2026-03-04T03:06:07Z"),
        ("string", 12, "12"),
    ],
)
def test_coercion(type_: str, raw: Any, expected: Any) -> None:
    canonical, errors = run(spec({"source": "x", "target": "v", "type": type_}), {"x": raw})
    assert errors == []
    assert canonical["v"] == expected


@pytest.mark.parametrize(
    ("type_", "raw"),
    [
        ("integer", "4x"),
        ("integer", "3.7"),
        ("float", "abc"),
        ("boolean", "maybe"),
        ("date", "13/45/2026"),
        ("datetime", "yesterday"),
    ],
)
def test_coercion_failures_are_coded_and_value_free(type_: str, raw: str) -> None:
    canonical, errors = run(spec({"source": "x", "target": "v", "type": type_}), {"x": raw})
    assert canonical is None
    assert codes(errors) == [("TYPE_COERCION_FAILED", "v")]
    assert raw not in errors[0].message


def test_custom_date_format_and_date_with_time_formats() -> None:
    m = spec({"source": "d", "target": "d", "type": "date", "formats": ["%m/%d/%Y"]})
    assert run(m, {"d": "03/04/2026"})[0]["d"] == "2026-03-04"
    assert run(m, {"d": "2026-03-04"})[1][0].code == "TYPE_COERCION_FAILED"


# ----------------------------------------------------------------------------- nulls
@pytest.mark.parametrize("token", ["", "  ", "NULL", "n/a", "NA", "-", None])
def test_default_null_tokens_become_none(token: Any) -> None:
    canonical, errors = run(spec({"source": "x", "target": "v", "type": "integer"}), {"x": token})
    assert errors == [] and canonical["v"] is None


def test_missing_source_column_is_null_for_optional_field() -> None:
    canonical, errors = run(spec({"source": "x", "target": "v"}), {})
    assert errors == [] and canonical["v"] is None


def test_required_null_is_rejected() -> None:
    _, errors = run(spec({"source": "x", "target": "v", "required": True}), {"x": "N/A"})
    assert codes(errors) == [("MISSING_REQUIRED", "v")]


def test_default_fills_null_and_is_coerced() -> None:
    m = spec({"source": "x", "target": "v", "type": "integer", "default": "9"})
    assert run(m, {"x": ""})[0]["v"] == 9


def test_custom_null_tokens_per_mapping() -> None:
    m = spec({"source": "x", "target": "v"}, null_values=["UNKNOWN"])
    assert run(m, {"x": "UNKNOWN"})[0]["v"] is None
    assert run(m, {"x": "N/A"})[0]["v"] == "N/A"


def test_aliases_first_non_null_wins() -> None:
    m = spec({"source": ["a", "b"], "target": "v"})
    assert run(m, {"a": "", "b": "second"})[0]["v"] == "second"


# ----------------------------------------------------------------------------- validation
def test_constraints_and_multiple_errors_reported_together() -> None:
    m = spec(
        {"source": "n", "target": "n", "type": "integer", "min": 0, "max": 10},
        {"source": "c", "target": "c", "allowed_values": ["A", "B"]},
        {"source": "p", "target": "p", "pattern": "^[0-9]{3}$"},
        {"source": "s", "target": "s", "max_length": 3},
    )
    _, errors = run(m, {"n": "11", "c": "Z", "p": "12", "s": "toolong"})
    assert codes(errors) == [
        ("OUT_OF_RANGE", "n"),
        ("NOT_IN_ALLOWED_VALUES", "c"),
        ("PATTERN_MISMATCH", "p"),
        ("MAX_LENGTH_EXCEEDED", "s"),
    ]
    assert all("toolong" not in e.message and "Z" not in e.message for e in errors)


def test_value_map_translates_and_rejects_unknown_codes() -> None:
    m = spec({"source": "g", "target": "g", "value_map": {"F": "FEMALE", "M": "MALE"}})
    assert run(m, {"g": "F"})[0]["g"] == "FEMALE"
    assert codes(run(m, {"g": "Q"})[1]) == [("NOT_IN_VALUE_MAP", "g")]


def test_case_and_trim() -> None:
    m = spec({"source": "x", "target": "v", "case": "upper"})
    assert run(m, {"x": "  active "})[0]["v"] == "ACTIVE"


def test_constant_and_from_config_origins() -> None:
    m = spec(
        {"target": "k", "constant": "SYNTHETIC_TEST_DATA"},
        {"target": "z", "from_config": "tenant_id"},
    )
    canonical, _ = run(m, {}, tenant_id="cfg-tenant")
    assert canonical["k"] == "SYNTHETIC_TEST_DATA" and canonical["z"] == "cfg-tenant"


# ----------------------------------------------------------------------------- spec validation
def test_mapping_requires_tenant_id() -> None:
    with pytest.raises(ValueError, match="tenant_id"):
        MappingSpec.model_validate(
            {
                "entity": "e",
                "bronze_table": "e",
                "schema_version": "1.0.0",
                "fields": [{"source": "x", "target": "v"}],
            }
        )


@pytest.mark.parametrize(
    "bad",
    [
        {"source": "x", "target": "v", "constant": 1},  # two origins
        {"target": "v"},  # no origin
        {"source": "x", "target": "V"},  # bad target name
        {"source": "x", "target": "v", "pattern": "["},  # invalid regex
        {"source": "x", "target": "v", "surprise": 1},  # unknown key
    ],
)
def test_invalid_field_specs_are_rejected(bad: dict[str, Any]) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 - pydantic ValidationError subclasses ValueError
        spec(bad)


def test_incremental_field_must_be_datetime() -> None:
    with pytest.raises(ValueError, match="incremental_field"):
        spec({"source": "x", "target": "v"}, incremental_field="v")


def test_all_shipped_mappings_load() -> None:
    files = sorted(MAPPINGS_DIR.glob("*.yaml"))
    assert len(files) >= 10
    for file in files:
        mapping = load_mapping(file)
        assert "tenant_id" in mapping.targets and "synthetic_marker" in mapping.targets


def test_load_mapping_rejects_unsafe_yaml(tmp_path) -> None:
    bad = tmp_path / "m.yaml"
    bad.write_text("entity: !!python/object/apply:os.system ['echo hi']\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_mapping(bad)


# ----------------------------------------------------------------------------- hashing
def test_record_hash_is_order_independent_and_content_sensitive() -> None:
    assert record_hash({"a": 1, "b": 2}) == record_hash({"b": 2, "a": 1})
    assert record_hash({"a": 1}) != record_hash({"a": 2})
    assert len(record_hash({})) == 64


def test_shipped_config_validates() -> None:
    config = load_config(MAPPINGS_DIR.parent / "ingestion.yaml")
    assert {
        "mock_sis",
        "mock_lms",
        "mock_assessment",
        "mock_attendance",
        "file_drop_synthetic",
    } <= set(config.sources)


# ----------------------------------------------------------------------------- coercer edges
@pytest.mark.parametrize(
    ("type_", "raw", "expected"),
    [
        ("integer", "5.0", 5),
        ("integer", 12.0, 12),
        ("string", 12.0, "12"),
        ("string", True, "true"),
        ("boolean", 1, True),
        ("boolean", 0, False),
        ("date", __import__("datetime").datetime(2026, 2, 3, 4, 5), "2026-02-03"),
        ("date", __import__("datetime").date(2026, 2, 3), "2026-02-03"),
        ("datetime", __import__("datetime").date(2026, 2, 3), "2026-02-03T00:00:00Z"),
        ("datetime", __import__("datetime").datetime(2026, 2, 3, 4, 5, 6), "2026-02-03T04:05:06Z"),
    ],
)
def test_coercion_edges(type_: str, raw: Any, expected: Any) -> None:
    canonical, errors = run(spec({"source": "x", "target": "v", "type": type_}), {"x": raw})
    assert errors == [] and canonical["v"] == expected


@pytest.mark.parametrize(
    ("type_", "raw"),
    [
        ("integer", True),
        ("integer", 2.5),
        ("float", "nan"),
        ("float", "inf"),
        ("float", True),
        ("boolean", 2),
        ("integer", "1e999"),
    ],
)
def test_more_coercion_failures(type_: str, raw: Any) -> None:
    _, errors = run(spec({"source": "x", "target": "v", "type": type_}), {"x": raw})
    assert [e.code for e in errors] == ["TYPE_COERCION_FAILED"]


def test_custom_true_false_tokens_and_naive_datetime_format() -> None:
    m = spec(
        {
            "source": "b",
            "target": "b",
            "type": "boolean",
            "true_values": ["oui"],
            "false_values": ["non"],
        },
        {"source": "d", "target": "d", "type": "datetime", "formats": ["%d.%m.%Y %H:%M"]},
    )
    canonical, errors = run(m, {"b": "OUI", "d": "03.02.2026 04:05"})
    assert errors == [] and canonical["b"] is True and canonical["d"] == "2026-02-03T04:05:00Z"
    assert run(m, {"b": "yes", "d": "03.02.2026 04:05"})[1][0].code == "TYPE_COERCION_FAILED"


def test_value_map_accepts_already_canonical_values_when_listed_in_allowed_values() -> None:
    m = spec(
        {"source": "g", "target": "g", "value_map": {"F": "FEMALE"}, "allowed_values": ["FEMALE"]}
    )
    assert run(m, {"g": "FEMALE"})[0]["g"] == "FEMALE"
