# ingestion

Config-driven multi-source ingestion into Bronze (Phase 2) and the synthetic-data tooling (Phase 1).
Guides: [`docs/ingestion.md`](../docs/ingestion.md), [`docs/data-lineage.md`](../docs/data-lineage.md).

| Path | Role |
|------|------|
| `cli.py` | `edupulse ingest --source <name\|all> --config <path>`, `edupulse verify-bronze --root <dir>` |
| `runner.py` | `IngestionRunner`: the source-independent pipeline (mapping, validation, dedup, Bronze, dead letter, state, reports) |
| `connectors/` | `base.py` (abstract `SourceConnector`), `file_drop.py` (CSV/Excel/JSONL/JSON), `rest_api.py`, `mock_system.py` (mock SIS / LMS / assessment / attendance-intervention), `__init__.py` (registry) |
| `config.py`, `mapping_spec.py` | pydantic models for the ingestion YAML and the field-mapping YAML |
| `transform.py` | row mapping, type coercion, null handling, row-level validation, record hashing |
| `bronze.py`, `deadletter.py`, `state.py` | immutable Bronze store + `verify_bronze`; dead-letter store; SQLite incremental state |
| `retry.py`, `errors.py`, `models.py` | exponential backoff with jitter; error hierarchy; shared dataclasses |
| `reporting.py`, `logging_utils.py` | run/validation reports, lineage events, structured logging |
| `configs/` | `ingestion.yaml` (annotated example) and `mappings/*.yaml` (one per entity) |
| `synthetic/` | deterministic generator + validator (`python -m ingestion.synthetic generate|validate`); `native.py` renders vendor-native shapes for the mock connectors; `rest_server.py` is the loopback mock REST API |

No school- or vendor-specific logic lives in code: sources are described by YAML.
