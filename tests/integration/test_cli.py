"""End-to-end checks of the generator CLI as a real subprocess."""

import json
import subprocess
import sys

import pytest

pytestmark = pytest.mark.integration


def run_cli(repo_root, *args):
    return subprocess.run(  # noqa: S603 - fixed interpreter and module, test-controlled args
        [sys.executable, "-m", "ingestion.synthetic", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )


def test_generate_then_validate(repo_root, tmp_path):
    out = tmp_path / "ds"
    gen = run_cli(
        repo_root, "generate", "--out", str(out), "--tenants", "1", "--students-per-school", "10"
    )
    assert gen.returncode == 0, gen.stderr
    assert "SYNTHETIC_TEST_DATA" in gen.stdout

    val = run_cli(repo_root, "validate", str(out))
    assert val.returncode == 0, val.stderr
    assert val.stdout.startswith("OK:")


def test_validate_fails_on_missing_dataset(repo_root, tmp_path):
    result = run_cli(repo_root, "validate", str(tmp_path / "nope"))
    assert result.returncode == 1
    assert "INVALID" in result.stderr


def test_config_file_with_flag_override(repo_root, tmp_path):
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"tenants": 1, "students_per_school": 5, "seed": 1}))
    out = tmp_path / "ds"
    result = run_cli(
        repo_root, "generate", "--out", str(out), "--config", str(cfg), "--schools-per-tenant", "1"
    )
    assert result.returncode == 0, result.stderr
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["config"]["tenants"] == 1
    assert manifest["config"]["schools_per_tenant"] == 1
    assert manifest["config"]["students_per_school"] == 5


def test_invalid_settings_exit_with_code_2(repo_root, tmp_path):
    result = run_cli(repo_root, "generate", "--out", str(tmp_path / "x"), "--tenants", "0")
    assert result.returncode == 2
    assert "tenants must be" in result.stderr
    assert not (tmp_path / "x").exists()


def test_unknown_config_key_is_refused(repo_root, tmp_path):
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"use_real_data": True}))
    result = run_cli(repo_root, "generate", "--out", str(tmp_path / "x"), "--config", str(cfg))
    assert result.returncode == 2
    assert "unknown config keys" in result.stderr
