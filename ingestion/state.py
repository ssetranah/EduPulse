"""Incremental-load state (SQLite, standard library).

Holds, per ``(source, entity)``: the watermark, the set of loaded record hashes (idempotency),
a ledger of processed file units, and the last observed schema fingerprint.

The state is a *cache of what Bronze already contains*; Bronze itself is the source of truth and
the state can be rebuilt from it. All updates for one entity batch happen in one transaction.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS watermarks (
    source TEXT NOT NULL, entity TEXT NOT NULL, watermark TEXT, updated_at TEXT NOT NULL,
    PRIMARY KEY (source, entity));
CREATE TABLE IF NOT EXISTS loaded_hashes (
    source TEXT NOT NULL, entity TEXT NOT NULL, record_hash TEXT NOT NULL, batch_id TEXT NOT NULL,
    PRIMARY KEY (source, entity, record_hash));
CREATE TABLE IF NOT EXISTS processed_units (
    source TEXT NOT NULL, entity TEXT NOT NULL, unit_id TEXT NOT NULL, checksum TEXT NOT NULL,
    batch_id TEXT NOT NULL, processed_at TEXT NOT NULL,
    PRIMARY KEY (source, entity, unit_id, checksum));
CREATE TABLE IF NOT EXISTS committed_batches (
    source TEXT NOT NULL, entity TEXT NOT NULL, batch_id TEXT NOT NULL,
    PRIMARY KEY (source, entity, batch_id));
CREATE TABLE IF NOT EXISTS schema_fingerprints (
    source TEXT NOT NULL, entity TEXT NOT NULL, unit_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
    PRIMARY KEY (source, entity, unit_id));
"""


class StateStore:
    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    @classmethod
    def in_memory_copy(cls, path: str | Path) -> StateStore:
        """A throw-away copy of an existing state file (used by dry runs: nothing is persisted)."""
        store = cls(":memory:")
        source = sqlite3.connect(str(path))
        try:
            source.backup(store._conn)
        finally:
            source.close()
        return store

    def close(self) -> None:
        self._conn.close()

    def batch_known(self, source: str, entity: str, batch_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM committed_batches WHERE source=? AND entity=? AND batch_id=?",
            (source, entity, batch_id),
        ).fetchone()
        return row is not None

    def recover_batch(
        self,
        source: str,
        entity: str,
        *,
        batch_id: str,
        timestamp: str,
        hashes: Iterable[str],
        watermark: str | None,
    ) -> None:
        """Re-register a batch that exists in Bronze but is missing from state (crash/loss)."""
        self.commit_entity(
            source,
            entity,
            batch_id=batch_id,
            timestamp=timestamp,
            hashes=hashes,
            watermark=self._max_watermark(source, entity, watermark),
            units=[],
            fingerprints={},
        )

    def _max_watermark(self, source: str, entity: str, candidate: str | None) -> str | None:
        current = self.watermark(source, entity)
        values = [v for v in (current, candidate) if v is not None]
        return max(values) if values else None

    # ------------------------------------------------------------------ reads
    def watermark(self, source: str, entity: str) -> str | None:
        row = self._conn.execute(
            "SELECT watermark FROM watermarks WHERE source=? AND entity=?", (source, entity)
        ).fetchone()
        return row[0] if row else None

    def has_hash(self, source: str, entity: str, record_hash: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM loaded_hashes WHERE source=? AND entity=? AND record_hash=?",
            (source, entity, record_hash),
        ).fetchone()
        return row is not None

    def unit_processed(self, source: str, entity: str, unit_id: str, checksum: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM processed_units WHERE source=? AND entity=? AND unit_id=? "
            "AND checksum=?",
            (source, entity, unit_id, checksum),
        ).fetchone()
        return row is not None

    def last_fingerprint(self, source: str, entity: str, unit_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT fingerprint FROM schema_fingerprints WHERE source=? AND entity=? AND unit_id=?",
            (source, entity, unit_id),
        ).fetchone()
        return row[0] if row else None

    # ------------------------------------------------------------------ writes
    def commit_entity(
        self,
        source: str,
        entity: str,
        *,
        batch_id: str,
        timestamp: str,
        hashes: Iterable[str],
        watermark: str | None,
        units: Iterable[tuple[str, str]],
        fingerprints: dict[str, str],
    ) -> None:
        """Record the outcome of one successfully committed entity batch, atomically."""
        with self._conn:  # one transaction
            self._conn.execute(
                "INSERT OR IGNORE INTO committed_batches VALUES (?,?,?)", (source, entity, batch_id)
            )
            self._conn.executemany(
                "INSERT OR IGNORE INTO loaded_hashes VALUES (?,?,?,?)",
                [(source, entity, h, batch_id) for h in hashes],
            )
            if watermark is not None:
                self._conn.execute(
                    "INSERT INTO watermarks VALUES (?,?,?,?) "
                    "ON CONFLICT(source, entity) DO UPDATE SET watermark=excluded.watermark, "
                    "updated_at=excluded.updated_at",
                    (source, entity, watermark, timestamp),
                )
            self._conn.executemany(
                "INSERT OR REPLACE INTO processed_units VALUES (?,?,?,?,?,?)",
                [
                    (source, entity, unit_id, checksum, batch_id, timestamp)
                    for unit_id, checksum in units
                ],
            )
            self._conn.executemany(
                "INSERT OR REPLACE INTO schema_fingerprints VALUES (?,?,?,?)",
                [(source, entity, unit_id, fp) for unit_id, fp in fingerprints.items()],
            )
