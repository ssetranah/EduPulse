"""CLI `main()` exercised in-process (coverage tools see it; subprocess tests cover the shell)."""

from __future__ import annotations

import pytest

from ingestion.cli import main
from tests.ing_support import mock_source

pytestmark = pytest.mark.unit


@pytest.fixture
def cfg(write_ingest_config):
    return write_ingest_config({"s": mock_source("mock_sis")})


def test_ingest_success_and_verify(cfg, tmp_path, capsys) -> None:
    assert main(["ingest", "--source", "all", "--config", str(cfg), "--quiet"]) == 0
    out = capsys.readouterr().out
    assert "RUN TOTAL" in out and "run report:" in out
    assert main(["verify-bronze", "--root", str(tmp_path / "store")]) == 0
    assert "verified" in capsys.readouterr().out


def test_config_error_exit_code(tmp_path, capsys) -> None:
    assert main(["ingest", "--source", "all", "--config", str(tmp_path / "nope.yaml")]) == 2
    assert "error:" in capsys.readouterr().err


def test_failed_and_strict_exit_codes(write_ingest_config, capsys) -> None:
    broken = mock_source("mock_sis", faults={"fail_first_n_calls": 999})
    broken["retry"] = {"max_attempts": 1, "base_delay_seconds": 0}
    assert (
        main(
            [
                "ingest",
                "--source",
                "all",
                "--config",
                str(write_ingest_config({"s": broken})),
                "--quiet",
            ]
        )
        == 1
    )
    rejects = mock_source("mock_sis", faults={"bad_row_rate": 0.03}, students_per_school=60)
    path = write_ingest_config({"s": rejects})
    assert main(["ingest", "--source", "all", "--config", str(path), "--quiet", "--strict"]) == 3
    capsys.readouterr()


def test_verify_reports_problems(cfg, tmp_path, capsys) -> None:
    import os
    import stat

    main(["ingest", "--source", "all", "--config", str(cfg), "--quiet"])
    part = next((tmp_path / "store" / "bronze").rglob("part-00000.jsonl"))
    os.chmod(part, stat.S_IWRITE | stat.S_IREAD)
    part.write_bytes(b"{}\n")
    capsys.readouterr()
    assert main(["verify-bronze", "--root", str(tmp_path / "store")]) == 1
    assert "PROBLEM" in capsys.readouterr().out
