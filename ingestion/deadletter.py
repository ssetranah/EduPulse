"""Dead-letter store for rejected records.

Rejected records are *not* dropped: the raw payload and the value-free list of row errors are
kept so the source can be fixed and the records replayed. Because dead-letter files contain raw
source values they are treated as **restricted** data: written owner-only where the OS allows,
never printed to logs, and never copied into run reports.

Layout::

    <root>/dead_letter/source=<s>/table=<t>/batch_id=<b>/rejected.jsonl
                                                      _MANIFEST.json
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import IO, Any

from .bronze import MANIFEST_NAME, BatchContext, partition_value
from .models import RowError


class DeadLetterWriter:
    def __init__(self, root: str | Path, ctx: BatchContext) -> None:
        self._dir = (
            Path(root)
            / "dead_letter"
            / f"source={partition_value(ctx.source_name)}"
            / f"table={ctx.bronze_table}"
            / f"batch_id={partition_value(ctx.batch_id)}"
        )
        self._ctx = ctx
        self._handle: IO[bytes] | None = None
        self._digest = hashlib.sha256()
        self.rows = 0
        self._codes: dict[str, int] = {}

    @property
    def path(self) -> Path:
        return self._dir / "rejected.jsonl"

    def write(
        self, *, raw: dict[str, Any], location: str, record_hash: str, errors: list[RowError]
    ) -> None:
        if self._handle is None:
            self._dir.mkdir(parents=True, exist_ok=False)
            self._handle = self.path.open("wb")
        entry = {
            "_batch_id": self._ctx.batch_id,
            "_run_id": self._ctx.run_id,
            "_source_name": self._ctx.source_name,
            "_source_entity": self._ctx.source_entity,
            "_source_location": location,
            "_source_record_hash": record_hash,
            "row_errors": [e.to_dict() for e in errors],
            "raw": raw,
        }
        line = (
            json.dumps(entry, ensure_ascii=True, separators=(",", ":"), default=str) + "\n"
        ).encode()
        self._handle.write(line)
        self._digest.update(line)
        self.rows += 1
        for err in errors:
            self._codes[err.code] = self._codes.get(err.code, 0) + 1

    def close(self, *, batch_status: str) -> Path | None:
        """Finalize; returns the directory, or ``None`` if nothing was rejected."""
        if self._handle is None:
            return None
        self._handle.close()
        manifest = {
            "batch_id": self._ctx.batch_id,
            "run_id": self._ctx.run_id,
            "source_name": self._ctx.source_name,
            "source_entity": self._ctx.source_entity,
            "bronze_table": self._ctx.bronze_table,
            "batch_status": batch_status,
            "rows": self.rows,
            "sha256": self._digest.hexdigest(),
            "errors_by_code": dict(sorted(self._codes.items())),
            "sensitivity": "restricted: contains raw source values",
        }
        manifest_path = self._dir / MANIFEST_NAME
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", "utf-8")
        for file in (self.path, manifest_path):
            with contextlib.suppress(OSError):  # best effort on Windows
                os.chmod(file, stat.S_IRUSR)  # owner read-only
        return self._dir


class NullDeadLetterWriter:
    """Dry-run stand-in."""

    def __init__(self) -> None:
        self.rows = 0
        self.path = None

    def write(self, **_: Any) -> None:
        self.rows += 1

    def close(self, *, batch_status: str) -> None:
        return None
