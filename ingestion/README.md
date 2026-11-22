# ingestion

- `synthetic/` — deterministic generator + validator for clearly fake (`SYNTHETIC_TEST_DATA`) source
  extracts. `python -m ingestion.synthetic generate|validate`. Layout: `spec.py` (single declarative
  definition of every extract), `generator.py`, `writers.py`, `validate.py`, `cli.py`.
- Config-driven CSV/JSON/API adapters and Bronze loading: **planned** (no school-specific logic will
  live in code; sources are described by configuration).
