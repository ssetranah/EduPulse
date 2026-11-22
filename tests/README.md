# tests

| Directory | Marker | Scope |
|-----------|--------|-------|
| `unit/` | `unit` | config, generator, writers, validator, health payload |
| `integration/` | `integration` | CLI as a subprocess, real HTTP health server, repository/compose/CI hygiene |
| `data_quality/` | `data_quality` | domain, range, completeness and plausibility checks on generated data |

Run: `make test` (or `pytest -m unit`, `-m integration`, `-m data_quality`).
