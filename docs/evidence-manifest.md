# Evidence manifest

A living index mapping every feature to the files, tests, documentation, metrics, screenshots and
deployment evidence that substantiate it. **Rule:** a cell says what was actually produced or run.
Anything not run is written as *not run* with the reason — never implied.

Machine-readable source for the numbers below: [`evidence/phase-1/evidence.json`](../evidence/phase-1/evidence.json)
(regenerate with `make evidence`). Last collected: Phase 1, 2026-11-21.

> **Scope note.** EduPulse is under construction as an MVP. As of Phase 1 **nothing is deployed to
> any school or district and no real student data has been processed.** No FERPA certification or
> legal-compliance claim is made; controls are described as *FERPA-aligned technical practices*.

## Feature → evidence table

| # | Feature | Source files | Tests | Documentation | Metrics | Screenshots / artifacts | Deployment evidence | Petition relevance |
|---|---------|--------------|-------|---------------|---------|-------------------------|---------------------|--------------------|
| 1 | Monorepo scaffold & packaging | `pyproject.toml`, `Makefile`, `.editorconfig`, dirs `ingestion/ dbt/ api/ web/ rag/ docs/ tests/ infra/ evidence/` | `tests/integration/test_repo_structure.py` (required dirs/files, Python ≥3.11) | `README.md`, per-directory `README.md` | 9 required dirs + 11 required files asserted | — | None (repository only) | Shows a modular, reusable architecture for multi-school data infrastructure |
| 2 | Quality gates (lint, format, pre-commit, CI) | `.pre-commit-config.yaml`, `[tool.ruff]`/`[tool.pytest]` in `pyproject.toml`, `.github/workflows/ci.yml` | `test_ruff_is_clean`, `test_ci_workflow_runs_lint_tests_and_synthetic_smoke` | `README.md` (test commands), `docs/CHANGELOG.md` | ruff 0.16.9: check + format pass; CI defines 4 jobs (lint, test ×3 Python versions, synthetic smoke, compose config) | `evidence/phase-1/junit-all.xml` | **CI not run** — repo not pushed to GitHub | Evidence of engineering discipline and reproducibility |
| 3 | Synthetic-data generator & validator | `ingestion/synthetic/{spec,config,generator,writers,validate,cli}.py` | `tests/unit/test_generator.py`, `test_config.py`, `test_writers_validate.py`; `tests/integration/test_cli.py`; `tests/data_quality/test_synthetic_quality.py` | `docs/data-dictionary.md` | Default dataset: **22,372 rows** / 8 extracts / 2 tenants / 6 schools / 360 students; output **byte-identical across runs**; validator problems: 0 | `evidence/phase-1/synthetic-manifest.json` (checksums), `evidence/phase-1/sample/` (first 5 rows per extract) | None (local CLI only) | Demonstrates privacy-preserving, reproducible test data for building and demonstrating the platform without student PII |
| 4 | Synthetic-only / privacy guardrails | `ingestion/synthetic/{constants,validate}.py`, `.gitignore`, `.pre-commit-config.yaml`, `.env.example` | `test_every_row_is_marked_synthetic`, `test_identifiers_are_obviously_synthetic`, `test_pii_like_strings_are_flagged` (SSN/phone/e-mail), `test_tampering_is_detected_by_checksum`, `test_env_example_contains_only_placeholder_secrets`, `test_env_files_are_gitignored`, `test_compose_contains_no_inline_secret_values` | `docs/architecture.md` §3, `docs/data-dictionary.md` | 100% of rows carry `SYNTHETIC_TEST_DATA`; PII-pattern scan: 0 hits | `evidence/phase-1/synthetic-manifest.json` | None | Shows technical controls consistent with FERPA-aligned practice (PII minimization, secret hygiene); **not** a compliance claim |
| 5 | Local dev stack (Compose: API, web, MinIO, dbt runner, synth, optional Postgres) | `docker-compose.yml`, `infra/docker/*`, `.env.example` | `test_compose_defines_required_services`, `test_compose_dockerfiles_exist`; CI job `compose-config` | `docs/deployment.md`, `docs/architecture.md` §5 | `docker compose config` valid for default and `postgres`+`tools` profiles | `evidence/phase-1/diagrams/architecture-2.png` | **Images not built; stack not started** — no Docker daemon in the build environment | Reproducible local environment for the platform |
| 6 | Health/version API stub | `api/__init__.py`, `api/health.py` | `tests/unit/test_health.py`; `tests/integration/test_health_server.py` (real HTTP server on ephemeral port) | `api/README.md` | 6 tests; responses carry `data_mode: synthetic` and security headers | `screenshots/dashboard-*.png` show it live | Not run in a container | Foundation of the REST backend (placeholder only; not the real API) |
| 7 | Dashboard scaffold | `web/public/{index.html,styles.css,app.js}`, `infra/docker/{web.Dockerfile,nginx.conf}` | Exercised via screenshots (no automated UI test yet) | `web/README.md` | Responsive at 390 px and 1100 px; light + dark themes; persistent synthetic banner | `evidence/phase-1/screenshots/dashboard-desktop.png`, `dashboard-mobile.png`, `dashboard-dark.png` (captured against the real health server through a local proxy standing in for nginx) | **nginx container not run** | Foundation of the responsive dashboard |
| 8 | dbt project skeleton (medallion conventions) | `dbt/dbt_project.yml`, `dbt/profiles.yml.example`, `dbt/models/{bronze,silver,gold}/` | `test_dbt_project_declares_medallion_layers`, `test_dbt_profile_has_no_literal_credentials` (YAML-level only) | `dbt/README.md`, `docs/architecture.md` §2 | 3 layers declared; **0 models** | — | **`dbt parse`/`debug` not run** — dbt not installable here | Establishes Bronze/Silver/Gold structure |
| 9 | Architecture & decision records | `docs/architecture.md`, `docs/adr/0001–0003` | Mermaid blocks rendered without error | the files themselves | 2 diagrams; 3 ADRs (data model, tenancy, runtime) | `evidence/phase-1/diagrams/architecture-1.png`, `architecture-2.png` | n/a | Documents the design of the proposed system |
| 10 | Evidence framework | `evidence/collect_evidence.py`, `docs/evidence-manifest.md`, `docs/CHANGELOG.md` | Run end-to-end by `make evidence` | `evidence/README.md` | Records pass/fail per marker and `not_run` + reason where applicable | `evidence/phase-1/evidence.json`, `junit-all.xml` | n/a | Traceable substantiation for each milestone |

## Phase 1 metrics (from `evidence.json`)

| Metric | Value |
|--------|-------|
| Tests (total / failed / skipped) | 95 / 0 / 0 |
| By group | unit 42 · integration 41 · data_quality 12 |
| Test runtime | ≈ 5.6 s |
| Lint | `ruff check` and `ruff format --check`: pass |
| Synthetic rows (default config) | 22,372 (attendance 10,800 · LMS 6,832 · grades 2,880 · assessments 1,440 · students 360 · interventions 46 · definitions 8 · schools 6) |
| Determinism | two generations → identical manifest/checksums |
| Runtime dependencies | 0 (standard library only) |

## Known gaps (not verified in Phase 1)

| Item | Why | How to close |
|------|-----|--------------|
| Docker images / compose stack not started | No Docker daemon in the build environment (`docker compose config` was validated) | `docker compose up --build`, then `curl :8000/healthz`; add a deployment screenshot |
| dbt not executed | dbt CLI not installed; package installs disabled | `docker compose --profile tools run --rm dbt`; add `dbt parse` to CI when models arrive |
| CI not run on GitHub | Repository not pushed | Push; attach the Actions run URL here |
| Pinned image tags (MinIO, mc) unverified | No network registry access | Confirm on first `docker compose pull` |
| No automated UI test | Phase 1 dashboard is a static scaffold | Add Playwright tests with the first real view |
| Pre-commit hooks not executed | `pre-commit` not installed | `make setup`, then `pre-commit run --all-files` |

## How to update this manifest
1. Add or change the feature row; every cell must point to a real file, test or artifact.
2. `make evidence` to refresh `evidence/phase-N/`; add screenshots under `screenshots/`.
3. Move items out of *Known gaps* only when the closing evidence exists.
4. Add a `docs/CHANGELOG.md` entry.
