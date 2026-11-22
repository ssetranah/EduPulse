"""Structured (JSON Lines) run logging.

``log_event`` accepts only primitive field values and truncates long strings, which keeps raw
records and other bulky/sensitive objects out of logs by construction. Row *values* must never
be passed in; use counts, codes, field names, file names and IDs.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

LOGGER_NAME = "edupulse.ingest"
_MAX_STR = 300


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        payload.update(getattr(record, "fields", {}))
        return json.dumps(payload, default=str, ensure_ascii=True)


class _ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = getattr(record, "fields", {})
        detail = " ".join(f"{k}={v}" for k, v in fields.items() if k in _CONSOLE_KEYS)
        return f"[{record.levelname:<7}] {record.getMessage()} {detail}".rstrip()


_CONSOLE_KEYS = ("source", "entity", "status", "received", "loaded", "rejected", "reason")


def _safe(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    text = str(value)
    return text if len(text) <= _MAX_STR else text[:_MAX_STR] + "...[truncated]"


def log_event(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    clean = {k: _safe(v) for k, v in fields.items()}
    logger.log(level, event, extra={"fields": clean})


def configure_run_logging(
    log_file: Path | None, *, level: str = "INFO", console: TextIO | None = None
) -> logging.Logger:
    """Fresh logger writing JSON lines to ``log_file`` and a short summary to the console."""
    logger = logging.getLogger(f"{LOGGER_NAME}.{id(log_file)}")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(_JsonFormatter())
        logger.addHandler(file_handler)
    stream = console if console is not None else sys.stderr
    console_handler = logging.StreamHandler(stream)
    console_handler.setFormatter(_ConsoleFormatter())
    logger.addHandler(console_handler)
    return logger


def close_logger(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)
