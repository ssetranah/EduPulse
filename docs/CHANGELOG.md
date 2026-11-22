# Changelog

All notable changes to EduPulse AI. Format: [Keep a Changelog](https://keepachangelog.com/);
versions follow [Semantic Versioning](https://semver.org/). Pre-1.0: the MVP is under construction.

## [0.1.0] — 2026-11-21 — Phase 1: Repository and evidence framework

### Added
- Monorepo layout: `ingestion/`, `dbt/`, `api/`, `web/`, `rag/`, `docs/`, `tests/`, `infra/`,
  `evidence/`.
- Python packaging (`pyproject.toml`, setuptools, Python ≥ 3.11), ruff lint/format config,
  pytest config with `unit` / `integration` / `data_quality` markers, pre-commit hooks, `Makefile`.
- GitHub Actions CI: lint, tests on Python 3.11–3.13, synthetic-data smoke test, compose validation.
- Docker Compose stack (api, dashboard, MinIO + bucket init, dbt runner, synthetic generator,
  optional PostgreSQL) with Dockerfiles and `.env.example` (placeholders only).
- Synthetic-data generator and validator (`python -m ingestion.synthetic`): 8 deterministic,
  multi-tenant source extracts (SIS, assessment, attendance, grading, LMS, intervention), every
  row marked `SYNTHETIC_TEST_DATA`, checksummed manifest, PII-pattern scan, optional defect
  injection for data-quality testing.
- Health/version API stub (`api/health.py`) and static dashboard scaffold with a persistent
  synthetic-data banner.
- dbt project skeleton with Bronze/Silver/Gold layer conventions and a DuckDB/Databricks profile
  template.
- Documentation: README, architecture (Mermaid), data dictionary, deployment guide, ADRs
  (canonical model, tenancy, local runtime), evidence manifest.
- Evidence collector (`evidence/collect_evidence.py`) producing a machine-readable Phase 1 record.

### Not done / known limits
- No business logic, ingestion adapters, dbt models, authentication, RBAC or audit logging.
- Compose stack and dbt project are written but were **not run** (no Docker daemon / dbt CLI in the
  build environment). CI has not run on GitHub.
- No FERPA certification or legal-compliance claim is made or implied.
