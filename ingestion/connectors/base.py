"""The ``SourceConnector`` interface every source implements.

A connector answers five questions, one abstract method each:

* ``discover``          -- what entities/files/endpoints can you give me?
* ``extract``           -- stream the records (lazily, as ``RecordStream`` units).
* ``validate_schema``   -- is the observed source schema compatible with the field mapping?
* ``plan_incremental`` / ``advance_watermark`` -- how do I load only what is new?
* ``handle_error``      -- classify a failure: retry, skip the stream, fail the entity, abort.

Everything else -- mapping/coercion, duplicate detection, Bronze writing, dead-lettering,
state, reporting -- is source-independent and lives in the runner. Connectors are stateless;
the runner owns the state store.

Helpers (``call_with_retry``, ``default_schema_check``, ``default_error_decision``,
``default_advance_watermark``) are provided so concrete connectors stay small.
"""

from __future__ import annotations

import hashlib
import random
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, ClassVar, TypeVar

from ..config import EntityConfig
from ..errors import (
    AuthenticationError,
    SchemaError,
    SourceError,
    TransientSourceError,
)
from ..mapping_spec import MappingSpec
from ..models import (
    EntityDescriptor,
    ErrorAction,
    ErrorContext,
    ErrorDecision,
    IncrementalPlan,
    RecordStream,
    SchemaCheck,
    SourceSystemMetadata,
)
from ..retry import RetryPolicy, retry_call

T = TypeVar("T")


class SourceConnector(ABC):
    #: value of ``type:`` in the YAML that selects this connector
    source_type: ClassVar[str] = ""
    #: how data is obtained, recorded in source-system metadata
    extraction_method: ClassVar[str] = "unspecified"

    def __init__(
        self,
        name: str,
        config: Any,
        *,
        retry_policy: RetryPolicy,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
        env: Mapping[str, str] | None = None,
        base_dir: Path | None = None,
    ) -> None:
        self.name = name
        self.config = config
        self.retry_policy = retry_policy
        self._sleep = sleep
        self._rng = rng or random.Random(0)  # noqa: S311 - retry jitter only
        self.env: Mapping[str, str] = env if env is not None else {}
        self.base_dir = base_dir or Path(".")
        #: total retries performed so far (the runner reports per-entity deltas)
        self.retry_count = 0

    # ------------------------------------------------------------------ metadata
    @property
    def metadata(self) -> SourceSystemMetadata:
        system = self.config.system
        return SourceSystemMetadata(
            source_name=self.name,
            system=system.system,
            connector_type=self.source_type,
            data_origin=self.config.data_origin,
            extraction_method=self.extraction_method,
            vendor=system.vendor,
            product=system.product,
            version=system.version,
            description=system.description,
        )

    # ------------------------------------------------------------------ the interface
    @abstractmethod
    def discover(self) -> list[EntityDescriptor]:
        """List the configured entities this source can currently provide."""

    @abstractmethod
    def extract(self, entity: EntityConfig, since: str | None) -> Iterator[RecordStream]:
        """Yield lazily-read ``RecordStream`` units for ``entity``.

        ``since`` is the stored watermark (inclusive lower bound) for watermark-based sources.
        Transient I/O inside the returned streams must go through ``call_with_retry``.
        """

    @abstractmethod
    def validate_schema(
        self, entity: EntityConfig, mapping: MappingSpec, observed_fields: Sequence[str]
    ) -> SchemaCheck:
        """Compare the observed source field names with what the mapping needs."""

    @abstractmethod
    def plan_incremental(
        self, entity: EntityConfig, mapping: MappingSpec, last_watermark: str | None
    ) -> IncrementalPlan:
        """Decide how this run loads incrementally (watermark / file ledger / full refresh)."""

    @abstractmethod
    def advance_watermark(
        self, entity: EntityConfig, previous: str | None, observed_max: str | None
    ) -> str | None:
        """Watermark to store after a successful load (never moves backwards)."""

    @abstractmethod
    def handle_error(self, error: BaseException, context: ErrorContext) -> ErrorDecision:
        """Classify a failure that escaped the retry layer."""

    # ------------------------------------------------------------------ helpers
    def call_with_retry(self, operation: str, fn: Callable[[], T]) -> T:
        """Run ``fn`` with this connector's retry policy, counting retries."""

        def _count(_attempt: int, _delay: float, _exc: TransientSourceError) -> None:
            self.retry_count += 1

        return retry_call(
            fn,
            self.retry_policy,
            operation=operation,
            sleep=self._sleep,
            rng=self._rng,
            on_retry=_count,
        )

    @staticmethod
    def default_schema_check(mapping: MappingSpec, observed_fields: Sequence[str]) -> SchemaCheck:
        observed = set(observed_fields)
        missing_required: list[str] = []
        missing_optional: list[str] = []
        for spec in mapping.fields:
            if not spec.aliases or observed.intersection(spec.aliases):
                continue
            label = "|".join(spec.aliases)
            (
                missing_required if spec.required and spec.default is None else missing_optional
            ).append(label)
        unexpected = sorted(observed - mapping.expected_source_fields)
        fingerprint = hashlib.sha256("\n".join(sorted(observed)).encode()).hexdigest()[:16]
        return SchemaCheck(
            ok=not missing_required,
            fingerprint=fingerprint,
            observed_field_count=len(observed),
            missing_required=tuple(missing_required),
            missing_optional=tuple(missing_optional),
            unexpected=tuple(unexpected),
        )

    @staticmethod
    def default_error_decision(error: BaseException, context: ErrorContext) -> ErrorDecision:
        if isinstance(error, AuthenticationError):
            return ErrorDecision(ErrorAction.ABORT_SOURCE, "credentials rejected")
        if isinstance(error, TransientSourceError):
            return ErrorDecision(ErrorAction.RETRY, "transient source failure")
        if isinstance(error, SchemaError):
            return ErrorDecision(ErrorAction.SKIP_STREAM, "schema incompatible with mapping")
        if isinstance(error, SourceError):
            return ErrorDecision(ErrorAction.FAIL_ENTITY, "permanent source failure")
        return ErrorDecision(ErrorAction.FAIL_ENTITY, f"unexpected {type(error).__name__}")

    @staticmethod
    def default_advance_watermark(previous: str | None, observed_max: str | None) -> str | None:
        """ISO-8601 UTC strings sort lexicographically, so ``max`` is correct and monotonic."""
        candidates = [v for v in (previous, observed_max) if v is not None]
        return max(candidates) if candidates else None
