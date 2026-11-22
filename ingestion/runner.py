"""The ingestion runner: the source-independent half of the framework.

For every entity of every selected source the runner does, in order::

    plan incremental -> extract streams -> (file ledger check) -> sample + schema check
      -> per record: parse-error / map+coerce / validate -> duplicate + unchanged detection
      -> Bronze write (staged) or dead-letter -> quality gate -> atomic commit -> state commit

Guarantees
----------
* Nothing is visible in Bronze, and no state moves, unless the whole entity batch succeeds.
* Record accounting invariants hold for every successful entity::

      received == rejected + accepted
      accepted == duplicates + unchanged_skipped + loaded
      transformed == accepted

* Reports and logs contain counts, codes and identifiers only -- never record values.
* ``data_origin: production`` is refused: this build is synthetic-only.
"""

from __future__ import annotations

import io
import itertools
import json
import logging
import random
import re
import sqlite3
import time
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .bronze import MANIFEST_NAME, BatchContext, BronzeStore, NullBatchWriter, partition_value
from .config import EntityConfig, IngestionConfig
from .connectors import SourceConnector, build_connector
from .constants import (
    DATA_ORIGIN_SYNTHETIC,
    SCHEMA_SAMPLE_SIZE,
    SYNTHETIC_MARKER_FIELD,
)
from .deadletter import DeadLetterWriter, NullDeadLetterWriter
from .errors import ConfigError, IngestionError
from .logging_utils import close_logger, configure_run_logging, log_event
from .mapping_spec import MappingSpec
from .models import (
    ErrorAction,
    ErrorContext,
    RawRecord,
    RecordStream,
    RowError,
    Status,
)
from .reporting import (
    EntityResult,
    RunReport,
    SourceResult,
    aggregate_status,
    lineage_event,
    render_markdown_summary,
    render_text_summary,
)
from .retry import RetryPolicy
from .state import StateStore
from .transform import RowProcessor, record_hash

_RUN_ID_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_STATE_FILE = "_state/ingest_state.sqlite"


def _iso(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_run_id(now: datetime | None = None) -> str:
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    return f"run-{stamp}-{random.SystemRandom().randrange(16**4):04x}"


class _EntityFailure(Exception):
    """Internal: unwinds an entity load with a classified failure."""

    def __init__(self, error: BaseException, decision_action: ErrorAction) -> None:
        super().__init__(str(error))
        self.error = error
        self.action = decision_action


class IngestionRunner:
    def __init__(
        self,
        config: IngestionConfig,
        *,
        config_path: str = "",
        output_dir: str | Path | None = None,
        run_id: str | None = None,
        dry_run: bool = False,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
        env: Mapping[str, str] | None = None,
        clock: Callable[[], datetime] | None = None,
        timer: Callable[[], float] = time.perf_counter,
        log_level: str = "INFO",
        console: Any = None,
        quiet: bool = False,
    ) -> None:
        self.config = config
        self.config_path = config_path
        self.root = Path(output_dir if output_dir is not None else config.storage.root)
        self.dry_run = dry_run
        self._sleep = sleep
        self._rng = rng
        self._env = env if env is not None else {}
        self._clock = clock or (lambda: datetime.now(UTC))
        self._timer = timer
        self._log_level = log_level
        self._console = console
        self._quiet = quiet
        self.run_id = run_id or new_run_id(self._clock())
        if not _RUN_ID_OK.match(self.run_id):
            raise ConfigError("run id may contain only letters, digits, '.', '_' and '-'")
        self.run_dir = self.root / "runs" / self.run_id
        self.bronze = BronzeStore(self.root)
        self.connectors: dict[str, SourceConnector] = {}

    # ------------------------------------------------------------------ public
    def run(self, source_names: list[str] | None = None) -> RunReport:
        names = self._select(source_names)
        for name in names:
            if self.config.source(name).data_origin != "synthetic":
                raise ConfigError(
                    f"source '{name}': data_origin '{self.config.source(name).data_origin}' is "
                    "not enabled in this build (synthetic data only)"
                )
        self.run_dir.mkdir(parents=True, exist_ok=True)
        logger = configure_run_logging(
            self.run_dir / "ingestion.log",
            level=self._log_level,
            console=(io.StringIO() if self._quiet else self._console),
        )
        started = self._clock()
        t0 = self._timer()
        report = RunReport(
            run_id=self.run_id,
            started_at=_iso(started),
            config_path=self.config_path,
            dry_run=self.dry_run,
            data_mode="synthetic",
            framework_version=__version__,
        )
        state = self._open_state()
        log_event(
            logger,
            logging.INFO,
            "run_started",
            run_id=self.run_id,
            sources=",".join(names),
            dry_run=self.dry_run,
        )
        try:
            for name in names:
                report.sources.append(self._run_source(name, state, logger))
        finally:
            state.close()
        report.finished_at = _iso(self._clock())
        report.duration_seconds = self._timer() - t0
        report.status = aggregate_status(s.status for s in report.sources)
        log_event(
            logger,
            logging.INFO,
            "run_finished",
            run_id=self.run_id,
            status=report.status,
            received=report.totals.received,
            loaded=report.totals.loaded,
            rejected=report.totals.rejected,
        )
        close_logger(logger)
        self._write_reports(report)
        return report

    # ------------------------------------------------------------------ setup
    def _select(self, names: list[str] | None) -> list[str]:
        if not names or names == ["all"]:
            selected = [n for n, s in self.config.sources.items() if s.enabled]
            if not selected:
                raise ConfigError("no enabled sources in the config")
            return selected
        for name in names:
            if not self.config.source(name).enabled:
                raise ConfigError(f"source '{name}' is disabled (enabled: false)")
        return names

    def _open_state(self) -> StateStore:
        path = self.root / _STATE_FILE
        if self.dry_run:  # a dry run reads state but never writes it
            return StateStore.in_memory_copy(path) if path.exists() else StateStore(":memory:")
        return StateStore(path)

    def _recover_state(
        self,
        connector: SourceConnector,
        entity: EntityConfig,
        mapping: MappingSpec,
        state: StateStore,
        logger: logging.Logger,
    ) -> int:
        """Re-register Bronze batches that state does not know about.

        State is a cache of Bronze. After a crash between the Bronze commit and the state commit
        (or after state loss) Bronze holds batches that state lacks; without this step the next run
        would load the same records again. Returns the number of batches recovered.
        """
        pattern = (
            f"tenant_id=*/source={partition_value(connector.name)}/"
            f"table={mapping.bronze_table}/batch_id=*"
        )
        unknown: dict[str, list[Path]] = {}
        for batch_dir in self.bronze.root.glob(pattern):
            manifest_path = batch_dir / MANIFEST_NAME
            if not manifest_path.is_file():
                continue
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("source_entity") != entity.name:
                continue
            if not state.batch_known(connector.name, entity.name, manifest["batch_id"]):
                unknown.setdefault(manifest["batch_id"], []).append(batch_dir)
        for batch_id, dirs in sorted(unknown.items()):
            hashes: list[str] = []
            watermark: str | None = None
            for batch_dir in dirs:
                for row in self.bronze.read_batch(batch_dir):
                    hashes.append(row["_source_record_hash"])
                    watermark = self._max(watermark, row["canonical"], mapping)
            state.recover_batch(
                connector.name,
                entity.name,
                batch_id=batch_id,
                timestamp=_iso(self._clock()),
                hashes=hashes,
                watermark=watermark,
            )
            log_event(
                logger,
                logging.WARNING,
                "state_recovered",
                source=connector.name,
                entity=entity.name,
                batch_id=batch_id,
                records=len(hashes),
            )
        return len(unknown)

    # ------------------------------------------------------------------ per source
    def _run_source(self, name: str, state: StateStore, logger: logging.Logger) -> SourceResult:
        source_cfg = self.config.source(name)
        policy = RetryPolicy.from_config(self.config.retry_for(name))
        started = self._timer()
        try:
            connector = build_connector(
                name,
                source_cfg,
                retry_policy=policy,
                sleep=self._sleep,
                rng=self._rng,
                env=self._env,
                base_dir=self.config.base_dir,
            )
        except IngestionError as exc:
            result = SourceResult(
                name,
                source_cfg.type,
                {"source_name": name},
                status=Status.FAILED.value,
                failure={"type": type(exc).__name__, "message": str(exc)},
            )
            log_event(logger, logging.ERROR, "source_failed", source=name, reason=str(exc))
            return result
        self.connectors[name] = connector
        result = SourceResult(name, source_cfg.type, connector.metadata.to_dict())
        log_event(logger, logging.INFO, "source_started", source=name, connector=source_cfg.type)

        for entity in source_cfg.entities:
            if not entity.enabled:
                continue
            entity_result = self._run_entity(connector, entity, state, logger)
            result.entities.append(entity_result)
            if entity_result.abort_source:
                result.failure = entity_result.failure
                break
        result.duration_seconds = self._timer() - started
        result.status = (
            Status.FAILED.value
            if result.failure and result.failure.get("abort")
            else aggregate_status(e.status for e in result.entities)
        )
        log_event(logger, logging.INFO, "source_finished", source=name, status=result.status)
        return result

    # ------------------------------------------------------------------ per entity
    def _run_entity(
        self,
        connector: SourceConnector,
        entity: EntityConfig,
        state: StateStore,
        logger: logging.Logger,
    ) -> EntityResult:
        source = connector.name
        started_ts = self._clock()
        t0 = self._timer()
        retries_before = connector.retry_count
        try:
            mapping = self.config.load_mapping(entity)
        except IngestionError as exc:
            res = EntityResult(
                source,
                entity.name,
                "",
                status=Status.FAILED.value,
                failure={"type": type(exc).__name__, "message": str(exc)},
            )
            return res

        batch_id = f"{self.run_id}-{entity.name}"
        res = EntityResult(
            source,
            entity.name,
            mapping.bronze_table,
            batch_id=batch_id,
            started_at=_iso(started_ts),
            schema_version=mapping.schema_version,
        )
        ctx = BatchContext(
            batch_id=batch_id,
            run_id=self.run_id,
            source_name=source,
            source_system=connector.config.system.system,
            source_entity=entity.name,
            bronze_table=mapping.bronze_table,
            schema_version=mapping.schema_version,
            ingestion_ts=_iso(started_ts),
            data_origin=DATA_ORIGIN_SYNTHETIC,
            field_sensitivity=mapping.field_sensitivity,
        )
        writer: Any = NullBatchWriter() if self.dry_run else self.bronze.new_batch(ctx)
        dead: Any = NullDeadLetterWriter() if self.dry_run else DeadLetterWriter(self.root, ctx)
        recovered = self._recover_state(connector, entity, mapping, state, logger)
        if recovered:
            res.warnings.append(
                f"state recovered from {recovered} Bronze batch(es) it did not know"
            )
        previous_wm = state.watermark(source, entity.name)
        plan = connector.plan_incremental(entity, mapping, previous_wm)
        res.incremental = {
            "strategy": plan.strategy,
            "since": plan.since,
            "previous_watermark": previous_wm,
            "description": plan.description,
        }
        log_event(
            logger,
            logging.INFO,
            "entity_started",
            source=source,
            entity=entity.name,
            strategy=plan.strategy,
            since=plan.since,
        )

        new_hashes: list[str] = []
        units: list[tuple[str, str]] = []
        fingerprints: dict[str, str] = {}
        observed_max: str | None = None
        try:
            observed_max = self._load_streams(
                connector,
                entity,
                mapping,
                state,
                res,
                writer,
                dead,
                plan.since,
                new_hashes,
                units,
                fingerprints,
                logger,
            )
            gate = self.config.gate_for(source)
            c = res.counters
            if (
                c.received >= gate.min_records
                and c.received
                and c.rejected / c.received > gate.max_reject_ratio
            ):
                res.status = Status.FAILED_QUALITY_GATE.value
                res.failure = {
                    "type": "QualityGateError",
                    "message": f"reject ratio {c.rejected}/{c.received} exceeds "
                    f"{gate.max_reject_ratio:.0%} (min_records={gate.min_records}); nothing loaded",
                }
                writer.abort()
                c.loaded = 0
            else:
                commit = writer.commit()
                res.bronze_batches = [self._rel(p) for p in commit.batch_dirs]
                if not self.dry_run:
                    watermark = connector.advance_watermark(entity, previous_wm, observed_max)
                    state.commit_entity(
                        source,
                        entity.name,
                        batch_id=batch_id,
                        timestamp=_iso(self._clock()),
                        hashes=new_hashes,
                        watermark=watermark,
                        units=units,
                        fingerprints=fingerprints,
                    )
                    res.incremental["new_watermark"] = watermark
                if c.rejected:
                    res.status = Status.SUCCEEDED_WITH_REJECTS.value
                elif c.loaded == 0:
                    res.status = Status.NO_NEW_DATA.value
                else:
                    res.status = Status.SUCCEEDED.value
        except (IngestionError, sqlite3.Error, OSError) as exc:
            writer.abort()  # no-op if already committed
            res.status = Status.FAILED.value
            res.failure = {"type": type(exc).__name__, "message": str(exc)}
            if res.bronze_batches:  # Bronze was published but the state update failed
                res.failure["message"] += (
                    " (Bronze batch was committed; state is recovered from Bronze on the next run)"
                )
            else:
                res.counters.loaded = 0
        except _EntityFailure as failure:
            writer.abort()
            res.counters.loaded = 0
            res.status = Status.FAILED.value
            res.failure = {"type": type(failure.error).__name__, "message": str(failure.error)}
            res.abort_source = failure.action is ErrorAction.ABORT_SOURCE
            if res.abort_source:
                res.failure["abort"] = "true"
        except Exception:
            writer.abort()
            raise
        dl_dir = dead.close(batch_status=res.status)
        res.dead_letter_path = self._rel(dl_dir) if dl_dir else None
        res.counters.retries = connector.retry_count - retries_before
        res.finished_at = _iso(self._clock())
        res.duration_seconds = self._timer() - t0
        log_event(
            logger,
            logging.INFO if res.status != Status.FAILED.value else logging.ERROR,
            "entity_finished",
            source=source,
            entity=entity.name,
            status=res.status,
            received=res.counters.received,
            loaded=res.counters.loaded,
            rejected=res.counters.rejected,
            reason=(res.failure or {}).get("message"),
        )
        return res

    # ------------------------------------------------------------------ streams and rows
    def _load_streams(
        self,
        connector: SourceConnector,
        entity: EntityConfig,
        mapping: MappingSpec,
        state: StateStore,
        res: EntityResult,
        writer: Any,
        dead: Any,
        since: str | None,
        new_hashes: list[str],
        units: list[tuple[str, str]],
        fingerprints: dict[str, str],
        logger: logging.Logger,
    ) -> str | None:
        source = connector.name
        source_cfg = connector.config
        processor = RowProcessor(
            mapping,
            config_values={"tenant_id": source_cfg.tenant_id} if source_cfg.tenant_id else {},
        )
        seen_hash: set[str] = set()
        seen_key: dict[tuple[Any, ...], str] = {}
        observed_max: str | None = None
        ingestion_ts = _iso(self._clock())

        def fail(exc: BaseException, unit: str | None = None) -> _EntityFailure:
            decision = connector.handle_error(
                exc, ErrorContext(source, entity.name, "extract", unit)
            )
            action = decision.action
            if action in (ErrorAction.RETRY, ErrorAction.SKIP_STREAM):
                action = (
                    ErrorAction.FAIL_ENTITY
                )  # retries are already exhausted; skip only for schema
            return _EntityFailure(exc, action)

        streams = connector.extract(entity, since)
        while True:
            try:
                stream = next(streams, None)
            except IngestionError as exc:
                raise fail(exc) from exc
            if stream is None:
                break
            if stream.checksum and state.unit_processed(
                source, entity.name, stream.unit_id, stream.checksum
            ):
                res.units.append({"unit": stream.unit_id, "status": "skipped_already_processed"})
                log_event(
                    logger,
                    logging.INFO,
                    "unit_skipped",
                    source=source,
                    entity=entity.name,
                    unit=stream.unit_id,
                    reason="already processed (path+checksum)",
                )
                continue
            unit_counts = {"unit": stream.unit_id, "received": 0, "rejected": 0, "status": "read"}
            try:
                observed_max = self._read_stream(
                    stream,
                    connector,
                    entity,
                    mapping,
                    state,
                    res,
                    writer,
                    dead,
                    processor,
                    new_hashes,
                    seen_hash,
                    seen_key,
                    observed_max,
                    fingerprints,
                    ingestion_ts,
                    unit_counts,
                    logger,
                )
            except IngestionError as exc:
                raise fail(exc, stream.unit_id) from exc
            if stream.checksum and unit_counts["status"] == "read":
                units.append((stream.unit_id, stream.checksum))
            res.units.append(unit_counts)
        return observed_max

    def _read_stream(
        self,
        stream: RecordStream,
        connector: SourceConnector,
        entity: EntityConfig,
        mapping: MappingSpec,
        state: StateStore,
        res: EntityResult,
        writer: Any,
        dead: Any,
        processor: RowProcessor,
        new_hashes: list[str],
        seen_hash: set[str],
        seen_key: dict[tuple[Any, ...], str],
        observed_max: str | None,
        fingerprints: dict[str, str],
        ingestion_ts: str,
        unit_counts: dict[str, Any],
        logger: logging.Logger,
    ) -> str | None:
        source = connector.name
        c = res.counters
        records: Iterator[RawRecord] = stream.records
        if stream.declared_fields is not None:
            observed_fields = list(stream.declared_fields)
        else:
            head = list(itertools.islice(records, SCHEMA_SAMPLE_SIZE))
            observed_fields = sorted({k for r in head if r.parse_error is None for k in r.payload})
            records = itertools.chain(head, records)

        check = connector.validate_schema(entity, mapping, observed_fields)
        previous_fp = state.last_fingerprint(source, entity.name, stream.unit_id)
        if previous_fp is not None:
            check = type(check)(
                **{**check.__dict__, "drift_from_previous": previous_fp != check.fingerprint}
            )
        res.schema_checks.append({"unit": stream.unit_id, **check.to_dict()})
        if check.drift_from_previous:
            res.warnings.append(f"schema drift detected in {stream.unit_id}")
            res.validation.add_warning("SCHEMA_DRIFT")
        if check.unexpected:
            res.validation.add_warning("UNEXPECTED_FIELDS")
            if mapping.unknown_fields == "reject_entity":
                from .errors import SchemaError

                raise SchemaError(f"{stream.unit_id}: unexpected fields {list(check.unexpected)}")
            if mapping.unknown_fields == "warn":
                res.warnings.append(
                    f"{stream.unit_id}: unmapped source fields {list(check.unexpected)}"
                )
        if not check.ok:
            from .errors import SchemaError

            msg = f"{stream.unit_id}: required source fields missing {list(check.missing_required)}"
            if entity.stream_error_policy == "skip_stream":
                res.warnings.append(msg + " (stream skipped)")
                unit_counts["status"] = "skipped_schema"
                return observed_max
            raise SchemaError(msg)
        fingerprints[stream.unit_id] = check.fingerprint

        for raw in records:
            c.received += 1
            unit_counts["received"] += 1
            rhash = (
                record_hash(raw.payload)
                if raw.parse_error is None
                else record_hash({"loc": raw.location})
            )
            errors: list[RowError]
            canonical: dict[str, Any] | None
            if raw.parse_error is not None:
                canonical, errors = (
                    None,
                    [RowError(raw.parse_error, None, "record could not be parsed")],
                )
            else:
                canonical, errors = processor.process(raw.payload)
                if canonical is not None and canonical.get(SYNTHETIC_MARKER_FIELD) != _marker():
                    canonical, errors = (
                        None,
                        [
                            RowError(
                                "SYNTHETIC_MARKER_INVALID",
                                SYNTHETIC_MARKER_FIELD,
                                "synthetic source record lacks the SYNTHETIC_TEST_DATA marker",
                            )
                        ],
                    )
            if canonical is None:
                self._reject(res, dead, unit_counts, raw, rhash, errors)
                continue

            key = tuple(canonical[k] for k in mapping.primary_key) if mapping.primary_key else None
            if rhash in seen_hash:
                self._accept(c)
                c.duplicates += 1
                res.validation.add_warning("DUPLICATE_RECORD_IN_BATCH")
                observed_max = self._max(observed_max, canonical, mapping)
                continue
            if key is not None and key in seen_key and seen_key[key] != rhash:
                if mapping.duplicate_policy == "reject":
                    self._reject(
                        res,
                        dead,
                        unit_counts,
                        raw,
                        rhash,
                        [
                            RowError(
                                "DUPLICATE_KEY",
                                None,
                                "same primary key with different content in batch",
                            )
                        ],
                    )
                    continue
                self._accept(c)
                c.duplicates += 1
                res.validation.add_warning("DUPLICATE_KEY_KEPT_FIRST")
                continue
            seen_hash.add(rhash)
            if key is not None:
                seen_key[key] = rhash
            observed_max = self._max(observed_max, canonical, mapping)
            if state.has_hash(source, entity.name, rhash):
                self._accept(c)
                c.unchanged_skipped += 1
                continue
            self._accept(c)
            c.loaded += 1
            new_hashes.append(rhash)
            writer.write(
                {
                    "_batch_id": res.batch_id,
                    "_run_id": self.run_id,
                    "_ingestion_ts": ingestion_ts,
                    "_source_name": source,
                    "_source_system": connector.config.system.system,
                    "_source_entity": entity.name,
                    "_bronze_table": mapping.bronze_table,
                    "_source_location": raw.location,
                    "_source_record_hash": rhash,
                    "_schema_version": mapping.schema_version,
                    "_data_origin": DATA_ORIGIN_SYNTHETIC,
                    "tenant_id": canonical["tenant_id"],
                    "raw": raw.payload,
                    "canonical": canonical,
                }
            )
        return observed_max

    # ------------------------------------------------------------------ small helpers
    @staticmethod
    def _accept(c: Any) -> None:
        c.accepted += 1
        c.transformed += 1

    @staticmethod
    def _max(current: str | None, canonical: dict[str, Any], mapping: MappingSpec) -> str | None:
        field = mapping.incremental_field
        value = canonical.get(field) if field else None
        if value is None:
            return current
        return value if current is None or value > current else current

    @staticmethod
    def _reject(
        res: EntityResult,
        dead: Any,
        unit_counts: dict[str, Any],
        raw: RawRecord,
        rhash: str,
        errors: list[RowError],
    ) -> None:
        res.counters.rejected += 1
        unit_counts["rejected"] += 1
        res.validation.add_rejection(raw.location, errors)
        dead.write(raw=raw.payload, location=raw.location, record_hash=rhash, errors=errors)

    def _rel(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return path.as_posix()

    # ------------------------------------------------------------------ reports
    def _write_reports(self, report: RunReport) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "run_report.json").write_text(
            json.dumps(report.to_dict(), indent=2, sort_keys=False) + "\n", encoding="utf-8"
        )
        (self.run_dir / "validation_report.json").write_text(
            json.dumps(report.validation_report(), indent=2) + "\n", encoding="utf-8"
        )
        (self.run_dir / "summary.md").write_text(
            render_markdown_summary(report) + "\n", encoding="utf-8"
        )
        (self.run_dir / "summary.txt").write_text(
            render_text_summary(report) + "\n", encoding="utf-8"
        )
        event_time = report.finished_at
        with (self.run_dir / "lineage_events.jsonl").open("w", encoding="utf-8") as handle:
            for source in report.sources:
                for entity in source.entities:
                    event = lineage_event(
                        run_id=report.run_id, event_time=event_time, source=source, entity=entity
                    )
                    handle.write(json.dumps(event, sort_keys=True) + "\n")


def _marker() -> str:
    from .synthetic.constants import SYNTHETIC_MARKER

    return SYNTHETIC_MARKER
