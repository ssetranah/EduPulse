"""`edupulse ingest` / `verify-bronze` as a real subprocess (exit codes, output, files)."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from tests.ing_support import REPO_ROOT, mock_source

pytestmark = pytest.mark.integration


def edupulse(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT), "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "ingestion", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


@pytest.fixture
def cfg(write_ingest_config):
    return write_ingest_config(
        {"mock_sis": mock_source("mock_sis"), "mock_lms": mock_source("mock_lms")}
    )


def test_ingest_all_success_prints_summary_and_writes_reports(cfg, tmp_path) -> None:
    out = edupulse(
        "ingest", "--source", "all", "--config", str(cfg), "--run-id", "cli1", cwd=tmp_path
    )
    assert out.returncode == 0, out.stderr
    assert "RUN TOTAL" in out.stdout and "mock_sis / students" in out.stdout
    report = json.loads((tmp_path / "store" / "runs" / "cli1" / "run_report.json").read_text())
    assert report["status"] == "SUCCEEDED" and report["records"]["loaded"] > 0
    assert "[INFO" in out.stderr  # console log lines


def test_single_and_comma_separated_sources(cfg, tmp_path) -> None:
    one = edupulse("ingest", "--source", "mock_lms", "--config", str(cfg), "--quiet", cwd=tmp_path)
    assert one.returncode == 0 and "mock_sis" not in one.stdout


def test_unknown_source_is_a_config_error_exit_2(cfg, tmp_path) -> None:
    out = edupulse("ingest", "--source", "nope", "--config", str(cfg), cwd=tmp_path)
    assert out.returncode == 2 and "unknown source 'nope'" in out.stderr
    assert "Traceback" not in out.stderr


def test_missing_config_file_exit_2(tmp_path) -> None:
    out = edupulse("ingest", "--source", "all", "--config", "missing.yaml", cwd=tmp_path)
    assert out.returncode == 2 and "cannot read config" in out.stderr


def test_invalid_config_reports_locations_without_values(write_ingest_config, tmp_path) -> None:
    bad = mock_source("mock_sis")
    bad["mock"]["students_per_school"] = "hunter2"
    out = edupulse(
        "ingest", "--source", "all", "--config", str(write_ingest_config({"s": bad})), cwd=tmp_path
    )
    assert (
        out.returncode == 2 and "students_per_school" in out.stderr and "hunter2" not in out.stderr
    )


def test_dry_run_flag(cfg, tmp_path) -> None:
    out = edupulse(
        "ingest", "--source", "all", "--config", str(cfg), "--dry-run", "--quiet", cwd=tmp_path
    )
    assert out.returncode == 0 and "dry_run=True" in out.stdout
    assert not (tmp_path / "store" / "bronze").exists()


def test_override_changes_config_value(cfg, tmp_path) -> None:
    out = edupulse(
        "ingest",
        "--source",
        "mock_sis",
        "--config",
        str(cfg),
        "--quiet",
        "--override",
        "sources.mock_sis.mock.students_per_school=5",
        cwd=tmp_path,
    )
    assert out.returncode == 0
    # 2 tenants x 2 schools x 5 students
    assert " 20 " in next(x for x in out.stdout.splitlines() if "mock_sis / students" in x)


def test_failed_run_exit_1(write_ingest_config, tmp_path) -> None:
    broken = mock_source("mock_sis", faults={"fail_first_n_calls": 999})
    broken["retry"] = {"max_attempts": 1, "base_delay_seconds": 0}
    out = edupulse(
        "ingest",
        "--source",
        "all",
        "--config",
        str(write_ingest_config({"s": broken})),
        "--quiet",
        cwd=tmp_path,
    )
    assert out.returncode == 1 and "FAILED" in out.stdout


def test_strict_turns_rejects_into_exit_3(write_ingest_config, tmp_path) -> None:
    src = mock_source("mock_sis", faults={"bad_row_rate": 0.03}, students_per_school=60)
    path = write_ingest_config({"s": src})
    lenient = edupulse("ingest", "--source", "all", "--config", str(path), "--quiet", cwd=tmp_path)
    assert lenient.returncode == 0 and "COMPLETED_WITH_REJECTS" in lenient.stdout
    strict = edupulse(
        "ingest",
        "--source",
        "all",
        "--config",
        str(path),
        "--quiet",
        "--strict",
        "--output-dir",
        str(tmp_path / "other"),
        cwd=tmp_path,
    )
    assert strict.returncode == 3


def test_verify_bronze_ok_then_detects_tampering(cfg, tmp_path) -> None:
    edupulse("ingest", "--source", "all", "--config", str(cfg), "--quiet", cwd=tmp_path)
    ok = edupulse("verify-bronze", "--root", str(tmp_path / "store"), cwd=tmp_path)
    assert ok.returncode == 0 and "verified" in ok.stdout
    part = next((tmp_path / "store" / "bronze").rglob("part-00000.jsonl"))
    os.chmod(part, stat.S_IWRITE | stat.S_IREAD)
    part.write_bytes(part.read_bytes()[:-5] + b"XXXX\n")
    bad = edupulse("verify-bronze", "--root", str(tmp_path / "store"), cwd=tmp_path)
    assert bad.returncode == 1 and "checksum mismatch" in bad.stdout


def test_version_and_help(tmp_path) -> None:
    assert "edupulse 0.2.0" in edupulse("--version", cwd=tmp_path).stdout
    assert "ingest" in edupulse("--help", cwd=tmp_path).stdout


def test_shipped_config_runs_end_to_end(tmp_path) -> None:
    """The documented quick-start: generate synthetic files, then ingest all enabled sources."""
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    gen = subprocess.run(
        [
            sys.executable,
            "-m",
            "ingestion.synthetic",
            "generate",
            "--out",
            "data/synthetic",
            "--students-per-school",
            "20",
            "--schools-per-tenant",
            "2",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert gen.returncode == 0, gen.stderr
    config = REPO_ROOT / "ingestion" / "configs" / "ingestion.yaml"
    out = edupulse("ingest", "--source", "all", "--config", str(config), "--quiet", cwd=tmp_path)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "rest_sis_api" not in out.stdout  # disabled by default
    for name in (
        "file_drop_synthetic",
        "mock_sis",
        "mock_lms",
        "mock_assessment",
        "mock_attendance",
    ):
        assert name in out.stdout
