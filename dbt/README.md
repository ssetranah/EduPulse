# dbt project

Transformations for the medallion layers. **No models exist yet** (Phase 1 is foundations only).

| Layer  | Directory       | Materialization | Purpose                                        |
|--------|-----------------|-----------------|------------------------------------------------|
| Bronze | `models/bronze` | view            | Raw landed data, typed but otherwise untouched |
| Silver | `models/silver` | table           | Cleaned, conformed, tenant-aware entities      |
| Gold   | `models/gold`   | table           | Analytics marts for the API/dashboard          |

Local mode targets DuckDB; `profiles.yml.example` also sketches a Databricks target.
Copy it to `profiles.yml` (git-ignored) to run dbt outside Docker.

> Status: `dbt_project.yml` and the profile are unvalidated in Phase 1 because the dbt CLI was not
> installable in the build environment. `docker compose --profile tools run --rm dbt` runs
> `dbt debug` and is the first check to perform.
