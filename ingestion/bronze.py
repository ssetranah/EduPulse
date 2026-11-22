"""Bronze store: raw, immutable, append-only landing of accepted source records.

Layout (Hive-style partitions, ready for Spark/Databricks/dbt external tables)::

    <root>/bronze/tenant_id=<t>/source=<s>/table=<bronze_table>/batch_id=<b>/
        part-00000.jsonl        one JSON object per record (envelope + raw + canonical)
        _MANIFEST.json          row counts, SHA-256 per part, schema version, sensitivity map

Guarantees
----------
* **Immutable.** A batch directory is created exactly once, by an atomic rename from a staging
  area. Committing over an existing batch raises ``ImmutabilityError``; committed files are made
  read-only. ``verify_bronze`` recomputes checksums to detect later tampering.
* **Atomic per entity batch.** Rows are streamed to ``_staging``; nothing is visible until
  ``commit()``. ``abort()`` (or a crash) leaves no partial batch.
* **Tenant-partitioned.** Each record's ``tenant_id`` routes it to its tenant partition
  (ADR-0002); a mixed-tenant extract is split at write time.

Record envelope (all underscore-prefixed fields are framework metadata)::

    _batch_id _run_id _ingestion_ts _source_name _source_system _source_entity _bronze_table
    _source_location _source_record_hash _schema_version _data_origin
    tenant_id raw{...as received...} canonical{...typed per mapping...}
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any
from urllib.parse import quote

from .errors import ImmutabilityError

MANIFEST_NAME = "_MANIFEST.json"
ENVELOPE_FIELDS = (
    "_batch_id",
    "_run_id",
    "_ingestion_ts",
    "_source_name",
    "_source_system",
    "_source_entity",
    "_bronze_table",
    "_source_location",
    "_source_record_hash",
    "_schema_version",
    "_data_origin",
    "tenant_id",
    "raw",
    "canonical",
)


@dataclass(frozen=True)
class BatchContext:
    """Everything that identifies one entity batch."""

    batch_id: str
    run_id: str
    source_name: str
    source_system: str
    source_entity: str
    bronze_table: str
    schema_version: str
    ingestion_ts: str
    data_origin: str
    field_sensitivity: dict[str, str] = field(default_factory=dict)


@dataclass
class BronzeCommit:
    """Result of committing a batch."""

    rows_by_tenant: dict[str, int] = field(default_factory=dict)
    batch_dirs: list[Path] = field(default_factory=list)

    @property
    def rows(self) -> int:
        return sum(self.rows_by_tenant.values())


def partition_value(value: str) -> str:
    """Filesystem-safe partition value (percent-encoded; ``=`` ``/`` ``:`` etc. are escaped)."""
    return quote(value, safe="-_.")


class _PartWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: IO[bytes] = path.open("wb")
        self.digest = hashlib.sha256()
        self.rows = 0

    def write_line(self, line: bytes) -> None:
        self.handle.write(line)
        self.digest.update(line)
        self.rows += 1

    def close(self) -> None:
        self.handle.close()


class BronzeBatchWriter:
    """Streams records of one batch into staging, then atomically publishes them."""

    def __init__(self, root: Path, ctx: BatchContext, *, max_rows_per_part: int = 50_000) -> None:
        self._root = root
        self._ctx = ctx
        self._max_rows = max_rows_per_part
        self._staging = root / "_staging" / ctx.batch_id
        self._parts: dict[str, list[_PartWriter]] = {}
        self._finished = False

    # ------------------------------------------------------------------ writing
    def write(self, record: dict[str, Any]) -> None:
        if self._finished:
            raise ImmutabilityError("batch already committed or aborted")
        tenant = record["tenant_id"]
        parts = self._parts.setdefault(tenant, [])
        if not parts or parts[-1].rows >= self._max_rows:
            tenant_dir = self._staging / partition_value(tenant)
            tenant_dir.mkdir(parents=True, exist_ok=True)
            parts.append(_PartWriter(tenant_dir / f"part-{len(parts):05d}.jsonl"))
        line = (json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n").encode()
        parts[-1].write_line(line)

    # ------------------------------------------------------------------ finishing
    def commit(self) -> BronzeCommit:
        """Publish every tenant partition atomically-per-tenant; never overwrite."""
        if self._finished:
            raise ImmutabilityError("batch already committed or aborted")
        self._finished = True
        for parts in self._parts.values():
            for part in parts:
                part.close()

        result = BronzeCommit()
        published: list[Path] = []
        try:
            for tenant, parts in self._parts.items():
                tenant_stage = self._staging / partition_value(tenant)
                final = self._final_dir(tenant)
                if final.exists():
                    raise ImmutabilityError(f"batch already exists, refusing to overwrite: {final}")
                manifest = {
                    "batch_id": self._ctx.batch_id,
                    "run_id": self._ctx.run_id,
                    "tenant_id": tenant,
                    "source_name": self._ctx.source_name,
                    "source_system": self._ctx.source_system,
                    "source_entity": self._ctx.source_entity,
                    "bronze_table": self._ctx.bronze_table,
                    "schema_version": self._ctx.schema_version,
                    "ingestion_ts": self._ctx.ingestion_ts,
                    "data_origin": self._ctx.data_origin,
                    "field_sensitivity": self._ctx.field_sensitivity,
                    "rows": sum(p.rows for p in parts),
                    "parts": [
                        {"file": p.path.name, "rows": p.rows, "sha256": p.digest.hexdigest()}
                        for p in parts
                    ],
                }
                (tenant_stage / MANIFEST_NAME).write_text(
                    json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
                )
                for file in tenant_stage.iterdir():
                    os.chmod(file, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
                final.parent.mkdir(parents=True, exist_ok=True)
                os.replace(tenant_stage, final)
                published.append(final)
                result.rows_by_tenant[tenant] = manifest["rows"]
                result.batch_dirs.append(final)
        except BaseException:
            for path in published:  # roll back what this commit created
                _force_rmtree(path)
            raise
        finally:
            self._cleanup_staging()
        return result

    def abort(self) -> None:
        """Discard everything staged. Safe to call more than once."""
        self._finished = True
        for parts in self._parts.values():
            for part in parts:
                part.close()
        self._cleanup_staging()

    def _cleanup_staging(self) -> None:
        """Remove this batch's staging dir, and the shared ``_staging`` parent if now empty."""
        _force_rmtree(self._staging)
        with contextlib.suppress(OSError):
            self._staging.parent.rmdir()  # only succeeds when empty (other batches may be staging)

    def _final_dir(self, tenant: str) -> Path:
        return (
            self._root
            / f"tenant_id={partition_value(tenant)}"
            / f"source={partition_value(self._ctx.source_name)}"
            / f"table={self._ctx.bronze_table}"
            / f"batch_id={partition_value(self._ctx.batch_id)}"
        )


class NullBatchWriter:
    """Stand-in used for dry runs: accepts records, persists nothing."""

    def __init__(self) -> None:
        self.rows = 0

    def write(self, record: dict[str, Any]) -> None:
        self.rows += 1

    def commit(self) -> BronzeCommit:
        return BronzeCommit()

    def abort(self) -> None:
        return None


class BronzeStore:
    """Entry point for creating batches and reading/verifying committed data."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root) / "bronze"

    def new_batch(self, ctx: BatchContext) -> BronzeBatchWriter:
        self.root.mkdir(parents=True, exist_ok=True)
        return BronzeBatchWriter(self.root, ctx)

    def batch_dirs(self) -> list[Path]:
        return sorted(p.parent for p in self.root.rglob(MANIFEST_NAME))

    def read_batch(self, batch_dir: Path) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        manifest = json.loads((batch_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
        for part in manifest["parts"]:
            with (batch_dir / part["file"]).open(encoding="utf-8") as handle:
                rows.extend(json.loads(line) for line in handle if line.strip())
        return rows

    def iter_records(self, bronze_table: str | None = None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for batch_dir in self.batch_dirs():
            if bronze_table and f"table={bronze_table}" not in batch_dir.parts:
                continue
            out.extend(self.read_batch(batch_dir))
        return out


def verify_bronze(root: str | Path) -> list[str]:
    """Recompute checksums/row counts for every committed batch. Empty list => intact."""
    problems: list[str] = []
    bronze_root = Path(root) / "bronze"
    if not bronze_root.exists():
        return problems
    for manifest_path in sorted(bronze_root.rglob(MANIFEST_NAME)):
        batch_dir = manifest_path.parent
        rel = batch_dir.relative_to(bronze_root).as_posix()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        listed = {p["file"] for p in manifest["parts"]}
        on_disk = {p.name for p in batch_dir.glob("part-*.jsonl")}
        for extra in sorted(on_disk - listed):
            problems.append(f"{rel}: unexpected file {extra}")
        for part in manifest["parts"]:
            path = batch_dir / part["file"]
            if not path.is_file():
                problems.append(f"{rel}: missing {part['file']}")
                continue
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != part["sha256"]:
                problems.append(f"{rel}: checksum mismatch in {part['file']}")
            if data.count(b"\n") != part["rows"]:
                problems.append(f"{rel}: row count mismatch in {part['file']}")
    return problems


def _force_rmtree(path: Path) -> None:
    """Remove a tree even if it contains read-only files (Windows-safe)."""

    def _onerror(func: Any, p: str, _exc: Any) -> None:
        os.chmod(p, stat.S_IWRITE)
        func(p)

    if path.exists():
        shutil.rmtree(path, onerror=_onerror)
