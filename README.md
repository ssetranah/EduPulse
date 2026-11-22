# EduPulse AI

A secure, modular, multi-tenant educational data platform for U.S. K-12 school networks.

> **Status: Phase 1 — foundation only.** The repository scaffold, CI, local stack, documentation
> framework and a synthetic-data generator exist. **There is no business logic yet**: no ingestion
> adapters, dbt models, authentication or analytics. See [`docs/CHANGELOG.md`](docs/CHANGELOG.md).
>
> **No real student data belongs in this repository.** All data is synthetic and marked
> `SYNTHETIC_TEST_DATA`. EduPulse implements technical controls *consistent with FERPA-aligned
> practices*; it is **not FERPA-certified** and nothing here is a statement of legal compliance.

## Purpose

School networks keep student data in disconnected systems — SIS, LMS, assessment, grading,
attendance and intervention tracking. EduPulse consolidates them into a governed analytics layer
so school leaders can:

- identify proficiency gaps across schools, grades, subjects and reporting periods,
- monitor assessment completion,
- track longitudinal student performance,
- prioritize academic interventions.

Predictive and AI (RAG) features are **decision support only**: they provide estimates and cited
context for educators and never determine a student's proficiency.

## Architecture

Source systems → config-driven ingestion → **Bronze** (raw) → **Silver** (cleaned, conformed) →
**Gold** (analytics) → REST API / dashboard → authorized users, with RBAC, field-level
sensitivity handling and audit logging cutting across every layer.
Diagrams, layer rules and design decisions: [`docs/architecture.md`](docs/architecture.md) and
[`docs/adr/`](docs/adr/).

| Directory | Contents |
|-----------|----------|
| [`ingestion/`](ingestion/) | Source adapters (later) and the synthetic-data generator (`ingestion/synthetic`) |
| [`dbt/`](dbt/) | Bronze/Silver/Gold models, tests, docs (skeleton) |
| [`api/`](api/) | REST API (Phase 1: health/version stub) |
| [`web/`](web/) | Responsive dashboard (Phase 1: static scaffold) |
| [`rag/`](rag/) | Optional RAG service — approved institutional documents only (reserved) |
| [`docs/`](docs/) | Architecture, ADRs, data dictionary, deployment guide, changelog, evidence manifest |
| [`tests/`](tests/) | `unit/`, `integration/`, `data_quality/` |
| [`infra/`](infra/) | Dockerfiles and deployment assets |
| [`evidence/`](evidence/) | Machine-readable evidence per milestone |

## Local setup

Requirements: Python ≥ 3.11 (and optionally Docker with Compose).

```bash
git clone <repo> && cd edupulse-ai
python -m pip install -e ".[dev]"     # ruff, pytest, pre-commit
pre-commit install
cp .env.example .env                  # local placeholders only; never commit .env
```

### Generate synthetic data

```bash
make synth                  # writes data/synthetic/ (git-ignored)
make validate-synth
python -m ingestion.synthetic generate --help     # tenants, schools, students, seed, defect rate…
```

### Run the stack

```bash
docker compose up --build                         # API :8000, dashboard :8080, MinIO :9000/:9001
docker compose --profile tools run --rm synth     # generate data inside the volume
docker compose --profile tools run --rm dbt       # dbt debug
docker compose --profile postgres up              # optional PostgreSQL
```

Or without Docker: `python -m api.health` and open `web/public/index.html` behind any static server.

## Test commands

```bash
make lint            # ruff check + format check
make test            # everything
make test-unit       # also: test-integration, test-dq
make evidence        # regenerate evidence/phase-1/ (tests, lint, metrics, samples)
```

CI (`.github/workflows/ci.yml`) runs lint, the three test groups on Python 3.11–3.13, a
synthetic-data generate+validate smoke test and `docker compose config` validation.

## Deployment path

Local Compose → single-node staging → Databricks-backed production, with hardening (authN/RBAC,
audit, retention, scanning, data-sharing agreements) required before any real data is handled.
Details and current status: [`docs/deployment.md`](docs/deployment.md).

## Evidence

Every milestone updates [`docs/evidence-manifest.md`](docs/evidence-manifest.md), which maps each
feature to its source files, tests, docs, metrics, screenshots and deployment evidence, and
records honestly what has *not* been verified.

## Engineering standards

Configuration-driven connectors (no school-specific logic), unit + integration + data-quality
tests, documented data handling, least-privilege access, PII minimization, and reproducible
synthetic fixtures.
