# infra

- `docker/` — `api.Dockerfile` (also used by the synthetic generator service), `dbt.Dockerfile`,
  `web.Dockerfile`, `nginx.conf`. Containers run as non-root where the base image allows it.
- Cloud/Databricks infrastructure-as-code: planned.

Note: these files were validated structurally (`docker compose config`, YAML tests) but the images
have not been built in the Phase 1 build environment.
