"""State store, Bronze immutability/tamper detection, dead-letter store."""

from __future__ import annotations

import json
import os
import stat

import pytest

from ingestion.bronze import BatchContext, BronzeStore, NullBatchWriter, verify_bronze
from ingestion.deadletter import DeadLetterWriter
from ingestion.errors import ImmutabilityError
from ingestion.models import RowError
from ingestion.state import StateStore

pytestmark = pytest.mark.unit


def ctx(batch_id: str = "run-1-students", **kw) -> BatchContext:
    base = {
        "batch_id": batch_id,
        "run_id": "run-1",
        "source_name": "src",
        "source_system": "SIS",
        "source_entity": "students",
        "bronze_table": "sis_students",
        "schema_version": "1.0.0",
        "ingestion_ts": "2026-01-01T00:00:00Z",
        "data_origin": "SYNTHETIC_TEST_DATA",
        "field_sensitivity": {"first_name": "restricted"},
    }
    return BatchContext(**{**base, **kw})


def record(tenant: str, n: int) -> dict:
    return {
        "tenant_id": tenant,
        "_source_record_hash": f"h{n}",
        "raw": {"n": n},
        "canonical": {"n": n},
    }


# ----------------------------------------------------------------------------- state
def test_state_roundtrip_and_atomic_commit(tmp_path) -> None:
    store = StateStore(tmp_path / "s" / "state.sqlite")
    assert store.watermark("s", "e") is None
    store.commit_entity(
        "s",
        "e",
        batch_id="b1",
        timestamp="t",
        hashes=["h1", "h2"],
        watermark="2026-01-02T00:00:00Z",
        units=[("f.csv", "abc")],
        fingerprints={"f.csv": "fp"},
    )
    assert store.watermark("s", "e") == "2026-01-02T00:00:00Z"
    assert store.has_hash("s", "e", "h1") and not store.has_hash("s", "e", "nope")
    assert store.unit_processed("s", "e", "f.csv", "abc")
    assert not store.unit_processed("s", "e", "f.csv", "different-checksum")
    assert store.last_fingerprint("s", "e", "f.csv") == "fp"
    store.close()
    reopened = StateStore(tmp_path / "s" / "state.sqlite")  # durable
    assert reopened.has_hash("s", "e", "h2")
    reopened.close()


def test_state_is_scoped_per_source_and_entity() -> None:
    store = StateStore(":memory:")
    store.commit_entity(
        "a",
        "e",
        batch_id="b",
        timestamp="t",
        hashes=["h"],
        watermark="w",
        units=[],
        fingerprints={},
    )
    assert not store.has_hash("b", "e", "h") and not store.has_hash("a", "other", "h")
    assert store.watermark("b", "e") is None


# ----------------------------------------------------------------------------- bronze
def test_bronze_commit_is_partitioned_by_tenant_and_readable(tmp_path) -> None:
    store = BronzeStore(tmp_path)
    writer = store.new_batch(ctx())
    for i in range(3):
        writer.write(record("tenant-a", i))
    writer.write(record("tenant-b", 9))
    commit = writer.commit()
    assert commit.rows_by_tenant == {"tenant-a": 3, "tenant-b": 1}
    dirs = {p.relative_to(store.root).parts[0] for p in commit.batch_dirs}
    assert dirs == {"tenant_id=tenant-a", "tenant_id=tenant-b"}
    rows = store.iter_records("sis_students")
    assert len(rows) == 4
    # tenant isolation: each partition holds only its own tenant's rows
    for batch_dir in commit.batch_dirs:
        tenants = {r["tenant_id"] for r in store.read_batch(batch_dir)}
        assert len(tenants) == 1
    assert verify_bronze(tmp_path) == []


def test_bronze_is_immutable_no_overwrite(tmp_path) -> None:
    store = BronzeStore(tmp_path)
    first = store.new_batch(ctx())
    first.write(record("t", 1))
    first.commit()
    again = store.new_batch(ctx())  # same batch id
    again.write(record("t", 2))
    with pytest.raises(ImmutabilityError):
        again.commit()
    assert len(store.iter_records()) == 1  # original untouched
    assert not (store.root / "_staging").exists()


def test_committed_files_are_read_only(tmp_path) -> None:
    store = BronzeStore(tmp_path)
    w = store.new_batch(ctx())
    w.write(record("t", 1))
    d = w.commit().batch_dirs[0]
    mode = (d / "part-00000.jsonl").stat().st_mode
    assert not mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)


def test_abort_leaves_nothing_visible(tmp_path) -> None:
    store = BronzeStore(tmp_path)
    w = store.new_batch(ctx())
    w.write(record("t", 1))
    w.abort()
    assert store.batch_dirs() == []
    assert not (store.root / "_staging").exists()
    with pytest.raises(ImmutabilityError):
        w.write(record("t", 2))


def test_verify_detects_tampering_missing_and_extra_files(tmp_path) -> None:
    store = BronzeStore(tmp_path)
    w = store.new_batch(ctx())
    for i in range(3):
        w.write(record("t", i))
    d = w.commit().batch_dirs[0]
    assert verify_bronze(tmp_path) == []

    part = d / "part-00000.jsonl"
    os.chmod(part, stat.S_IWRITE | stat.S_IREAD)
    original = part.read_bytes()
    part.write_bytes(original.replace(b'"n":1', b'"n":7'))
    assert any("checksum mismatch" in p for p in verify_bronze(tmp_path))
    part.write_bytes(original + b"{}\n")
    problems = verify_bronze(tmp_path)
    assert any("row count mismatch" in p for p in problems)
    part.write_bytes(original)
    (d / "part-00009.jsonl").write_bytes(b"x\n")
    assert any("unexpected file" in p for p in verify_bronze(tmp_path))
    (d / "part-00009.jsonl").unlink()
    part.unlink()
    assert any("missing" in p for p in verify_bronze(tmp_path))


def test_manifest_records_schema_version_source_and_sensitivity(tmp_path) -> None:
    store = BronzeStore(tmp_path)
    w = store.new_batch(ctx())
    w.write(record("t", 1))
    d = w.commit().batch_dirs[0]
    manifest = json.loads((d / "_MANIFEST.json").read_text())
    assert manifest["schema_version"] == "1.0.0"
    assert manifest["source_name"] == "src"
    assert manifest["field_sensitivity"] == {"first_name": "restricted"}
    assert manifest["parts"][0]["rows"] == 1


def test_unsafe_tenant_values_cannot_escape_the_partition_root(tmp_path) -> None:
    store = BronzeStore(tmp_path)
    w = store.new_batch(ctx(source_name="a/../b"))
    w.write(record("../../evil", 1))
    commit = w.commit()
    for d in commit.batch_dirs:
        assert d.resolve().is_relative_to((tmp_path / "bronze").resolve())


def test_rows_split_into_multiple_parts(tmp_path) -> None:
    store = BronzeStore(tmp_path)
    w = store.new_batch(ctx())
    w._max_rows = 2
    for i in range(5):
        w.write(record("t", i))
    d = w.commit().batch_dirs[0]
    assert len(list(d.glob("part-*.jsonl"))) == 3
    assert len(store.read_batch(d)) == 5 and verify_bronze(tmp_path) == []


def test_null_writer_persists_nothing(tmp_path) -> None:
    w = NullBatchWriter()
    w.write(record("t", 1))
    assert w.commit().rows == 0 and not list(tmp_path.iterdir())


# ----------------------------------------------------------------------------- dead letter
def test_dead_letter_keeps_raw_and_value_free_errors(tmp_path) -> None:
    dl = DeadLetterWriter(tmp_path, ctx())
    dl.write(
        raw={"StudentUniqueId": "SYN-1", "BirthDate": "garbage"},
        location="f.csv#3",
        record_hash="abc",
        errors=[RowError("TYPE_COERCION_FAILED", "birth_date", "not a date")],
    )
    d = dl.close(batch_status="SUCCEEDED_WITH_REJECTS")
    line = json.loads((d / "rejected.jsonl").read_text().splitlines()[0])
    assert line["raw"]["BirthDate"] == "garbage"  # replayable
    assert line["row_errors"] == [
        {"code": "TYPE_COERCION_FAILED", "field": "birth_date", "message": "not a date"}
    ]
    assert "garbage" not in json.dumps(line["row_errors"])
    manifest = json.loads((d / "_MANIFEST.json").read_text())
    assert manifest["rows"] == 1 and manifest["errors_by_code"] == {"TYPE_COERCION_FAILED": 1}
    assert "restricted" in manifest["sensitivity"]


def test_dead_letter_with_no_rejects_creates_nothing(tmp_path) -> None:
    dl = DeadLetterWriter(tmp_path, ctx())
    assert dl.close(batch_status="SUCCEEDED") is None
    assert not (tmp_path / "dead_letter").exists()
