"""CSV / Excel / JSON Lines / JSON file-drop connector.

A *file drop* is a directory where an upstream system (or a person) leaves extract files. The
connector never modifies, moves or deletes them; the runner's processed-file ledger (path +
SHA-256) prevents re-ingesting an unchanged file, while a changed file is picked up again and
row-level hash idempotency suppresses the records that did not change.

Safety: patterns cannot escape the drop directory (no absolute paths, no ``..``), symlinks that
resolve outside it are ignored, and files over ``max_file_mb`` are refused.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Iterator, Sequence
from datetime import date, datetime, time
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar

from ..config import FileDropEntity, FileDropSource
from ..errors import ConfigError, PermanentSourceError, SchemaError, TransientSourceError
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
from .base import SourceConnector

_EXTENSIONS = {
    ".csv": "csv",
    ".tsv": "csv",
    ".xlsx": "excel",
    ".xlsm": "excel",
    ".jsonl": "jsonl",
    ".ndjson": "jsonl",
    ".json": "json",
}


class FileDropConnector(SourceConnector):
    source_type: ClassVar[str] = "file_drop"
    extraction_method: ClassVar[str] = "file drop (CSV / Excel / JSON Lines / JSON)"

    config: FileDropSource

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.drop_dir = Path(self.config.drop_dir).expanduser().resolve()

    # ------------------------------------------------------------------ discovery
    def _check_pattern(self, pattern: str) -> None:
        parts = PurePosixPath(pattern.replace("\\", "/")).parts
        if PurePosixPath(pattern).is_absolute() or ".." in parts or Path(pattern).is_absolute():
            raise ConfigError(f"file_pattern '{pattern}' must be relative and stay inside drop_dir")

    def matching_files(self, entity: FileDropEntity) -> list[Path]:
        self._check_pattern(entity.file_pattern)
        if not self.drop_dir.is_dir():
            return []
        found: list[Path] = []
        for path in sorted(self.drop_dir.glob(entity.file_pattern)):
            if not path.is_file():
                continue
            try:
                path.resolve().relative_to(self.drop_dir)
            except ValueError:
                continue  # symlink escaping the drop directory
            found.append(path)
        return found

    def discover(self) -> list[EntityDescriptor]:
        return [
            EntityDescriptor(
                name=e.name,
                supports_incremental=True,
                description=f"files matching {e.file_pattern}",
                available_units=len(self.matching_files(e)),
            )
            for e in self.config.entities
            if e.enabled
        ]

    # ------------------------------------------------------------------ extraction
    def extract(self, entity: FileDropEntity, since: str | None) -> Iterator[RecordStream]:  # type: ignore[override]
        for path in self.matching_files(entity):
            yield self._open_stream(entity, path)

    def _open_stream(self, entity: FileDropEntity, path: Path) -> RecordStream:
        unit_id = path.relative_to(self.drop_dir).as_posix()
        size_mb = path.stat().st_size / (1024 * 1024)
        if size_mb > self.config.max_file_mb:
            raise PermanentSourceError(
                f"{unit_id}: file is {size_mb:.0f} MB, over the {self.config.max_file_mb} MB limit"
            )
        fmt = entity.format if entity.format != "auto" else _EXTENSIONS.get(path.suffix.lower())
        if fmt is None:
            raise SchemaError(f"{unit_id}: cannot infer format from extension '{path.suffix}'")

        checksum = self.call_with_retry(f"checksum {unit_id}", lambda: _sha256(path))
        if fmt == "csv":
            fields, records = self._csv(entity, path, unit_id)
        elif fmt == "excel":
            fields, records = self._excel(entity, path, unit_id)
        elif fmt == "jsonl":
            fields, records = None, self._jsonl(path, unit_id)
        else:
            fields, records = None, self._json(entity, path, unit_id)
        return RecordStream(
            entity=entity.name,
            unit_id=unit_id,
            records=records,
            declared_fields=fields,
            checksum=checksum,
        )

    # -- CSV -----------------------------------------------------------------------------------
    def _csv(
        self, entity: FileDropEntity, path: Path, unit_id: str
    ) -> tuple[list[str], Iterator[RawRecord]]:
        opts = entity.csv

        def opener() -> Any:
            try:
                return path.open(newline="", encoding=opts.encoding)
            except PermissionError as exc:  # e.g. file still open in Excel on Windows
                raise TransientSourceError(f"{unit_id}: file is locked") from exc
            except OSError as exc:
                raise PermanentSourceError(f"{unit_id}: cannot open file ({exc.strerror})") from exc

        handle = self.call_with_retry(f"open {unit_id}", opener)
        reader = csv.reader(handle, delimiter=opts.delimiter, quotechar=opts.quotechar)
        try:
            header = next(reader)
        except StopIteration:
            handle.close()
            raise SchemaError(f"{unit_id}: file is empty (no header row)") from None
        except (csv.Error, UnicodeDecodeError) as exc:
            handle.close()
            raise SchemaError(f"{unit_id}: unreadable header ({type(exc).__name__})") from exc
        header = [h.strip() for h in header]
        if len(set(header)) != len(header) or any(h == "" for h in header):
            handle.close()
            raise SchemaError(f"{unit_id}: header has blank or duplicate column names")

        def rows() -> Iterator[RawRecord]:
            try:
                for row in reader:
                    if not row:
                        continue  # blank line
                    location = f"{unit_id}:line {reader.line_num}"
                    if len(row) != len(header):
                        yield RawRecord({}, location, parse_error="COLUMN_COUNT_MISMATCH")
                        continue
                    yield RawRecord(dict(zip(header, row, strict=True)), location)
            except UnicodeDecodeError as exc:
                raise SchemaError(f"{unit_id}: not valid {opts.encoding} text") from exc
            except csv.Error as exc:
                raise SchemaError(f"{unit_id}: malformed CSV ({exc})") from exc
            finally:
                handle.close()

        return header, rows()

    # -- Excel ---------------------------------------------------------------------------------
    def _excel(
        self, entity: FileDropEntity, path: Path, unit_id: str
    ) -> tuple[list[str], Iterator[RawRecord]]:
        try:
            import openpyxl
        except ImportError as exc:
            raise PermanentSourceError(
                "Excel support needs the 'openpyxl' package: pip install 'edupulse-ai[excel]'"
            ) from exc
        opts = entity.excel

        def opener() -> Any:
            try:
                return openpyxl.load_workbook(path, read_only=True, data_only=True)
            except PermissionError as exc:
                raise TransientSourceError(f"{unit_id}: file is locked") from exc
            except Exception as exc:  # corrupt / not an xlsx
                raise SchemaError(
                    f"{unit_id}: not a readable Excel workbook ({type(exc).__name__})"
                ) from exc

        workbook = self.call_with_retry(f"open {unit_id}", opener)
        try:
            names = workbook.sheetnames
            if isinstance(opts.sheet, int):
                if not 0 <= opts.sheet < len(names):
                    raise SchemaError(f"{unit_id}: sheet index {opts.sheet} out of range")
                sheet = workbook[names[opts.sheet]]
            else:
                if opts.sheet not in names:
                    raise SchemaError(f"{unit_id}: sheet '{opts.sheet}' not found")
                sheet = workbook[opts.sheet]
            row_iter = sheet.iter_rows(values_only=True)
            row_number = 0
            header: list[str] | None = None
            while header is None:
                try:
                    values = next(row_iter)
                except StopIteration:
                    raise SchemaError(
                        f"{unit_id}: header row {opts.header_row} not found"
                    ) from None
                row_number += 1
                if row_number == opts.header_row:
                    header = [("" if v is None else str(v).strip()) for v in values]
        except BaseException:
            workbook.close()
            raise
        if not any(header) or len({h for h in header if h}) != len([h for h in header if h]):
            workbook.close()
            raise SchemaError(f"{unit_id}: header row is empty or has duplicate names")
        columns = [(i, h) for i, h in enumerate(header) if h]

        def rows() -> Iterator[RawRecord]:
            nonlocal row_number
            try:
                for values in row_iter:
                    row_number += 1
                    if all(v is None or (isinstance(v, str) and not v.strip()) for v in values):
                        continue
                    payload = {
                        h: _jsonable(values[i] if i < len(values) else None) for i, h in columns
                    }
                    yield RawRecord(payload, f"{unit_id}:row {row_number}")
            finally:
                workbook.close()

        return [h for _, h in columns], rows()

    # -- JSON Lines / JSON ------------------------------------------------------------------
    def _jsonl(self, path: Path, unit_id: str) -> Iterator[RawRecord]:
        def opener() -> Any:
            try:
                return path.open(encoding="utf-8-sig")
            except PermissionError as exc:
                raise TransientSourceError(f"{unit_id}: file is locked") from exc
            except OSError as exc:
                raise PermanentSourceError(f"{unit_id}: cannot open file ({exc.strerror})") from exc

        handle = self.call_with_retry(f"open {unit_id}", opener)

        def rows() -> Iterator[RawRecord]:
            try:
                for number, line in enumerate(handle, start=1):
                    if not line.strip():
                        continue
                    location = f"{unit_id}:line {number}"
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        yield RawRecord({}, location, parse_error="INVALID_JSON")
                        continue
                    if not isinstance(obj, dict):
                        yield RawRecord({}, location, parse_error="ROW_NOT_AN_OBJECT")
                        continue
                    yield RawRecord(obj, location)
            except UnicodeDecodeError as exc:
                raise SchemaError(f"{unit_id}: not valid UTF-8 text") from exc
            finally:
                handle.close()

        return rows()

    def _json(self, entity: FileDropEntity, path: Path, unit_id: str) -> Iterator[RawRecord]:
        try:
            body = json.loads(path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SchemaError(f"{unit_id}: not valid JSON") from exc
        except PermissionError as exc:
            raise TransientSourceError(f"{unit_id}: file is locked") from exc
        items: Any = body
        if entity.json_records_path:
            for part in entity.json_records_path.split("."):
                items = items.get(part) if isinstance(items, dict) else None
        if not isinstance(items, list):
            raise SchemaError(f"{unit_id}: expected a JSON array of records")

        def rows() -> Iterator[RawRecord]:
            for index, obj in enumerate(items):
                location = f"{unit_id}:item {index}"
                if isinstance(obj, dict):
                    yield RawRecord(obj, location)
                else:
                    yield RawRecord({}, location, parse_error="ROW_NOT_AN_OBJECT")

        return rows()

    # ------------------------------------------------------------------ interface (rest)
    def validate_schema(
        self, entity: Any, mapping: MappingSpec, observed_fields: Sequence[str]
    ) -> SchemaCheck:
        return self.default_schema_check(mapping, observed_fields)

    def plan_incremental(
        self, entity: Any, mapping: MappingSpec, last_watermark: str | None
    ) -> IncrementalPlan:
        return IncrementalPlan(
            strategy="file_ledger",
            description="skip files whose path+SHA-256 were already ingested; "
            "row-hash idempotency suppresses unchanged rows of a re-delivered file",
        )

    def advance_watermark(
        self, entity: Any, previous: str | None, observed_max: str | None
    ) -> str | None:
        return self.default_advance_watermark(previous, observed_max)

    def handle_error(self, error: BaseException, context: ErrorContext) -> ErrorDecision:
        return self.default_error_decision(error, context)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except PermissionError as exc:
        raise TransientSourceError(f"{path.name}: file is locked") from exc
    except OSError as exc:
        raise PermanentSourceError(f"{path.name}: cannot read file ({exc.strerror})") from exc
    return digest.hexdigest()


def _jsonable(value: Any) -> Any:
    """Excel cell values -> JSON-safe values (dates become ISO strings)."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat()
    return value
