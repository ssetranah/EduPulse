"""Plain data structures shared by connectors, the runner and the reports."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ErrorAction(StrEnum):
    """What the runner should do after a connector classifies an error."""

    RETRY = "retry"
    SKIP_STREAM = "skip_stream"
    FAIL_ENTITY = "fail_entity"
    ABORT_SOURCE = "abort_source"


class Status(StrEnum):
    """Load status vocabulary used for entities, sources and whole runs."""

    SUCCEEDED = "SUCCEEDED"
    SUCCEEDED_WITH_REJECTS = "SUCCEEDED_WITH_REJECTS"
    NO_NEW_DATA = "NO_NEW_DATA"
    FAILED = "FAILED"
    FAILED_QUALITY_GATE = "FAILED_QUALITY_GATE"
    # run/source level aggregates
    COMPLETED_WITH_REJECTS = "COMPLETED_WITH_REJECTS"
    PARTIAL_FAILURE = "PARTIAL_FAILURE"


@dataclass(frozen=True)
class SourceSystemMetadata:
    """Descriptive metadata about the system a connector reads from."""

    source_name: str
    system: str
    connector_type: str
    data_origin: str
    extraction_method: str
    vendor: str | None = None
    product: str | None = None
    version: str | None = None
    description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class EntityDescriptor:
    """An entity (table/file family/endpoint) that a source can provide."""

    name: str
    supports_incremental: bool
    description: str | None = None
    available_units: int | None = None


@dataclass
class RawRecord:
    """One record exactly as received (before mapping), plus where it came from."""

    payload: dict[str, Any]
    location: str
    #: set by a connector when the record could not even be parsed into fields
    parse_error: str | None = None


@dataclass
class RecordStream:
    """A lazily-read unit of extraction: one file, or one API endpoint/entity."""

    entity: str
    unit_id: str
    records: Iterator[RawRecord]
    #: field names known up-front (CSV header); ``None`` => inferred from a sample of records
    declared_fields: Sequence[str] | None = None
    #: content checksum used by the processed-file ledger (file drops only)
    checksum: str | None = None


@dataclass(frozen=True)
class IncrementalPlan:
    strategy: str  # "watermark" | "file_ledger" | "full_refresh"
    since: str | None = None
    description: str = ""


@dataclass(frozen=True)
class SchemaCheck:
    ok: bool
    fingerprint: str
    observed_field_count: int
    missing_required: tuple[str, ...] = ()
    missing_optional: tuple[str, ...] = ()
    unexpected: tuple[str, ...] = ()
    drift_from_previous: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "fingerprint": self.fingerprint,
            "observed_field_count": self.observed_field_count,
            "missing_required": list(self.missing_required),
            "missing_optional": list(self.missing_optional),
            "unexpected": list(self.unexpected),
            "drift_from_previous": self.drift_from_previous,
        }


@dataclass(frozen=True)
class RowError:
    """A row-level validation problem. Never carries the offending value."""

    code: str
    field: str | None
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "field": self.field, "message": self.message}


@dataclass(frozen=True)
class ErrorContext:
    source: str
    entity: str
    operation: str
    unit_id: str | None = None


@dataclass(frozen=True)
class ErrorDecision:
    action: ErrorAction
    reason: str


@dataclass
class EntityCounters:
    """Record accounting for one entity in one run.

    Invariants (asserted in tests)::

        received == rejected + accepted
        accepted == duplicates + unchanged_skipped + loaded
        transformed == accepted
    """

    received: int = 0
    accepted: int = 0
    rejected: int = 0
    duplicates: int = 0
    unchanged_skipped: int = 0
    transformed: int = 0
    loaded: int = 0
    retries: int = 0

    def to_dict(self) -> dict[str, int]:
        return dict(self.__dict__)

    def add(self, other: EntityCounters) -> None:
        for key, value in other.__dict__.items():
            setattr(self, key, getattr(self, key) + value)


@dataclass
class ValidationSummary:
    """Aggregated row-level validation outcomes (counts and value-free examples)."""

    errors_by_code: dict[str, int] = field(default_factory=dict)
    errors_by_field: dict[str, int] = field(default_factory=dict)
    warnings_by_code: dict[str, int] = field(default_factory=dict)
    examples: list[dict[str, Any]] = field(default_factory=list)
    max_examples: int = 25

    def add_rejection(self, location: str, errors: Sequence[RowError]) -> None:
        for err in errors:
            self.errors_by_code[err.code] = self.errors_by_code.get(err.code, 0) + 1
            if err.field:
                self.errors_by_field[err.field] = self.errors_by_field.get(err.field, 0) + 1
        if len(self.examples) < self.max_examples:
            self.examples.append({"location": location, "errors": [e.to_dict() for e in errors]})

    def add_warning(self, code: str) -> None:
        self.warnings_by_code[code] = self.warnings_by_code.get(code, 0) + 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "errors_by_code": dict(sorted(self.errors_by_code.items())),
            "errors_by_field": dict(sorted(self.errors_by_field.items())),
            "warnings_by_code": dict(sorted(self.warnings_by_code.items())),
            "examples": self.examples,
        }
