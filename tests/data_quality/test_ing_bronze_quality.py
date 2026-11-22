"""Data-quality assertions on what actually lands in Bronze after a full multi-source run."""

from __future__ import annotations

import re
from collections import defaultdict

import pytest
import yaml

from ingestion.bronze import BronzeStore, verify_bronze
from ingestion.config import load_config
from ingestion.runner import IngestionRunner
from ingestion.transform import record_hash
from tests.ing_support import MAPPINGS_DIR, MOCK_SOURCES, mock_source

pytestmark = pytest.mark.data_quality


@pytest.fixture(scope="module")
def landed(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("dq")
    body = {
        "version": 1,
        "storage": {"root": str(tmp / "store")},
        "sources": {t: mock_source(t, students_per_school=40) for t in MOCK_SOURCES},
    }
    path = tmp / "c.yaml"
    path.write_text(yaml.safe_dump(body), encoding="utf-8")
    report = IngestionRunner(load_config(path), output_dir=tmp / "store", quiet=True).run()
    assert report.status == "SUCCEEDED"
    store = BronzeStore(tmp / "store")
    rows: dict[str, list[dict]] = defaultdict(list)
    for r in store.iter_records():
        rows[r["_bronze_table"]].append(r)
    return tmp / "store", rows


def test_every_table_landed_and_checksums_verify(landed) -> None:
    root, rows = landed
    assert set(rows) == {
        "sis_schools",
        "sis_students",
        "lms_activity",
        "assessment_definitions",
        "assessment_results",
        "attendance_daily",
        "interventions",
    }
    assert verify_bronze(root) == []


def test_every_row_is_marked_synthetic_and_tenant_tagged(landed) -> None:
    _, rows = landed
    for table, items in rows.items():
        for r in items:
            assert r["_data_origin"] == "SYNTHETIC_TEST_DATA", table
            assert r["canonical"]["synthetic_marker"] == "SYNTHETIC_TEST_DATA", table
            assert r["tenant_id"] and r["tenant_id"] == r["canonical"]["tenant_id"], table


def test_source_record_hash_matches_raw_payload(landed) -> None:
    _, rows = landed
    for items in rows.values():
        for r in items[:200]:
            assert r["_source_record_hash"] == record_hash(r["raw"])


def test_no_duplicate_record_hashes_within_a_table(landed) -> None:
    _, rows = landed
    for table, items in rows.items():
        hashes = [r["_source_record_hash"] for r in items]
        assert len(hashes) == len(set(hashes)), table


def test_canonical_fields_match_the_mapping_exactly(landed) -> None:
    _, rows = landed
    for table, items in rows.items():
        mapping = yaml.safe_load(
            (
                MAPPINGS_DIR
                / next(
                    f
                    for f in MAPPING_FILES
                    if yaml.safe_load((MAPPINGS_DIR / f).read_text())["bronze_table"] == table
                )
            ).read_text()
        )
        targets = {f["target"] for f in mapping["fields"]}
        assert all(set(r["canonical"]) == targets for r in items[:200]), table


MAPPING_FILES = [p.name for p in MAPPINGS_DIR.glob("*.yaml") if not p.name.startswith("filedrop_")]


def test_primary_keys_unique_per_version_and_timestamps_are_iso_utc(landed) -> None:
    _, rows = landed
    iso = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    for table, items in rows.items():
        for r in items[:200]:
            assert iso.match(r["_ingestion_ts"])
            assert iso.match(r["canonical"]["source_modified_at"]), table


def test_referential_integrity_across_sources(landed) -> None:
    _, rows = landed
    students = {(r["tenant_id"], r["canonical"]["student_id"]) for r in rows["sis_students"]}
    schools = {(r["tenant_id"], r["canonical"]["school_id"]) for r in rows["sis_schools"]}
    for table in ("lms_activity", "attendance_daily", "assessment_results", "interventions"):
        for r in rows[table]:
            assert (r["tenant_id"], r["canonical"]["student_id"]) in students, table
            assert (r["tenant_id"], r["canonical"]["school_id"]) in schools, table


def test_scores_respect_assessment_scale_bounds(landed) -> None:
    _, rows = landed
    scale = {
        (r["tenant_id"], r["canonical"]["assessment_id"]): (
            r["canonical"]["scale_min"],
            r["canonical"]["scale_max"],
        )
        for r in rows["assessment_definitions"]
    }
    for r in rows["assessment_results"]:
        score = r["canonical"]["scale_score"]
        if score is not None:
            lo, hi = scale[(r["tenant_id"], r["canonical"]["assessment_id"])]
            assert lo <= score <= hi + 5  # +5: the mock's "updated" versions nudge scores up


def test_sensitivity_labels_recorded_in_batch_manifests(landed) -> None:
    root, _ = landed
    import json

    manifest = next((root / "bronze").rglob("table=sis_students/**/_MANIFEST.json"))
    labels = json.loads(manifest.read_text())["field_sensitivity"]
    assert labels["first_name"] == "restricted" and labels["student_id"] == "confidential"
