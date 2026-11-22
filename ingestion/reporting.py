# ruff: noqa: E501
"""Result objects and renderers: run report, validation report, summary tables, lineage events.

Reports contain counts, codes, field names, file names and IDs -- **never record values**.
Row-level examples show *where* a record failed (location) and *why* (code + field).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .models import EntityCounters, Status, ValidationSummary

REPORT_SCHEMA_VERSION = "1.0"


def aggregate_status(statuses: Iterable[str]) -> str:
    """Worst-wins roll-up of entity/source statuses into a source/run status."""
    items = list(statuses)
    if not items:
        return Status.NO_NEW_DATA.value
    failed = [s for s in items if s in (Status.FAILED.value, Status.FAILED_QUALITY_GATE.value)]
    if failed:
        return Status.FAILED.value if len(failed) == len(items) else Status.PARTIAL_FAILURE.value
    if any(s == Status.PARTIAL_FAILURE.value for s in items):
        return Status.PARTIAL_FAILURE.value
    if any(
        s in (Status.SUCCEEDED_WITH_REJECTS.value, Status.COMPLETED_WITH_REJECTS.value)
        for s in items
    ):
        return Status.COMPLETED_WITH_REJECTS.value
    if all(s == Status.NO_NEW_DATA.value for s in items):
        return Status.NO_NEW_DATA.value
    return Status.SUCCEEDED.value


@dataclass
class EntityResult:
    source: str
    entity: str
    bronze_table: str
    status: str = Status.SUCCEEDED.value
    counters: EntityCounters = field(default_factory=EntityCounters)
    batch_id: str = ""
    started_at: str = ""
    finished_at: str = ""
    duration_seconds: float = 0.0
    schema_version: str = ""
    incremental: dict[str, Any] = field(default_factory=dict)
    schema_checks: list[dict[str, Any]] = field(default_factory=list)
    validation: ValidationSummary = field(default_factory=ValidationSummary)
    warnings: list[str] = field(default_factory=list)
    failure: dict[str, str] | None = None
    bronze_batches: list[str] = field(default_factory=list)
    dead_letter_path: str | None = None
    units: list[dict[str, Any]] = field(default_factory=list)
    abort_source: bool = False

    def to_dict(self, *, include_examples: bool = False) -> dict[str, Any]:
        validation = self.validation.to_dict()
        if not include_examples:
            validation = {k: v for k, v in validation.items() if k != "examples"}
        return {
            "entity": self.entity,
            "bronze_table": self.bronze_table,
            "status": self.status,
            "batch_id": self.batch_id,
            "schema_version": self.schema_version,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": round(self.duration_seconds, 4),
            "records": self.counters.to_dict(),
            "incremental": self.incremental,
            "units": self.units,
            "schema_checks": self.schema_checks,
            "validation": validation,
            "warnings": self.warnings,
            "failure": self.failure,
            "bronze_batches": self.bronze_batches,
            "dead_letter_path": self.dead_letter_path,
        }


@dataclass
class SourceResult:
    name: str
    connector_type: str
    metadata: dict[str, Any]
    entities: list[EntityResult] = field(default_factory=list)
    status: str = Status.SUCCEEDED.value
    duration_seconds: float = 0.0
    failure: dict[str, str] | None = None

    @property
    def totals(self) -> EntityCounters:
        total = EntityCounters()
        for entity in self.entities:
            total.add(entity.counters)
        return total

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.name,
            "connector_type": self.connector_type,
            "source_system": self.metadata,
            "status": self.status,
            "duration_seconds": round(self.duration_seconds, 4),
            "records": self.totals.to_dict(),
            "failure": self.failure,
            "entities": [e.to_dict() for e in self.entities],
        }


@dataclass
class RunReport:
    run_id: str
    started_at: str
    config_path: str
    dry_run: bool
    data_mode: str
    framework_version: str
    sources: list[SourceResult] = field(default_factory=list)
    finished_at: str = ""
    duration_seconds: float = 0.0
    status: str = Status.SUCCEEDED.value

    @property
    def totals(self) -> EntityCounters:
        total = EntityCounters()
        for source in self.sources:
            total.add(source.totals)
        return total

    def entity_results(self) -> list[EntityResult]:
        return [e for s in self.sources for e in s.entities]

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_schema_version": REPORT_SCHEMA_VERSION,
            "run_id": self.run_id,
            "status": self.status,
            "dry_run": self.dry_run,
            "data_mode": self.data_mode,
            "framework_version": self.framework_version,
            "config_path": self.config_path,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": round(self.duration_seconds, 4),
            "records": self.totals.to_dict(),
            "sources": [s.to_dict() for s in self.sources],
            "notes": [
                "received = rejected + accepted; accepted = duplicates + unchanged_skipped + loaded",
                "transformed = records successfully mapped and type-coerced (== accepted)",
                "loaded = records written to Bronze in this run",
                "This report contains no record values.",
            ],
        }

    def validation_report(self) -> dict[str, Any]:
        return {
            "report_schema_version": REPORT_SCHEMA_VERSION,
            "run_id": self.run_id,
            "notes": [
                "Row-level examples list location, error code and field only (no values).",
                "Rejected raw records are in the dead-letter store, not in this report.",
            ],
            "entities": [
                {
                    "source": source.name,
                    "entity": e.entity,
                    "status": e.status,
                    "schema_checks": e.schema_checks,
                    "records": {
                        "received": e.counters.received,
                        "rejected": e.counters.rejected,
                        "accepted": e.counters.accepted,
                    },
                    "validation": e.validation.to_dict(),
                    "warnings": e.warnings,
                    "failure": e.failure,
                }
                for source in self.sources
                for e in source.entities
            ],
        }


_COLUMNS = (
    ("received", "recv"),
    ("accepted", "acc"),
    ("rejected", "rej"),
    ("duplicates", "dup"),
    ("unchanged_skipped", "unch"),
    ("transformed", "xform"),
    ("loaded", "load"),
    ("retries", "retry"),
)


def render_text_summary(report: RunReport) -> str:
    """Fixed-width table of per-entity record counts, with per-source subtotals."""
    header = (
        f"{'SOURCE / ENTITY':<46}{'STATUS':<24}"
        + "".join(f"{c[1]:>8}" for c in _COLUMNS)
        + f"{'secs':>8}"
    )
    lines = [
        f"Run {report.run_id}  status={report.status}  dry_run={report.dry_run}",
        header,
        "-" * len(header),
    ]
    for source in report.sources:
        for e in source.entities:
            label = f"{source.name} / {e.entity}"
            counts = "".join(f"{getattr(e.counters, c[0]):>8}" for c in _COLUMNS)
            lines.append(f"{label:<46}{e.status:<24}{counts}{e.duration_seconds:>8.2f}")
        if not source.entities:
            lines.append(f"{source.name:<46}{source.status:<24}")
        totals = source.totals
        counts = "".join(f"{getattr(totals, c[0]):>8}" for c in _COLUMNS)
        lines.append(
            f"{'  = ' + source.name + ' total':<46}{source.status:<24}{counts}{source.duration_seconds:>8.2f}"
        )
    total = report.totals
    counts = "".join(f"{getattr(total, c[0]):>8}" for c in _COLUMNS)
    lines.append("-" * len(header))
    lines.append(f"{'RUN TOTAL':<46}{report.status:<24}{counts}{report.duration_seconds:>8.2f}")
    return "\n".join(lines)


def render_markdown_summary(report: RunReport) -> str:
    """Markdown table of records by source (one row per source)."""
    head = "| Source | System | Status | Received | Accepted | Rejected | Duplicates | Unchanged | Transformed | Loaded | Retries | Seconds |"
    rule = "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"
    rows = [head, rule]
    for s in report.sources:
        t = s.totals
        rows.append(
            f"| `{s.name}` | {s.metadata.get('system', '')} | {s.status} | {t.received} | {t.accepted} | "
            f"{t.rejected} | {t.duplicates} | {t.unchanged_skipped} | {t.transformed} | {t.loaded} | "
            f"{t.retries} | {s.duration_seconds:.2f} |"
        )
    t = report.totals
    rows.append(
        f"| **Total** | | **{report.status}** | **{t.received}** | **{t.accepted}** | **{t.rejected}** | "
        f"**{t.duplicates}** | **{t.unchanged_skipped}** | **{t.transformed}** | **{t.loaded}** | "
        f"**{t.retries}** | **{report.duration_seconds:.2f}** |"
    )
    return "\n".join(rows)


def lineage_event(
    *,
    run_id: str,
    event_time: str,
    source: SourceResult,
    entity: EntityResult,
) -> dict[str, Any]:
    """OpenLineage-*inspired* event (job / run / inputs / outputs). Not a conformance claim."""
    ok = entity.status not in (Status.FAILED.value, Status.FAILED_QUALITY_GATE.value)
    return {
        "eventType": "COMPLETE" if ok else "FAIL",
        "eventTime": event_time,
        "producer": "edupulse-ingestion",
        "run": {"runId": run_id, "facets": {"batchId": entity.batch_id}},
        "job": {"namespace": "edupulse.ingestion", "name": f"{source.name}.{entity.entity}"},
        "inputs": [
            {
                "namespace": f"source:{source.metadata.get('system', '')}/{source.name}",
                "name": entity.entity,
                "facets": {
                    "sourceSystem": source.metadata,
                    "schemaChecks": entity.schema_checks,
                    "incremental": entity.incremental,
                    "units": entity.units,
                },
            }
        ],
        "outputs": [
            {
                "namespace": "bronze",
                "name": entity.bronze_table,
                "facets": {
                    "schemaVersion": entity.schema_version,
                    "recordsLoaded": entity.counters.loaded,
                    "bronzeBatches": entity.bronze_batches,
                    "deadLetterPath": entity.dead_letter_path,
                    "status": entity.status,
                },
            }
        ],
    }
