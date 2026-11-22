# Changelog

All notable changes to EduPulse AI. Format: [Keep a Changelog](https://keepachangelog.com/);
versions follow [Semantic Versioning](https://semver.org/). Pre-1.0: the MVP is under construction.

## [0.2.0] — 2026-11-21 — Phase 2: Multi-source ingestion framework

### Added
- **`SourceConnector` interface** (`ingestion/connectors/base.py`): `discover`, `extract`,
  `validate_schema`, `plan_incremental` + `advance_watermark`, `handle_error`; source-system metadata.
- **Six connectors:** CSV/Excel/JSON Lines/JSON file drop; JSON REST API (page + cursor pagination,
  bearer/API-key auth via environment-variable name, https rule, no redirects, size/page caps);
  mock SIS, mock LMS, mock assessment platform, mock attendance/intervention (vendor-native shapes,
  deterministic `as_of` clock, fault injection).
- **YAML field mapping** (`ingestion/configs/mappings/`, 10 files): renaming, type coercion, null
  handling, `value_map`, constraints, sensitivity labels; validated with pydantic (unknown keys rejected).
- **`IngestionRunner`:** row-level validation with value-free error codes, duplicate detection
  (in-batch identical, in-batch same-key, already-loaded), incremental loading (inclusive watermark,
  file ledger, hash idempotency), quality gate, atomic per-entity commit, state recovery from Bronze.
- **Bronze store:** immutable, write-once, tenant-partitioned batches with source, ingestion timestamp,
  batch id, source-record hash, schema version, SHA-256 manifests; `verify_bronze` tamper detection.
- **Retry/backoff** (exponential, full jitter, `Retry-After`), **dead-letter store**, structured JSON
  logging, **JSON run report**, validation report, OpenLineage-inspired lineage events.
- **CLI:** `edupulse ingest --source <name|all> --config <path>` (`--dry-run`, `--strict`,
  `--override`, `--run-id`, `--output-dir`), `edupulse verify-bronze`; exit codes 0/1/2/3.
- Loopback mock REST server (`ingestion/synthetic/rest_server.py`) and vendor-native views of the
  synthetic data (`ingestion/synthetic/native.py`).
- **Tests:** +224 (95 → 319): retry, mapping/coercion, config, storage, file drops, REST, runner and
  mocks, recovery, CLI (subprocess and in-process), Bronze data quality, repo checks.
- **Docs:** `docs/ingestion.md`, `docs/data-lineage.md`; README, architecture, manifest updated.
- **Evidence** (`evidence/phase-2/`): ingestion logs, run reports, validation reports, CLI output,
  coverage report, sample run by source — produced by `evidence/phase2.py`.
- CI: `coverage` and `ingestion-smoke` jobs. Makefile: `ingest`, `verify-bronze`, `coverage`,
  `evidence-phase2`.

### Changed
- Runtime dependencies: `pyyaml`, `pydantic>=2.6` (Phase 1 was standard-library only). New extras:
  `excel` (`openpyxl`); `dev` gains `pytest-cov`, `openpyxl`. New console script `edupulse`.

### Not done / known limits
- Synthetic data and mock systems only; no real source system, TLS endpoint or large volume was tested.
- Coverage: committed report uses a stdlib line-coverage tool (coverage.py could not be installed):
  line coverage only, subprocess code unmeasured. CI uses `pytest --cov`.
- No dead-letter replay command, no multi-runner locking, no scheduling, no encryption at rest,
  RBAC or audit logging; Silver/Gold/dbt models still absent. CI has not run on GitHub; Python 3.11
  and 3.12 were not exercised locally (tests ran on 3.13).
- No FERPA certification or legal-compliance claim is made or implied.

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
