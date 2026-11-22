# Deployment guide

> **Phase 1 status:** only the local development path exists and it has **not** been exercised
> against a running Docker daemon yet (see `docs/evidence-manifest.md`). Cloud/Databricks steps
> below are the intended path, not completed work. Nothing here has been deployed to any school.

## 1. Local (Docker Compose)

```bash
cp .env.example .env            # placeholders only; change the passwords
docker compose up --build       # api :8000, dashboard :8080, MinIO :9000/:9001
docker compose --profile tools run --rm synth   # SYNTHETIC_TEST_DATA -> /data/synthetic
docker compose --profile tools run --rm dbt     # `dbt debug`
docker compose --profile postgres up            # optional PostgreSQL
```

Verify: `curl localhost:8000/healthz` → `{"status":"ok",…,"data_mode":"synthetic"}` and open
`http://localhost:8080` (the page shows "API reachable").

## 2. Developer machine (no Docker)

```bash
python -m pip install -e ".[dev]" && pre-commit install
make lint test
make synth validate-synth
python -m api.health            # health stub on 127.0.0.1:8000
```

## 3. Path to a shared environment (planned)

1. **Container registry + CI build** of the api/web/dbt images (extend `.github/workflows`).
2. **Single-node staging** with Compose or a managed container service; TLS terminated at a
   load balancer; secrets from a secret manager, never `.env`.
3. **Databricks target:** add `dbt-databricks`, set `DATABRICKS_*` env vars, run dbt with
   `--target databricks`; Bronze/Silver/Gold as Delta tables in a Unity Catalog; storage on cloud
   object storage with encryption at rest.
4. **Production hardening before any real data:** authentication and RBAC, audit log, retention
   jobs, backups, vulnerability scanning, penetration test, data-sharing agreements with each
   district. These are prerequisites, not part of Phase 1.

## 4. Configuration reference
See `.env.example` for every variable; all values are local-only placeholders.
