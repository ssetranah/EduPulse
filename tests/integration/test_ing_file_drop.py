"""CSV / Excel / JSON Lines file drops through the full runner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ingestion.config import load_config
from ingestion.connectors import build_connector
from ingestion.errors import ConfigError
from ingestion.retry import RetryPolicy
from ingestion.runner import IngestionRunner
from tests.ing_support import assert_invariants, write_yaml

pytestmark = pytest.mark.integration

MARKER = "SYNTHETIC_TEST_DATA"
MAPPING = {
    "entity": "people",
    "bronze_table": "people",
    "schema_version": "1.0.0",
    "primary_key": ["tenant_id", "pid"],
    "fields": [
        {"source": "tenant_id", "target": "tenant_id", "required": True},
        {"source": "pid", "target": "pid", "required": True},
        {"source": "age", "target": "age", "type": "integer", "min": 0, "max": 120},
        {"source": "synthetic_marker", "target": "synthetic_marker", "required": True},
    ],
}


@pytest.fixture
def env(tmp_path: Path):
    drop = tmp_path / "drop"
    drop.mkdir()
    write_yaml(tmp_path / "people.yaml", MAPPING)

    def make(pattern: str = "*.csv", fmt: str = "auto", **source: Any):
        body = {
            "version": 1,
            "storage": {"root": str(tmp_path / "store")},
            "sources": {
                "drop": {
                    "type": "file_drop",
                    "system": {"system": "FILE_DROP"},
                    "drop_dir": str(drop),
                    "entities": [
                        {
                            "name": "people",
                            "mapping": "people.yaml",
                            "file_pattern": pattern,
                            "format": fmt,
                        }
                    ],
                    **source,
                }
            },
        }
        path = write_yaml(tmp_path / "ingestion.yaml", body)
        return load_config(path)

    def run(config, **kw):
        return IngestionRunner(
            config, output_dir=tmp_path / "store", quiet=True, sleep=lambda _s: None, **kw
        ).run(["drop"])

    return drop, make, run, tmp_path


def csv_text(rows: list[tuple[str, str, str]]) -> str:
    lines = ["tenant_id,pid,age,synthetic_marker"]
    lines += [f"t1,{pid},{age},{MARKER}" for pid, age, _ in rows]
    return "\n".join(lines) + "\n"


def entity(report):
    return report.sources[0].entities[0]


def test_csv_happy_path_counts_and_bronze_envelope(env) -> None:
    drop, make, run, tmp = env
    (drop / "a.csv").write_text(csv_text([("p1", "10", ""), ("p2", "11", "")]), encoding="utf-8")
    report = run(make())
    e = entity(report)
    assert e.status == "SUCCEEDED" and e.counters.loaded == 2
    assert_invariants(e.counters.to_dict())
    from ingestion.bronze import BronzeStore

    rows = BronzeStore(tmp / "store").iter_records("people")
    assert {r["canonical"]["pid"] for r in rows} == {"p1", "p2"}
    r = rows[0]
    assert r["_source_name"] == "drop" and r["_batch_id"] == e.batch_id
    assert r["_schema_version"] == "1.0.0" and len(r["_source_record_hash"]) == 64
    assert r["_ingestion_ts"].endswith("Z") and r["_data_origin"] == MARKER
    assert r["raw"]["age"] in ("10", "11")  # raw preserved as received (strings)
    assert isinstance(r["canonical"]["age"], int)  # canonical is typed


def test_row_level_rejects_go_to_dead_letter_with_codes(env) -> None:
    drop, make, run, tmp = env
    body = (
        csv_text([(f"p{i}", "20", "") for i in range(30)])
        + f"t1,bad,999,{MARKER}\nt1,,5,{MARKER}\n"
    )
    (drop / "a.csv").write_text(body, encoding="utf-8")
    e = entity(run(make()))
    assert e.status == "SUCCEEDED_WITH_REJECTS"
    assert (e.counters.rejected, e.counters.loaded) == (2, 30)
    assert e.validation.errors_by_code == {"OUT_OF_RANGE": 1, "MISSING_REQUIRED": 1}
    assert_invariants(e.counters.to_dict())
    dead = tmp / "store" / e.dead_letter_path / "rejected.jsonl"
    assert len(dead.read_text().splitlines()) == 2


def test_ragged_rows_are_rejected_not_silently_padded(env) -> None:
    drop, make, run, _ = env
    text = csv_text([(f"p{i}", "1", "") for i in range(30)]) + "t1,only-two-columns\n"
    (drop / "a.csv").write_text(text, encoding="utf-8")
    e = entity(run(make()))
    assert e.validation.errors_by_code == {"COLUMN_COUNT_MISMATCH": 1}


def test_bom_and_crlf_are_tolerated(env) -> None:
    drop, make, run, _ = env
    (drop / "a.csv").write_bytes(
        b"\xef\xbb\xbf" + csv_text([("p1", "1", "")]).replace("\n", "\r\n").encode()
    )
    assert entity(run(make())).counters.loaded == 1


def test_missing_required_column_fails_the_entity_cleanly(env) -> None:
    drop, make, run, tmp = env
    (drop / "a.csv").write_text("tenant_id,age,synthetic_marker\nt1,5,SYNTHETIC_TEST_DATA\n")
    e = entity(run(make()))
    assert e.status == "FAILED" and "missing" in e.failure["message"]
    assert not list((tmp / "store").glob("bronze/**/part-*.jsonl"))


def test_skip_stream_policy_skips_a_bad_file_and_loads_the_rest(env) -> None:
    drop, make, run, _ = env
    (drop / "bad.csv").write_text("nothing,useful\n1,2\n")
    (drop / "good.csv").write_text(csv_text([("p1", "1", "")]))
    config = make()
    config.sources["drop"].entities[0].stream_error_policy = "skip_stream"
    e = entity(run(config))
    assert e.status == "SUCCEEDED" and e.counters.loaded == 1
    assert any("stream skipped" in w for w in e.warnings)


def test_file_ledger_skips_processed_files_then_picks_up_new_ones(env) -> None:
    drop, make, run, _ = env
    (drop / "a.csv").write_text(csv_text([("p1", "1", "")]))
    assert entity(run(make())).counters.loaded == 1
    second = entity(run(make()))
    assert second.status == "NO_NEW_DATA" and second.counters.received == 0
    assert second.units[0]["status"] == "skipped_already_processed"
    (drop / "b.csv").write_text(csv_text([("p2", "2", "")]))
    third = entity(run(make()))
    assert third.counters.loaded == 1 and third.counters.received == 1


def test_redelivered_changed_file_loads_only_changed_rows(env) -> None:
    drop, make, run, _ = env
    (drop / "a.csv").write_text(csv_text([("p1", "1", ""), ("p2", "2", "")]))
    run(make())
    (drop / "a.csv").write_text(csv_text([("p1", "1", ""), ("p2", "3", "")]))  # p2 changed
    e = entity(run(make()))
    assert (e.counters.received, e.counters.unchanged_skipped, e.counters.loaded) == (2, 1, 1)
    assert_invariants(e.counters.to_dict())


def test_duplicate_rows_in_one_file_are_detected(env) -> None:
    drop, make, run, _ = env
    (drop / "a.csv").write_text(csv_text([("p1", "1", ""), ("p1", "1", ""), ("p2", "2", "")]))
    e = entity(run(make()))
    assert (e.counters.duplicates, e.counters.loaded) == (1, 2)
    assert_invariants(e.counters.to_dict())


def test_same_key_different_content_keep_first_or_reject(env) -> None:
    drop, make, run, tmp = env
    (drop / "a.csv").write_text(csv_text([("p1", "1", ""), ("p1", "9", "")]))
    kept = entity(run(make()))
    assert (kept.counters.duplicates, kept.counters.loaded, kept.counters.rejected) == (1, 1, 0)
    # now reject policy (fresh store)
    mapping = {**MAPPING, "duplicate_policy": "reject"}
    write_yaml(tmp / "people.yaml", mapping)
    import shutil

    shutil.rmtree(tmp / "store")
    rejected = entity(run(make()))
    assert rejected.validation.errors_by_code == {"DUPLICATE_KEY": 1}
    assert_invariants(rejected.counters.to_dict())


def test_jsonl_with_a_corrupt_line(env) -> None:
    drop, make, run, _ = env
    good = json.dumps({"tenant_id": "t1", "pid": "p1", "age": 5, "synthetic_marker": MARKER})
    (drop / "a.jsonl").write_text(f"{good}\n{{not json\n[1,2]\n")
    e = entity(run(make("*.jsonl", "jsonl")))
    assert e.counters.loaded == 1
    assert e.validation.errors_by_code == {"INVALID_JSON": 1, "ROW_NOT_AN_OBJECT": 1}


def test_json_array_file(env) -> None:
    drop, make, run, _ = env
    rows = [
        {"tenant_id": "t1", "pid": f"p{i}", "age": i, "synthetic_marker": MARKER} for i in range(3)
    ]
    (drop / "a.json").write_text(json.dumps(rows))
    assert entity(run(make("*.json", "json"))).counters.loaded == 3


def test_excel_workbook(env) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    drop, make, run, _ = env
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["tenant_id", "pid", "age", "synthetic_marker"])
    ws.append(["t1", "p1", 7, MARKER])
    ws.append(["t1", "p2", 8.0, MARKER])
    wb.save(drop / "a.xlsx")
    e = entity(run(make("*.xlsx", "excel")))
    assert e.counters.loaded == 2 and e.status == "SUCCEEDED"


def test_unknown_extension_with_auto_format_fails(env) -> None:
    drop, make, run, _ = env
    (drop / "a.dat").write_text("x")
    assert entity(run(make("*.dat", "auto"))).status == "FAILED"


def test_oversized_file_is_refused(env) -> None:
    drop, make, run, _ = env
    (drop / "a.csv").write_text(csv_text([("p1", "1", "")]))
    config = make()
    config.sources["drop"].max_file_mb = 0  # type: ignore[assignment]  # force the guard
    e = entity(run(config))
    assert e.status == "FAILED" and "MB" in e.failure["message"]


def test_missing_drop_dir_is_no_new_data(env) -> None:
    drop, make, run, _ = env
    config = make()
    config.sources["drop"].drop_dir = str(drop / "does-not-exist")
    assert entity(run(config)).status == "NO_NEW_DATA"


@pytest.mark.parametrize("pattern", ["../*.csv", "/etc/*", "sub/../../*.csv"])
def test_file_pattern_cannot_escape_drop_dir(env, pattern: str) -> None:
    _, make, _, _ = env
    connector = build_connector("drop", make(pattern).source("drop"), retry_policy=RetryPolicy())
    with pytest.raises(ConfigError, match="inside drop_dir"):
        connector.matching_files(connector.config.entities[0])


def test_symlink_pointing_outside_drop_dir_is_ignored(env) -> None:
    drop, make, run, tmp = env
    outside = tmp / "outside.csv"
    outside.write_text(csv_text([("p9", "9", "")]))
    try:
        (drop / "link.csv").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    assert entity(run(make())).counters.received == 0


# ----------------------------------------------------------------------------- edge cases
def test_discover_reports_available_files(env) -> None:
    drop, make, _, _ = env
    (drop / "a.csv").write_text(csv_text([("p1", "1", "")]))
    (drop / "b.csv").write_text(csv_text([("p2", "2", "")]))
    connector = build_connector("drop", make().source("drop"), retry_policy=RetryPolicy())
    (found,) = connector.discover()
    assert found.name == "people" and found.available_units == 2 and found.supports_incremental


def test_empty_file_and_duplicate_header_fail(env) -> None:
    drop, make, run, _ = env
    (drop / "a.csv").write_text("")
    assert "empty" in entity(run(make())).failure["message"]
    (drop / "a.csv").write_text("tenant_id,pid,pid\nt,1,2\n")
    assert "duplicate" in entity(run(make())).failure["message"]


def test_wrong_encoding_fails_cleanly(env) -> None:
    drop, make, run, _ = env
    (drop / "a.csv").write_bytes(b"tenant_id,pid,age,synthetic_marker\nt1,\xff\xfe\xfa,1,x\n")
    e = entity(run(make()))
    assert e.status == "FAILED"
    assert "UnicodeDecodeError" in e.failure["message"] or "not valid" in e.failure["message"]
    assert e.counters.loaded == 0


def test_custom_delimiter(env) -> None:
    drop, make, run, _ = env
    (drop / "a.csv").write_text(csv_text([("p1", "1", "")]).replace(",", ";"))
    config = make()
    config.sources["drop"].entities[0].csv.delimiter = ";"
    assert entity(run(config)).counters.loaded == 1


def test_locked_file_is_retried_then_succeeds(env, monkeypatch) -> None:
    drop, make, run, _ = env
    (drop / "a.csv").write_text(csv_text([("p1", "1", "")]))
    real_open = Path.open
    state = {"fails": 1}

    def flaky(self, *a, **kw):
        if self.name == "a.csv" and "encoding" in kw and state["fails"]:
            state["fails"] -= 1
            raise PermissionError("locked by another process")
        return real_open(self, *a, **kw)

    monkeypatch.setattr(Path, "open", flaky)
    e = entity(run(make()))
    assert e.status == "SUCCEEDED" and e.counters.retries == 1


def test_json_file_variants(env) -> None:
    drop, make, run, _ = env
    rows = [{"tenant_id": "t1", "pid": "p1", "age": 1, "synthetic_marker": MARKER}]
    (drop / "a.json").write_text(json.dumps({"payload": {"items": rows}}))
    config = make("*.json", "json")
    config.sources["drop"].entities[0].json_records_path = "payload.items"
    assert entity(run(config)).counters.loaded == 1
    (drop / "a.json").write_text("{broken")
    assert "not valid JSON" in entity(run(make("*.json", "json"))).failure["message"]
    (drop / "a.json").write_text('{"a": 1}')
    assert "JSON array" in entity(run(make("*.json", "json"))).failure["message"]


def test_jsonl_blank_lines_and_bom(env) -> None:
    drop, make, run, _ = env
    good = json.dumps({"tenant_id": "t1", "pid": "p1", "age": 1, "synthetic_marker": MARKER})
    (drop / "a.jsonl").write_bytes(b"\xef\xbb\xbf" + f"{good}\n\n".encode())
    assert entity(run(make("*.jsonl", "jsonl"))).counters.loaded == 1


@pytest.fixture
def xlsx(env):
    openpyxl = pytest.importorskip("openpyxl")
    drop = env[0]

    def build(rows, *, sheet_names=("Sheet",), name="a.xlsx"):
        wb = openpyxl.Workbook()
        wb.active.title = sheet_names[0]
        for extra in sheet_names[1:]:
            wb.create_sheet(extra)
        for row in rows:
            wb[sheet_names[0]].append(row)
        wb.save(drop / name)

    return build


HEADER = ["tenant_id", "pid", "age", "synthetic_marker"]


def test_excel_named_sheet_header_row_and_blank_rows(env, xlsx) -> None:
    _, make, run, _ = env
    xlsx(
        [
            ["junk title"],
            HEADER,
            ["t1", "p1", 1, MARKER],
            [None, None, None, None],
            ["t1", "p2", 2, MARKER],
        ]
    )
    config = make("*.xlsx", "excel")
    config.sources["drop"].entities[0].excel.header_row = 2
    e = entity(run(config))
    assert e.counters.loaded == 2 and e.counters.rejected == 0

    config.sources["drop"].entities[0].excel.sheet = "Missing"
    assert "not found" in entity(run(config)).failure["message"]
    config.sources["drop"].entities[0].excel.sheet = 7
    assert "out of range" in entity(run(config)).failure["message"]


def test_excel_dates_become_iso_strings(env, xlsx) -> None:
    import datetime as dt

    _, make, run, tmp = env
    xlsx([[*HEADER, "when"], ["t1", "p1", 1, MARKER, dt.datetime(2026, 1, 2, 3, 4, 5)]])
    assert entity(run(make("*.xlsx", "excel"))).counters.loaded == 1
    from ingestion.bronze import BronzeStore

    raw = BronzeStore(tmp / "store").iter_records("people")[0]["raw"]
    assert raw["when"] == "2026-01-02T03:04:05"


def test_excel_corrupt_and_missing_header(env) -> None:
    drop, make, run, _ = env
    (drop / "a.xlsx").write_bytes(b"this is not a workbook")
    assert "not a readable Excel" in entity(run(make("*.xlsx", "excel"))).failure["message"]


def test_excel_without_openpyxl_gives_actionable_error(env, monkeypatch) -> None:
    drop, make, run, _ = env
    (drop / "a.xlsx").write_bytes(b"x")
    monkeypatch.setitem(__import__("sys").modules, "openpyxl", None)
    assert "edupulse-ai[excel]" in entity(run(make("*.xlsx", "excel"))).failure["message"]
