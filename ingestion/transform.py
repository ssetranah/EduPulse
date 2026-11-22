"""Row mapping, data-type coercion, null handling and row-level validation.

``RowProcessor`` turns one raw source record into a canonical record according to a
``MappingSpec``. It returns either a canonical dict or a list of ``RowError``s. Errors carry a
stable *code*, the *target field* and a message built from configuration only -- **never the
offending value**, so logs and reports cannot leak student data.

Error codes: ``MISSING_REQUIRED``, ``TYPE_COERCION_FAILED``, ``OUT_OF_RANGE``,
``NOT_IN_ALLOWED_VALUES``, ``NOT_IN_VALUE_MAP``, ``PATTERN_MISMATCH``, ``MAX_LENGTH_EXCEEDED``.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any

from .mapping_spec import FieldSpec, MappingSpec
from .models import RowError

_DEFAULT_TRUE = ("true", "t", "yes", "y", "1")
_DEFAULT_FALSE = ("false", "f", "no", "n", "0")
_DEFAULT_DATE_FORMATS = ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S")

_MISSING = object()


class _Invalid(Exception):
    """Internal: a field failed coercion/validation; carries the RowError to report."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def record_hash(payload: Mapping[str, Any]) -> str:
    """SHA-256 of the raw payload in a canonical JSON form (sorted keys, no whitespace).

    Computed over the *raw* record only -- never over ingestion metadata -- so the same source
    record always hashes the same, which is what makes incremental loads idempotent.
    """
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class RowProcessor:
    """Applies a mapping to raw records."""

    def __init__(self, mapping: MappingSpec, *, config_values: Mapping[str, str] | None = None):
        self._mapping = mapping
        self._config_values = dict(config_values or {})
        self._null_tokens = {t.strip().lower() for t in mapping.null_values}
        self._compiled = {
            f.target: re.compile(f.pattern) for f in mapping.fields if f.pattern is not None
        }

    # ------------------------------------------------------------------ public
    def process(self, raw: Mapping[str, Any]) -> tuple[dict[str, Any] | None, list[RowError]]:
        canonical: dict[str, Any] = {}
        errors: list[RowError] = []
        for spec in self._mapping.fields:
            try:
                canonical[spec.target] = self._build_field(spec, raw)
            except _Invalid as exc:
                errors.append(RowError(exc.code, spec.target, exc.message))
        return (None, errors) if errors else (canonical, [])

    # ------------------------------------------------------------------ internals
    def _is_null(self, value: Any, spec: FieldSpec) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            tokens = (
                {t.strip().lower() for t in spec.null_values}
                if spec.null_values is not None
                else self._null_tokens
            )
            return value.strip().lower() in tokens
        return False

    def _resolve(self, spec: FieldSpec, raw: Mapping[str, Any]) -> Any:
        if spec.has_constant:
            return spec.constant
        if spec.from_config is not None:
            return self._config_values.get(spec.from_config)
        for alias in spec.aliases:
            if alias in raw and not self._is_null(raw[alias], spec):
                return raw[alias]
        return None

    def _build_field(self, spec: FieldSpec, raw: Mapping[str, Any]) -> Any:
        value = self._resolve(spec, raw)
        if self._is_null(value, spec):
            if spec.default is not None:
                value = spec.default
            elif spec.required:
                raise _Invalid("MISSING_REQUIRED", "required value is missing or null")
            else:
                return None

        if isinstance(value, str) and spec.trim:
            value = value.strip()
        if isinstance(value, str) and spec.case:
            value = value.upper() if spec.case == "upper" else value.lower()
        if spec.value_map is not None:
            value = self._translate(spec, value)

        coerced = self._coerce(spec, value)
        self._check_constraints(spec, coerced)
        return coerced

    @staticmethod
    def _translate(spec: FieldSpec, value: Any) -> Any:
        key = str(value)
        if spec.value_map is None:  # pragma: no cover - guarded by the caller
            return value
        if key in spec.value_map:
            return spec.value_map[key]
        if spec.allowed_values is not None and key in spec.allowed_values:
            return value  # already a canonical value
        raise _Invalid("NOT_IN_VALUE_MAP", "source code has no entry in value_map")

    # -- coercion ---------------------------------------------------------------------------
    def _coerce(self, spec: FieldSpec, value: Any) -> Any:
        kind = spec.type
        try:
            if kind == "string":
                return _to_string(value)
            if kind == "integer":
                return _to_int(value)
            if kind == "float":
                return _to_float(value)
            if kind == "boolean":
                return _to_bool(value, spec)
            if kind == "date":
                return _to_date(value, spec)
            if kind == "datetime":
                return _to_datetime(value, spec)
        except (ValueError, TypeError, OverflowError):
            pass
        raise _Invalid("TYPE_COERCION_FAILED", f"value could not be converted to {kind}")

    # -- constraints ------------------------------------------------------------------------
    def _check_constraints(self, spec: FieldSpec, value: Any) -> None:
        if value is None:
            return
        if spec.allowed_values is not None and str(value) not in spec.allowed_values:
            raise _Invalid(
                "NOT_IN_ALLOWED_VALUES",
                f"value not in allowed set ({', '.join(spec.allowed_values)})",
            )
        if spec.max_length is not None and isinstance(value, str) and len(value) > spec.max_length:
            raise _Invalid("MAX_LENGTH_EXCEEDED", f"longer than {spec.max_length} characters")
        pattern = self._compiled.get(spec.target)
        if pattern is not None and not pattern.search(str(value)):
            raise _Invalid("PATTERN_MISMATCH", "value does not match the required pattern")
        if isinstance(value, int | float) and not isinstance(value, bool):
            if spec.min is not None and value < spec.min:
                raise _Invalid("OUT_OF_RANGE", f"below minimum {spec.min:g}")
            if spec.max is not None and value > spec.max:
                raise _Invalid("OUT_OF_RANGE", f"above maximum {spec.max:g}")


# ------------------------------------------------------------------------------ coercers
def _to_string(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))  # Excel stores 12 as 12.0
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _to_int(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("bool is not an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError("not integral")
        return int(value)
    text = str(value).strip()
    try:
        return int(text)
    except ValueError:
        number = float(text)
        if not math.isfinite(number) or not number.is_integer():
            raise
        return int(number)


def _to_float(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("bool is not a float")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite")
    return number


def _to_bool(value: Any, spec: FieldSpec) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    text = str(value).strip().lower()
    true_values = [v.lower() for v in spec.true_values] if spec.true_values else _DEFAULT_TRUE
    false_values = [v.lower() for v in spec.false_values] if spec.false_values else _DEFAULT_FALSE
    if text in true_values:
        return True
    if text in false_values:
        return False
    raise ValueError("not a boolean token")


def _to_date(value: Any, spec: FieldSpec) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    for fmt in spec.formats or _DEFAULT_DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError("no date format matched")


def _to_datetime(value: Any, spec: FieldSpec) -> str:
    """Normalize to UTC ``YYYY-MM-DDTHH:MM:SSZ``. Naive values are treated as UTC."""
    parsed: datetime | None = None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day)
    else:
        text = str(value).strip()
        for fmt in spec.formats or ():
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        if parsed is None and not spec.formats:
            parsed = datetime.fromisoformat(text)  # handles "Z" and offsets on Python 3.11+
    if parsed is None:
        raise ValueError("no datetime format matched")
    parsed = parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")
