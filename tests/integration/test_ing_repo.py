"""Phase 2 repository-level checks: deliverables exist, configs are safe, CI covers ingestion."""

from __future__ import annotations

import re
import tomllib

import pytest
import yaml

pytestmark = pytest.mark.integration

DELIVERABLES = [
    "docs/ingestion.md",
    "docs/data-lineage.md",
    "ingestion/configs/ingestion.yaml",
    "ingestion/cli.py",
    "ingestion/runner.py",
    "ingestion/connectors/base.py",
    "evidence/line_coverage.py",
    "evidence/phase2.py",
]


@pytest.mark.parametrize("path", DELIVERABLES)
def test_phase2_deliverables_exist(repo_root, path) -> None:
    assert (repo_root / path).is_file()


def test_pyproject_declares_cli_and_runtime_dependencies(repo_root) -> None:
    project = tomllib.loads((repo_root / "pyproject.toml").read_text())["project"]
    assert project["scripts"]["edupulse"] == "ingestion.cli:main"
    deps = " ".join(project["dependencies"]).lower()
    assert "pyyaml" in deps and "pydantic" in deps
    assert any("openpyxl" in d for d in project["optional-dependencies"]["excel"])
    assert any("pytest-cov" in d for d in project["optional-dependencies"]["dev"])


def test_shipped_configs_contain_no_secrets(repo_root) -> None:
    secret_value = re.compile(r"(token|password|secret|api_key)\s*:\s*['\"]?[A-Za-z0-9]{12,}", re.I)
    for path in (repo_root / "ingestion" / "configs").rglob("*.yaml"):
        assert not secret_value.search(path.read_text()), path.name


def test_shipped_config_enables_only_safe_sources_by_default(repo_root) -> None:
    config = yaml.safe_load((repo_root / "ingestion/configs/ingestion.yaml").read_text())
    for name, source in config["sources"].items():
        assert source.get("data_origin", "synthetic") == "synthetic", name
    assert config["sources"]["rest_sis_api"]["enabled"] is False  # needs a running server


def test_ci_runs_coverage_and_an_ingestion_smoke_test(repo_root) -> None:
    jobs = yaml.safe_load((repo_root / ".github/workflows/ci.yml").read_text())["jobs"]
    assert {"coverage", "ingestion-smoke"} <= set(jobs)
    smoke = " ".join(step.get("run", "") for step in jobs["ingestion-smoke"]["steps"])
    assert "edupulse ingest" in smoke and "verify-bronze" in smoke


def test_every_mapping_file_is_referenced_by_a_config(repo_root) -> None:
    config_text = (repo_root / "ingestion/configs/ingestion.yaml").read_text()
    for mapping in (repo_root / "ingestion/configs/mappings").glob("*.yaml"):
        assert mapping.name in config_text, f"{mapping.name} is not used by the shipped config"


def test_docs_do_not_claim_certification(repo_root) -> None:
    banned = re.compile(r"(is|are|be|been)\s+FERPA[- ](certified|compliant)", re.I)
    for doc in ("docs/ingestion.md", "docs/data-lineage.md"):
        assert not banned.search((repo_root / doc).read_text()), doc
