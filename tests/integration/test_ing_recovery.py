"""State is a cache of Bronze: crash windows and state loss must never cause duplicate loads."""

from __future__ import annotations

import hashlib
from collections import Counter

import pytest

from ingestion.bronze import BronzeStore
from ingestion.config import load_config
from ingestion.runner import IngestionRunner
from ingestion.state import StateStore
from tests.ing_support import mock_source

pytestmark = pytest.mark.integration


@pytest.fixture
def make_runner(write_ingest_config, tmp_path):
    path = write_ingest_config({"s": mock_source("mock_sis")})

    def _make(**kw):
        return IngestionRunner(
            load_config(path),
            output_dir=tmp_path / "store",
            quiet=True,
            sleep=lambda _s: None,
            **kw,
        )

    return _make


def hashes(tmp_path):
    return Counter(r["_source_record_hash"] for r in BronzeStore(tmp_path / "store").iter_records())


def test_crash_between_bronze_commit_and_state_commit_is_recovered(
    make_runner, tmp_path, monkeypatch
) -> None:
    original = StateStore.commit_entity

    def exploding(self, source, entity, **kw):
        if entity == "students":
            raise OSError("simulated crash while writing state")
        return original(self, source, entity, **kw)

    monkeypatch.setattr(StateStore, "commit_entity", exploding)
    first = make_runner(run_id="r1").run()
    failed = next(e for e in first.entity_results() if e.entity == "students")
    assert failed.status == "FAILED" and "recovered from Bronze" in failed.failure["message"]
    assert failed.counters.loaded > 0  # Bronze really holds the rows
    before = hashes(tmp_path)
    assert before and max(before.values()) == 1

    monkeypatch.setattr(StateStore, "commit_entity", original)
    second = make_runner(run_id="r2").run()
    students = next(e for e in second.entity_results() if e.entity == "students")
    assert students.counters.loaded == 0 and students.status == "NO_NEW_DATA"
    assert any("state recovered" in w for w in students.warnings)
    after = hashes(tmp_path)
    assert after == before  # nothing duplicated


def test_state_loss_is_rebuilt_from_bronze(make_runner, tmp_path) -> None:
    make_runner(run_id="r1").run()
    before = hashes(tmp_path)
    (tmp_path / "store" / "_state" / "ingest_state.sqlite").unlink()
    again = make_runner(run_id="r2").run()
    assert again.totals.loaded == 0 and again.status == "NO_NEW_DATA"
    assert hashes(tmp_path) == before
    # and the rebuilt watermark lets the next run stay incremental
    state = StateStore(tmp_path / "store" / "_state" / "ingest_state.sqlite")
    assert state.watermark("s", "students") is not None
    state.close()


def test_dry_run_never_modifies_the_state_file(make_runner, tmp_path) -> None:
    make_runner(run_id="r1").run()
    state_file = tmp_path / "store" / "_state" / "ingest_state.sqlite"
    before = hashlib.sha256(state_file.read_bytes()).hexdigest()
    make_runner(run_id="r2", dry_run=True).run()
    assert hashlib.sha256(state_file.read_bytes()).hexdigest() == before


def test_dry_run_after_state_loss_reports_nothing_new_without_writing(
    make_runner, tmp_path
) -> None:
    make_runner(run_id="r1").run()
    (tmp_path / "store" / "_state" / "ingest_state.sqlite").unlink()
    dry = make_runner(run_id="r2", dry_run=True).run()
    assert dry.totals.loaded == 0
    assert not (tmp_path / "store" / "_state" / "ingest_state.sqlite").exists()
