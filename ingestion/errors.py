"""Exception hierarchy for the ingestion framework.

The split between *transient* and *permanent* source errors drives retry behavior: only
``TransientSourceError`` is retried. Messages must never contain record values (student data),
only identifiers such as field names, file names and error codes.
"""

from __future__ import annotations


class IngestionError(Exception):
    """Base class for all ingestion failures."""


class ConfigError(IngestionError):
    """Invalid or unsafe configuration (bad YAML, unknown keys, missing mapping, ...)."""


class SourceError(IngestionError):
    """A problem talking to or reading from a source system."""


class TransientSourceError(SourceError):
    """A failure that may succeed if retried (timeouts, 5xx, 429, locked files)."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class PermanentSourceError(SourceError):
    """A failure that retrying will not fix (4xx, malformed payload, missing file)."""


class AuthenticationError(PermanentSourceError):
    """Credentials rejected. Aborts the whole source: other entities would fail the same way."""


class SchemaError(PermanentSourceError):
    """The observed source schema is incompatible with the field-mapping configuration."""


class RetryExhaustedError(PermanentSourceError):
    """All retry attempts for a transient failure were used up."""

    def __init__(self, message: str, *, attempts: int, last_error: BaseException) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.last_error = last_error


class QualityGateError(IngestionError):
    """Rejected-record ratio exceeded the configured quality gate."""


class ImmutabilityError(IngestionError):
    """An attempt to overwrite or modify committed Bronze data."""
