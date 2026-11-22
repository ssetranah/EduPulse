# tests

| Directory | Marker | Scope |
|-----------|--------|-------|
| `unit/` | `unit` | config, generator, writers, validator, health payload; Phase 2: retry, mapping/coercion, config validation, state/Bronze/dead-letter, in-process CLI (`test_ing_*`) |
| `integration/` | `integration` | CLI as a subprocess, real HTTP health server, repository/compose/CI hygiene; Phase 2: file drops (CSV/Excel/JSONL), REST connector vs a loopback mock API, runner + mock systems, run reports, repo checks (`test_ing_*`) |
| `data_quality/` | `data_quality` | domain, range, completeness and plausibility checks on generated data; Phase 2: properties of what lands in Bronze (`test_ing_bronze_quality.py`) |

Shared Phase 2 helpers live in `tests/ing_support.py`; the `write_ingest_config` fixture is in `conftest.py`.

Run: `make test` (or `pytest -m unit`, `-m integration`, `-m data_quality`).
