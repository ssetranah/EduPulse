"""Mock connectors and the runner: invariants, idempotency, incremental, faults, reports."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from ingestion.bronze import BronzeStore, verify_bronze
from ingestion.config import load_config
from ingestion.errors import ConfigError
from ingestion.runner import IngestionRunner
from tests.ing_support import MAPPINGS_DIR, MOCK_SOURCES, assert_invariants, mock_source, read_json

pytestmark = pytest.mark.integration

ALL_MOCKS = list(MOCK_SOURCES)


@pytest.fixture
def runner_for(write_ingest_config, tmp_path):
    def _make(sources, *, run_id=None, dry_run=False, **top):
        path = write_ingest_config(sources, **top)
        return IngestionRunner(
            load_config(path),
            output_dir=tmp_path / "store",
            run_id=run_id,
            dry_run=dry_run,
            quiet=True,
            sleep=lambda _s: None,
        )

    return _make


def all_sources(**mock):
    return {t: mock_source(t, **mock) for t in ALL_MOCKS}


def by_entity(report):
    return {(s.name, e.entity): e for s in report.sources for e in s.entities}


# ----------------------------------------------------------------------------- happy path
def test_all_four_mock_systems_load_with_invariants(runner_for, tmp_path) -> None:
    report = runner_for(all_sources(), run_id="r1").run()
    assert report.status == "SUCCEEDED"
    entities = by_entity(report)
    assert len(entities) == 7
    for e in entities.values():
        assert e.status == "SUCCEEDED", e.failure
        assert_invariants(e.counters.to_dict())
        assert e.counters.loaded == e.counters.received > 0
    total = report.totals
    assert total.received == sum(e.counters.received for e in entities.values())
    assert BronzeStore(tmp_path / "store").iter_records()  # data really landed
    assert verify_bronze(tmp_path / "store") == []


def test_bronze_envelope_has_every_required_field(runner_for, tmp_path) -> None:
    runner_for({"mock_sis": mock_source("mock_sis")}, run_id="r1").run()
    from ingestion.bronze import ENVELOPE_FIELDS

    for row in BronzeStore(tmp_path / "store").iter_records():
        assert set(row) == set(ENVELOPE_FIELDS)
        assert row["_run_id"] == "r1" and row["_data_origin"] == "SYNTHETIC_TEST_DATA"
        assert row["tenant_id"] == row["canonical"]["tenant_id"]
        assert len(row["_source_record_hash"]) == 64 and row["_schema_version"] == "1.0.0"


def test_tenant_partitions_never_mix(runner_for, tmp_path) -> None:
    runner_for({"mock_sis": mock_source("mock_sis", tenants=3)}).run()
    store = BronzeStore(tmp_path / "store")
    dirs = store.batch_dirs()
    assert len({d.parent.parent.parent.name for d in dirs}) == 3
    for d in dirs:
        assert {r["tenant_id"] for r in store.read_batch(d)} == {
            d.parent.parent.parent.name.split("=")[1]
        }


def test_canonical_values_are_typed_and_translated(runner_for, tmp_path) -> None:
    runner_for({"mock_sis": mock_source("mock_sis")}).run()
    rows = BronzeStore(tmp_path / "store").iter_records("sis_students")
    sample = rows[0]
    assert sample["raw"]["GenderDescriptor"] in {"F", "M", "X"}
    assert sample["canonical"]["gender"] in {"FEMALE", "MALE", "NON_BINARY"}
    assert isinstance(sample["canonical"]["english_learner"], bool)
    assert sample["canonical"]["birth_date"].count("-") == 2  # ISO from MM/DD/YYYY
    assert sample["canonical"]["synthetic_marker"] == "SYNTHETIC_TEST_DATA"


def test_assessment_na_scores_are_null_not_rejected(runner_for, tmp_path) -> None:
    runner_for({"a": mock_source("mock_assessment")}).run()
    rows = BronzeStore(tmp_path / "store").iter_records("assessment_results")
    nulls = [r for r in rows if r["raw"]["ScaleScore"] == "N/A"]
    assert nulls and all(r["canonical"]["scale_score"] is None for r in nulls)


# ----------------------------------------------------------------------------- incremental
def test_rerun_is_idempotent_no_duplicate_bronze_rows(runner_for, tmp_path) -> None:
    sources = all_sources()
    first = runner_for(sources, run_id="r1").run()
    rows_after_first = len(BronzeStore(tmp_path / "store").iter_records())
    second = runner_for(sources, run_id="r2").run()
    assert len(BronzeStore(tmp_path / "store").iter_records()) == rows_after_first
    assert second.totals.loaded == 0 and second.status == "NO_NEW_DATA"
    assert first.totals.loaded > 0
    for e in by_entity(second).values():
        assert_invariants(e.counters.to_dict())


def test_incremental_as_of_loads_only_new_and_updated_records(runner_for) -> None:
    early = {"s": mock_source("mock_sis", as_of="2026-01-04T00:00:00")}
    late = {"s": mock_source("mock_sis", as_of="2026-01-20T00:00:00")}
    first = runner_for(early, run_id="r1").run()
    second = runner_for(late, run_id="r2").run()
    f, s = by_entity(first)[("s", "students")], by_entity(second)[("s", "students")]
    assert 0 < f.counters.loaded < 100  # only part of the roster existed at the early "now"
    assert s.counters.loaded > 0
    assert s.counters.received < 100  # the watermark filtered out the already-loaded history
    assert s.incremental["since"] == f.incremental["new_watermark"]
    # after both runs Bronze holds every student once + updated versions (history preserved)
    assert_invariants(s.counters.to_dict())


def test_updates_create_new_bronze_versions_never_overwrite(runner_for, tmp_path) -> None:
    big = {"students_per_school": 150}  # ~4% of students get a second version
    runner_for(
        {"s": mock_source("mock_sis", as_of="2026-01-06T00:00:00", **big)}, run_id="r1"
    ).run()
    before = {
        (r["tenant_id"], r["canonical"]["student_id"]): r["_source_record_hash"]
        for r in BronzeStore(tmp_path / "store").iter_records("sis_students")
    }
    runner_for({"s": mock_source("mock_sis", **big)}, run_id="r2").run()  # incl. updates
    rows = BronzeStore(tmp_path / "store").iter_records("sis_students")
    versions: dict[tuple[str, str], set[str]] = {}
    for r in rows:
        versions.setdefault((r["tenant_id"], r["canonical"]["student_id"]), set()).add(
            r["_source_record_hash"]
        )
    updated = [k for k, v in versions.items() if len(v) > 1]
    assert updated, "expected some students to have a second (updated) version"
    for key, old_hash in before.items():
        assert old_hash in versions[key]  # the original version is still there


def test_watermark_never_moves_backwards(runner_for, tmp_path) -> None:
    runner_for({"s": mock_source("mock_sis")}, run_id="r1").run()
    from ingestion.state import StateStore

    state = StateStore(tmp_path / "store" / "_state" / "ingest_state.sqlite")
    high = state.watermark("s", "students")
    state.close()
    runner_for({"s": mock_source("mock_sis", as_of="2026-01-03T00:00:00")}, run_id="r2").run()
    state = StateStore(tmp_path / "store" / "_state" / "ingest_state.sqlite")
    assert state.watermark("s", "students") == high
    state.close()


# ----------------------------------------------------------------------------- faults
def test_bad_rows_are_rejected_deadlettered_and_run_continues(runner_for, tmp_path) -> None:
    src = {"s": mock_source("mock_sis", students_per_school=60, faults={"bad_row_rate": 0.04})}
    report = runner_for(src, run_id="r1").run()
    e = by_entity(report)[("s", "students")]
    assert e.status == "SUCCEEDED_WITH_REJECTS" and e.counters.rejected > 0
    assert_invariants(e.counters.to_dict())
    assert report.status == "COMPLETED_WITH_REJECTS"
    injected = runner_for  # noqa: F841  (injected faults are asserted via counts below)
    dead = tmp_path / "store" / e.dead_letter_path / "rejected.jsonl"
    assert len(dead.read_text().splitlines()) == e.counters.rejected
    assert sum(e.validation.errors_by_code.values()) >= e.counters.rejected


def test_quality_gate_fails_entity_and_loads_nothing(runner_for, tmp_path) -> None:
    src = {"s": mock_source("mock_sis", faults={"bad_row_rate": 0.5})}
    report = runner_for(src).run()
    e = by_entity(report)[("s", "students")]
    assert e.status == "FAILED_QUALITY_GATE" and e.counters.loaded == 0
    assert "exceeds" in e.failure["message"]
    assert BronzeStore(tmp_path / "store").iter_records("sis_students") == []
    assert report.status in {"PARTIAL_FAILURE", "FAILED"}
    # the dead letter is kept for diagnosis even though the batch failed
    assert e.dead_letter_path and (tmp_path / "store" / e.dead_letter_path).exists()


def test_quality_gate_failure_does_not_advance_state(runner_for, tmp_path) -> None:
    runner_for({"s": mock_source("mock_sis", faults={"bad_row_rate": 0.5})}, run_id="r1").run()
    ok = runner_for({"s": mock_source("mock_sis")}, run_id="r2").run()  # same source, now healthy
    e = by_entity(ok)[("s", "students")]
    assert e.status == "SUCCEEDED" and e.counters.loaded == e.counters.received


def test_transient_failures_are_retried_and_recorded(runner_for) -> None:
    src = {"s": mock_source("mock_sis", faults={"fail_first_n_calls": 2})}
    report = runner_for(src).run()
    e = by_entity(report)[("s", "schools")]
    assert e.status == "SUCCEEDED" and e.counters.retries == 2
    assert report.status == "SUCCEEDED"


def test_persistent_failure_after_retries_fails_atomically(runner_for, tmp_path) -> None:
    src = {"s": mock_source("mock_sis", faults={"fail_first_n_calls": 999})}
    report = runner_for(src).run()
    for e in by_entity(report).values():
        assert e.status == "FAILED" and e.counters.loaded == 0
        assert "gave up after 4 attempts" in e.failure["message"]
    assert report.status == "FAILED"
    assert BronzeStore(tmp_path / "store").iter_records() == []
    assert not (tmp_path / "store" / "bronze" / "_staging").exists()


def test_duplicate_rows_from_the_source_are_counted_not_loaded_twice(runner_for, tmp_path) -> None:
    src = {"s": mock_source("mock_sis", faults={"duplicate_row_rate": 0.1})}
    e = by_entity(runner_for(src).run())[("s", "students")]
    assert e.counters.duplicates > 0
    assert_invariants(e.counters.to_dict())
    keys = [
        (r["tenant_id"], r["canonical"]["student_id"])
        for r in BronzeStore(tmp_path / "store").iter_records("sis_students")
    ]
    assert len(keys) == len(set(keys))


def test_dropped_required_field_fails_schema_check(runner_for) -> None:
    src = {"s": mock_source("mock_sis", faults={"drop_fields": ["BirthDate"]})}
    e = by_entity(runner_for(src).run())[("s", "students")]
    assert e.status == "FAILED" and "BirthDate" in e.failure["message"]
    assert e.schema_checks[0]["ok"] is False


def test_added_fields_warn_but_load(runner_for) -> None:
    src = {"s": mock_source("mock_sis", faults={"add_fields": ["BrandNewColumn"]})}
    e = by_entity(runner_for(src).run())[("s", "students")]
    assert e.status == "SUCCEEDED"
    assert e.validation.warnings_by_code["UNEXPECTED_FIELDS"] == 1
    assert e.schema_checks[0]["unexpected"] == ["BrandNewColumn"]


def test_schema_drift_between_runs_is_flagged(runner_for) -> None:
    runner_for({"s": mock_source("mock_sis")}, run_id="r1").run()
    drifted = {"s": mock_source("mock_sis", faults={"add_fields": ["Extra"]})}
    e = by_entity(runner_for(drifted, run_id="r2").run())[("s", "students")]
    assert e.schema_checks[0]["drift_from_previous"] is True
    assert "SCHEMA_DRIFT" in e.validation.warnings_by_code


def test_one_failing_entity_does_not_block_others(runner_for) -> None:
    src = {"s": mock_source("mock_sis", faults={"drop_fields": ["BirthDate"]})}  # students only
    report = runner_for(src).run()
    statuses = {e.entity: e.status for e in report.sources[0].entities}
    assert statuses == {"schools": "SUCCEEDED", "students": "FAILED"}
    assert report.status == "PARTIAL_FAILURE"


# ----------------------------------------------------------------------------- safety rails
def test_production_data_origin_is_refused(runner_for) -> None:
    src = mock_source("mock_sis")
    src["data_origin"] = "production"
    with pytest.raises(ConfigError, match="synthetic data only"):
        runner_for({"s": src}).run()


def test_synthetic_source_must_map_the_marker(write_ingest_config, tmp_path) -> None:
    import yaml

    mapping = yaml.safe_load((MAPPINGS_DIR / "sis_schools.yaml").read_text())
    mapping["fields"] = [f for f in mapping["fields"] if f["target"] != "synthetic_marker"]
    (tmp_path / "m.yaml").write_text(yaml.safe_dump(mapping))
    src = mock_source("mock_sis")
    src["entities"] = [{"name": "schools", "mapping": str(tmp_path / "m.yaml")}]
    with pytest.raises(ConfigError, match="synthetic_marker"):
        load_config(write_ingest_config({"s": src}))


def test_records_without_the_marker_value_are_rejected(write_ingest_config, tmp_path) -> None:
    import yaml

    mapping = yaml.safe_load((MAPPINGS_DIR / "sis_schools.yaml").read_text())
    for f in mapping["fields"]:
        if f["target"] == "synthetic_marker":
            f.pop("source")
            f["constant"] = "REAL_LOOKING"
    (tmp_path / "m.yaml").write_text(yaml.safe_dump(mapping))
    src = mock_source("mock_sis")
    src["entities"] = [{"name": "schools", "mapping": str(tmp_path / "m.yaml")}]
    path = write_ingest_config({"s": src})
    report = IngestionRunner(load_config(path), output_dir=tmp_path / "store", quiet=True).run()
    e = by_entity(report)[("s", "schools")]
    assert e.counters.loaded == 0 and e.validation.errors_by_code == {"SYNTHETIC_MARKER_INVALID": 4}


def test_unknown_or_disabled_source_names(runner_for) -> None:
    runner = runner_for(
        {"s": mock_source("mock_sis"), "off": {**mock_source("mock_lms"), "enabled": False}}
    )
    with pytest.raises(ConfigError, match="unknown source"):
        runner.run(["nope"])
    with pytest.raises(ConfigError, match="disabled"):
        runner.run(["off"])
    assert [s.name for s in runner.run(None).sources] == ["s"]  # 'all' skips disabled


def test_invalid_run_id_is_rejected(runner_for) -> None:
    with pytest.raises(ConfigError, match="run id"):
        runner_for({"s": mock_source("mock_sis")}, run_id="../escape")


# ----------------------------------------------------------------------------- dry run
def test_dry_run_counts_but_persists_nothing(runner_for, tmp_path) -> None:
    report = runner_for(
        {"s": mock_source("mock_sis", faults={"bad_row_rate": 0.04})}, dry_run=True
    ).run()
    e = by_entity(report)[("s", "students")]
    assert e.counters.received > 0 and e.counters.loaded > 0 and report.dry_run is True
    store = tmp_path / "store"
    assert not (store / "bronze").exists() and not (store / "dead_letter").exists()
    assert not (store / "_state").exists()
    assert (store / "runs" / report.run_id / "run_report.json").exists()  # reports are written


def test_dry_run_does_not_move_state(runner_for) -> None:
    runner_for({"s": mock_source("mock_sis")}, dry_run=True).run()
    real = runner_for({"s": mock_source("mock_sis")}).run()
    assert real.totals.loaded > 0  # a dry run did not mark anything as loaded


# ----------------------------------------------------------------------------- reports
def test_run_report_structure_and_numbers(runner_for, tmp_path) -> None:
    runner = runner_for(all_sources(), run_id="rep1")
    report = runner.run()
    data = read_json(runner.run_dir / "run_report.json")
    assert data["run_id"] == "rep1" and data["data_mode"] == "synthetic"
    assert data["report_schema_version"] == "1.0" and data["framework_version"]
    assert datetime.fromisoformat(data["started_at"]) <= datetime.fromisoformat(data["finished_at"])
    assert data["records"]["received"] == report.totals.received
    assert [s["source"] for s in data["sources"]] == ALL_MOCKS
    src = data["sources"][0]
    assert {"source_system", "status", "duration_seconds", "records", "entities"} <= set(src)
    assert src["source_system"]["data_origin"] == "synthetic"
    ent = src["entities"][0]
    assert set(ent["records"]) >= {
        "received",
        "accepted",
        "rejected",
        "transformed",
        "loaded",
        "retries",
    }
    assert ent["duration_seconds"] >= 0 and ent["schema_version"] == "1.0.0"
    assert sum(s["records"]["loaded"] for s in data["sources"]) == data["records"]["loaded"]


def test_reports_logs_and_lineage_contain_no_record_values(runner_for, tmp_path) -> None:
    src = {"s": mock_source("mock_sis", faults={"bad_row_rate": 0.05})}
    runner = runner_for(src, run_id="pii1")
    runner.run()
    rows = BronzeStore(tmp_path / "store").iter_records("sis_students")
    secrets = {r["raw"]["FirstName"] for r in rows} | {r["raw"]["LastSurname"] for r in rows}
    secrets |= {r["raw"]["StudentUniqueId"] for r in rows}
    assert len(secrets) > 50
    for file in runner.run_dir.iterdir():
        text = file.read_text()
        leaked = [s for s in secrets if len(s) > 3 and s in text]
        assert not leaked, f"{file.name} leaked {len(leaked)} record values"


def test_lineage_events_cover_every_entity(runner_for) -> None:
    runner = runner_for(all_sources(), run_id="lin1")
    report = runner.run()
    events = [
        json.loads(line)
        for line in (runner.run_dir / "lineage_events.jsonl").read_text().splitlines()
    ]
    assert len(events) == len(report.entity_results()) == 7
    for ev in events:
        assert ev["eventType"] == "COMPLETE" and ev["run"]["runId"] == "lin1"
        assert ev["inputs"][0]["facets"]["sourceSystem"]["data_origin"] == "synthetic"
        assert ev["outputs"][0]["namespace"] == "bronze"
        assert ev["outputs"][0]["facets"]["schemaVersion"] == "1.0.0"


def test_validation_report_lists_codes_and_locations_only(runner_for) -> None:
    src = {"s": mock_source("mock_sis", faults={"bad_row_rate": 0.05})}
    runner = runner_for(src, run_id="val1")
    runner.run()
    data = read_json(runner.run_dir / "validation_report.json")
    entity = next(e for e in data["entities"] if e["entity"] == "students")
    assert entity["validation"]["errors_by_code"]
    for ex in entity["validation"]["examples"]:
        assert set(ex) == {"location", "errors"} and ex["location"].startswith("mock://")


def test_structured_log_file_is_json_lines_with_events(runner_for) -> None:
    runner = runner_for({"s": mock_source("mock_sis")}, run_id="log1")
    runner.run()
    lines = [json.loads(x) for x in (runner.run_dir / "ingestion.log").read_text().splitlines()]
    events = [x["event"] for x in lines]
    assert events[0] == "run_started" and events[-1] == "run_finished"
    assert {"source_started", "entity_started", "entity_finished", "source_finished"} <= set(events)
    done = next(x for x in lines if x["event"] == "entity_finished")
    assert {"source", "entity", "status", "received", "loaded", "rejected"} <= set(done)


def test_cross_source_reconciliation_file_drop_vs_mock_sis(
    runner_for, write_ingest_config, tmp_path, clean_data
) -> None:
    """The same students arriving as a CSV drop and from the mock SIS land identically."""
    from ingestion.synthetic import write_dataset
    from tests.ing_support import MAPPINGS_DIR

    drop = tmp_path / "drop"
    write_dataset(clean_data, drop)
    cfg = {
        "csv": {
            "type": "file_drop",
            "system": {"system": "FILE_DROP"},
            "drop_dir": str(drop),
            "entities": [
                {
                    "name": "students",
                    "mapping": str(MAPPINGS_DIR / "filedrop_students.yaml"),
                    "file_pattern": "sis/students.csv",
                    "format": "csv",
                }
            ],
        },
        "sis": mock_source(
            "mock_sis", seed=7, tenants=2, schools_per_tenant=3, students_per_school=40
        ),
    }
    cfg["sis"]["entities"] = [e for e in cfg["sis"]["entities"] if e["name"] == "students"]
    report = runner_for(cfg).run()
    assert report.status == "SUCCEEDED"
    store = BronzeStore(tmp_path / "store")

    def students(source: str):
        out = {}
        for r in store.iter_records("sis_students"):
            if r["_source_name"] == source:
                out[(r["tenant_id"], r["canonical"]["student_id"])] = r["canonical"]
        return out

    csv_rows, sis_rows = students("csv"), students("sis")
    assert csv_rows.keys() == sis_rows.keys() and len(csv_rows) == 240
    compare = [
        "school_id",
        "first_name",
        "last_name",
        "birth_date",
        "grade_level",
        "gender",
        "english_learner",
        "special_education",
        "enrollment_start_date",
    ]
    mismatches = [k for k in csv_rows if any(csv_rows[k][f] != sis_rows[k][f] for f in compare)]
    assert mismatches == []
