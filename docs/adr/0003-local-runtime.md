# ADR-0003: Local runtime — DuckDB + MinIO, PostgreSQL optional

**Status:** Accepted (MVP) · **Date:** 2026-11-21

## Decision
- **DuckDB** (a file in a shared volume) is the default local lakehouse engine; dbt targets it
  through `dbt-duckdb`. It needs no server and runs in CI.
- **MinIO** provides S3-compatible storage so landing/Bronze code paths match cloud object storage.
- **PostgreSQL** is optional (`--profile postgres`) and reserved for the serving store and
  row-level security (ADR-0002).
- **Databricks** is the production-shaped target; warehouse-specific SQL stays behind dbt
  adapters/macros so models port.
- **API framework:** deferred to the phase that adds the first data endpoint. Phase 1 ships a
  stdlib-only health stub so nothing unverified is committed.

## Consequences
- Fast, dependency-light developer loop and CI.
- Some Databricks features (Delta, Unity Catalog) cannot be exercised locally; a Databricks
  integration check is planned for the deployment phase.
