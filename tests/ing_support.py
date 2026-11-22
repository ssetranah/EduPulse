"""Helpers shared by the Phase 2 ingestion tests (import as ``tests.ing_support``)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]


MAPPINGS_DIR = REPO_ROOT / "ingestion" / "configs" / "mappings"

#: mock source type -> (system label, [(entity, mapping file)])
MOCK_SOURCES: dict[str, list[tuple[str, str]]] = {
    "mock_sis": [("schools", "sis_schools.yaml"), ("students", "sis_students.yaml")],
    "mock_lms": [("lms_activity", "lms_activity.yaml")],
    "mock_assessment": [
        ("assessment_definitions", "assessment_definitions.yaml"),
        ("assessment_results", "assessment_results.yaml"),
    ],
    "mock_attendance_intervention": [
        ("attendance_daily", "attendance_daily.yaml"),
        ("interventions", "interventions.yaml"),
    ],
}

SMALL_MOCK: dict[str, Any] = {
    "seed": 11,
    "tenants": 2,
    "schools_per_tenant": 2,
    "students_per_school": 25,
    "attendance_days": 6,
    "lms_events_per_student": 4,
}


def mock_source(source_type: str, **mock_overrides: Any) -> dict[str, Any]:
    """Config dict for one mock source using the shipped mapping files."""
    return {
        "type": source_type,
        "system": {"system": source_type.upper(), "vendor": "Mock Vendor"},
        "mock": {**SMALL_MOCK, **mock_overrides},
        "entities": [
            {"name": name, "mapping": str(MAPPINGS_DIR / mapping)}
            for name, mapping in MOCK_SOURCES[source_type]
        ],
    }


def write_yaml(path: Path, body: dict[str, Any]) -> Path:
    import yaml

    path.write_text(yaml.safe_dump(body, sort_keys=False), encoding="utf-8")
    return path


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def assert_invariants(counters: dict[str, int]) -> None:
    """Record-accounting invariants that hold for every successful entity."""
    assert counters["received"] == counters["rejected"] + counters["accepted"]
    assert counters["accepted"] == (
        counters["duplicates"] + counters["unchanged_skipped"] + counters["loaded"]
    )
    assert counters["transformed"] == counters["accepted"]
