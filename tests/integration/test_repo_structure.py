"""Repository hygiene: required layout, safe configuration templates, CI and compose wiring."""

import re
import shutil
import subprocess

import pytest
import yaml

pytestmark = pytest.mark.integration

REQUIRED_DIRS = ["ingestion", "dbt", "api", "web", "rag", "docs", "tests", "infra", "evidence"]
REQUIRED_FILES = [
    "README.md",
    "pyproject.toml",
    ".pre-commit-config.yaml",
    ".env.example",
    ".gitignore",
    "docker-compose.yml",
    ".github/workflows/ci.yml",
    "docs/architecture.md",
    "docs/evidence-manifest.md",
    "docs/CHANGELOG.md",
    "docs/data-dictionary.md",
]
SECRET_KEY = re.compile(r"(PASSWORD|SECRET|TOKEN|API_KEY|PRIVATE_KEY)", re.IGNORECASE)


@pytest.mark.parametrize("name", REQUIRED_DIRS)
def test_required_directories_exist(repo_root, name):
    assert (repo_root / name).is_dir()


@pytest.mark.parametrize("name", REQUIRED_FILES)
def test_required_files_exist(repo_root, name):
    assert (repo_root / name).is_file()


def _env_pairs(repo_root):
    for line in (repo_root / ".env.example").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            yield key, value


def test_env_example_contains_only_placeholder_secrets(repo_root):
    secret_pairs = [(k, v) for k, v in _env_pairs(repo_root) if SECRET_KEY.search(k)]
    assert secret_pairs, "expected at least the local-only placeholder passwords"
    for key, value in secret_pairs:
        assert value == "" or value.startswith("change-me"), f"{key} must be a placeholder"


def test_env_example_declares_synthetic_data_mode(repo_root):
    assert dict(_env_pairs(repo_root))["EDUPULSE_DATA_MODE"] == "synthetic"


def test_env_files_are_gitignored(repo_root):
    ignore = (repo_root / ".gitignore").read_text().splitlines()
    assert ".env" in ignore
    assert "!.env.example" in ignore
    assert "data/" in ignore


def test_no_real_env_file_present(repo_root):
    assert not (repo_root / ".env").exists()


def test_compose_defines_required_services(repo_root):
    compose = yaml.safe_load((repo_root / "docker-compose.yml").read_text())
    assert {"api", "web", "minio", "minio-init", "dbt", "postgres", "synth"} <= set(
        compose["services"]
    )
    assert compose["services"]["postgres"]["profiles"] == ["postgres"]
    for name in ("dbt", "synth"):
        assert compose["services"][name]["profiles"] == ["tools"]


def test_compose_contains_no_inline_secret_values(repo_root):
    """Credentials must be interpolated from the environment, never written literally."""
    compose = yaml.safe_load((repo_root / "docker-compose.yml").read_text())
    for name, service in compose["services"].items():
        for key, value in (service.get("environment") or {}).items():
            if SECRET_KEY.search(key):
                assert str(value).startswith("${"), f"{name}.{key} must come from the environment"


def test_compose_dockerfiles_exist(repo_root):
    compose = yaml.safe_load((repo_root / "docker-compose.yml").read_text())
    for name, service in compose["services"].items():
        build = service.get("build")
        if build:
            assert (repo_root / build["dockerfile"]).is_file(), name


def test_ci_workflow_runs_lint_tests_and_synthetic_smoke(repo_root):
    workflow = yaml.safe_load((repo_root / ".github/workflows/ci.yml").read_text())
    assert {"lint", "test", "synthetic-data-smoke", "compose-config"} <= set(workflow["jobs"])
    assert workflow["permissions"] == {"contents": "read"}


def test_dbt_project_declares_medallion_layers(repo_root):
    project = yaml.safe_load((repo_root / "dbt/dbt_project.yml").read_text())
    assert set(project["models"]["edupulse"]) == {"bronze", "silver", "gold"}
    for layer in ("bronze", "silver", "gold"):
        assert (repo_root / "dbt/models" / layer).is_dir()


def test_dbt_profile_has_no_literal_credentials(repo_root):
    profile = yaml.safe_load((repo_root / "dbt/profiles.yml.example").read_text())
    for target in profile["edupulse"]["outputs"].values():
        for key, value in target.items():
            if SECRET_KEY.search(key):
                assert "env_var(" in str(value)


def test_pyproject_pins_supported_python(repo_root):
    text = (repo_root / "pyproject.toml").read_text()
    assert 'requires-python = ">=3.11"' in text


def test_ruff_is_clean(repo_root):
    ruff = shutil.which("ruff")
    if ruff is None:
        pytest.skip("ruff not installed")
    result = subprocess.run(  # noqa: S603 - absolute path from shutil.which, fixed arguments
        [ruff, "check", "."], cwd=repo_root, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
