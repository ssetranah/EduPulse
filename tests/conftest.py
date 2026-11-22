"""Shared fixtures. Datasets are generated once per session into pytest temp dirs."""

from __future__ import annotations

from pathlib import Path

import pytest

from ingestion.synthetic import GeneratedData, GeneratorConfig, generate, write_dataset

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def clean_config() -> GeneratorConfig:
    return GeneratorConfig(seed=7, tenants=2, schools_per_tenant=3, students_per_school=40)


@pytest.fixture(scope="session")
def clean_data(clean_config: GeneratorConfig) -> GeneratedData:
    return generate(clean_config)


@pytest.fixture(scope="session")
def clean_dataset_dir(tmp_path_factory: pytest.TempPathFactory, clean_data: GeneratedData) -> Path:
    out = tmp_path_factory.mktemp("synthetic_clean")
    write_dataset(clean_data, out)
    return out


@pytest.fixture(scope="session")
def defective_data() -> GeneratedData:
    config = GeneratorConfig(seed=7, tenants=1, students_per_school=80, defect_rate=0.1)
    return generate(config)
