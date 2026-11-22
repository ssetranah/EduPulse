"""Mock vendor-system connectors (SIS, LMS, assessment, attendance/intervention).

These simulate what a real vendor API integration looks like -- source-native field names and
formats, pagination, a modification watermark, transient failures -- on top of the synthetic
data, entirely in-process. They let the whole ingestion path (mapping, coercion, validation,
retry, incremental load, Bronze, dead-letter) be exercised end to end with **no real system and
no real student data**.

Fault injection (``mock.faults`` in YAML) is deterministic, so tests and demos are reproducible:
``fail_first_n_calls`` (transient failures), ``bad_row_rate`` (rows that must be rejected),
``duplicate_row_rate``, ``drop_fields`` / ``add_fields`` (schema drift).
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any, ClassVar

from ..config import EntityConfig, MockSource
from ..errors import TransientSourceError
from ..mapping_spec import MappingSpec
from ..models import (
    EntityDescriptor,
    ErrorContext,
    ErrorDecision,
    IncrementalPlan,
    RawRecord,
    RecordStream,
    SchemaCheck,
)
from ..synthetic import GeneratedData, GeneratorConfig, generate
from ..synthetic.native import NATIVE_SPECS, SYSTEM_ENTITIES, NativeSpec, build_native_records
from .base import SourceConnector


@lru_cache(maxsize=8)
def _generated(config: GeneratorConfig) -> GeneratedData:
    return generate(config)


def _bucket(*parts: object) -> int:
    return int(hashlib.sha256(":".join(map(str, parts)).encode()).hexdigest()[:12], 16)


class MockSystemConnector(SourceConnector):
    """Shared behavior of the four mock vendor connectors."""

    system_key: ClassVar[str] = ""
    extraction_method: ClassVar[str] = "in-process simulation of a vendor API (synthetic data)"

    config: MockSource

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._calls = 0
        #: faults deliberately injected so far, e.g. ``{"bad_row": 3, "duplicate_row": 2}``
        self.injected: Counter[str] = Counter()

    # ------------------------------------------------------------------ data
    @property
    def generated(self) -> GeneratedData:
        opts = self.config.mock
        return _generated(
            GeneratorConfig(
                seed=opts.seed,
                tenants=opts.tenants,
                schools_per_tenant=opts.schools_per_tenant,
                students_per_school=opts.students_per_school,
                attendance_days=opts.attendance_days,
                lms_events_per_student=opts.lms_events_per_student,
            )
        )

    @property
    def as_of(self) -> datetime | None:
        value = self.config.mock.as_of
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value

    # ------------------------------------------------------------------ interface
    def discover(self) -> list[EntityDescriptor]:
        provided = SYSTEM_ENTITIES[self.system_key]
        return [
            EntityDescriptor(
                name=e.name,
                supports_incremental=True,
                description=f"{self.system_key} entity (mock)",
            )
            for e in self.config.entities
            if e.enabled and e.name in provided
        ]

    def extract(self, entity: EntityConfig, since: str | None) -> Iterator[RecordStream]:
        spec = NATIVE_SPECS[(self.system_key, entity.name)]
        records = build_native_records(
            self.generated, self.system_key, entity.name, as_of=self.as_of
        )
        if since:
            since_dt = datetime.fromisoformat(since)
            records = [item for item in records if item[0] >= since_dt]  # inclusive; see docs
        yield RecordStream(
            entity=entity.name,
            unit_id=f"mock://{self.name}/{entity.name}",
            records=self._paged(entity.name, spec, records),
        )

    def validate_schema(
        self, entity: Any, mapping: MappingSpec, observed_fields: Sequence[str]
    ) -> SchemaCheck:
        return self.default_schema_check(mapping, observed_fields)

    def plan_incremental(
        self, entity: Any, mapping: MappingSpec, last_watermark: str | None
    ) -> IncrementalPlan:
        if mapping.incremental_field:
            return IncrementalPlan(
                "watermark",
                since=last_watermark,
                description=f"modified >= watermark on '{mapping.incremental_field}'",
            )
        return IncrementalPlan("full_refresh", description="mapping has no incremental_field")

    def advance_watermark(
        self, entity: Any, previous: str | None, observed_max: str | None
    ) -> str | None:
        return self.default_advance_watermark(previous, observed_max)

    def handle_error(self, error: BaseException, context: ErrorContext) -> ErrorDecision:
        return self.default_error_decision(error, context)

    # ------------------------------------------------------------------ simulation
    def _fetch_page(
        self, records: list[tuple[datetime, dict[str, Any]]], start: int, size: int
    ) -> list[tuple[datetime, dict[str, Any]]]:
        self._calls += 1
        if self._calls <= self.config.mock.faults.fail_first_n_calls:
            raise TransientSourceError("simulated transient upstream failure (HTTP 503)")
        return [(ts, dict(rec)) for ts, rec in records[start : start + size]]

    def _paged(
        self, entity: str, spec: NativeSpec, records: list[tuple[datetime, dict[str, Any]]]
    ) -> Iterator[RawRecord]:
        size = self.config.mock.page_size
        for page_no, start in enumerate(range(0, len(records), size), start=1):
            chunk = self.call_with_retry(
                f"mock fetch {entity} page {page_no}",
                lambda s=start: self._fetch_page(records, s, size),
            )
            for index, (_ts, record) in enumerate(chunk):
                location = f"mock://{self.name}/{entity}?page={page_no}#{index}"
                yield from self._emit(spec, record, location)

    def _emit(self, spec: NativeSpec, record: dict[str, Any], location: str) -> Iterator[RawRecord]:
        faults = self.config.mock.faults
        seed = self.config.mock.seed
        key = record.get(spec.fault_id) or f"{record}"
        if faults.bad_row_rate > 0 and _bucket(seed, "bad", spec.entity, key) % 10_000 < int(
            faults.bad_row_rate * 10_000
        ):
            kind = _bucket(seed, "kind", spec.entity, key) % 3
            if kind == 1 and spec.fault_date:
                record[spec.fault_date] = "not-a-date"
            elif kind == 2 and spec.fault_code:
                record[spec.fault_code] = "??"
            else:
                record[spec.fault_id] = ""
            self.injected["bad_row"] += 1
        for name in faults.drop_fields:
            record.pop(name, None)
        for name in faults.add_fields:
            record[name] = "unexpected"
        yield RawRecord(record, location)
        if faults.duplicate_row_rate > 0 and _bucket(seed, "dup", spec.entity, key) % 10_000 < int(
            faults.duplicate_row_rate * 10_000
        ):
            self.injected["duplicate_row"] += 1
            yield RawRecord(dict(record), location + "(dup)")


class MockSISConnector(MockSystemConnector):
    source_type = "mock_sis"
    system_key = "SIS"


class MockLMSConnector(MockSystemConnector):
    source_type = "mock_lms"
    system_key = "LMS"


class MockAssessmentConnector(MockSystemConnector):
    source_type = "mock_assessment"
    system_key = "ASSESSMENT"


class MockAttendanceInterventionConnector(MockSystemConnector):
    source_type = "mock_attendance_intervention"
    system_key = "ATTENDANCE_INTERVENTION"
