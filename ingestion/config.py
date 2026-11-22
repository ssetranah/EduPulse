"""Ingestion configuration (YAML) -> validated models.

One YAML file defines storage, defaults and any number of named *sources*. Each source has a
``type`` (which connector to use), source-system metadata, retry/quality settings and a list of
entities, each pointing at a field-mapping file.

Path rules: ``mapping`` paths resolve relative to the config file; data paths (``drop_dir``,
``storage.root``) resolve relative to the current working directory. Secrets never appear in
config: authentication refers to an *environment variable name*.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .constants import SYNTHETIC_MARKER_FIELD
from .errors import ConfigError
from .mapping_spec import MappingSpec, format_validation_error, load_mapping


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RetryConfig(_Strict):
    max_attempts: int = Field(default=4, ge=1, le=10)
    base_delay_seconds: float = Field(default=0.5, ge=0)
    max_delay_seconds: float = Field(default=30.0, ge=0)
    multiplier: float = Field(default=2.0, ge=1.0)
    jitter: bool = True


class QualityGateConfig(_Strict):
    #: fail the entity (nothing loaded) when rejected/received exceeds this ratio ...
    max_reject_ratio: float = Field(default=0.10, ge=0.0, le=1.0)
    #: ... but only once at least this many records were received (tiny batches are noisy)
    min_records: int = Field(default=10, ge=1)


class SourceSystem(_Strict):
    """Source-system metadata recorded in Bronze, reports and lineage."""

    system: str
    vendor: str | None = None
    product: str | None = None
    version: str | None = None
    description: str | None = None


class StorageConfig(_Strict):
    root: str = "data/ingestion"


class Defaults(_Strict):
    retry: RetryConfig = Field(default_factory=RetryConfig)
    quality_gate: QualityGateConfig = Field(default_factory=QualityGateConfig)


# --------------------------------------------------------------------------- entities
class EntityConfig(_Strict):
    name: str
    mapping: str
    enabled: bool = True
    #: ``fail_entity``: one bad file/stream fails the entity batch; ``skip_stream``: skip it
    stream_error_policy: Literal["fail_entity", "skip_stream"] = "fail_entity"


class CsvOptions(_Strict):
    delimiter: str = Field(default=",", min_length=1, max_length=1)
    encoding: str = "utf-8-sig"  # tolerates a BOM from Excel exports
    quotechar: str = Field(default='"', min_length=1, max_length=1)


class ExcelOptions(_Strict):
    sheet: str | int = 0  # name or zero-based index
    header_row: int = Field(default=1, ge=1)


class FileDropEntity(EntityConfig):
    file_pattern: str
    format: Literal["csv", "excel", "jsonl", "json", "auto"] = "auto"
    csv: CsvOptions = Field(default_factory=CsvOptions)
    excel: ExcelOptions = Field(default_factory=ExcelOptions)
    #: for ``json`` files: dotted path to the record list (default: top-level array)
    json_records_path: str | None = None


class PaginationConfig(_Strict):
    type: Literal["none", "page", "cursor"] = "page"
    page_param: str = "page"
    size_param: str = "page_size"
    page_size: int = Field(default=100, ge=1, le=10000)
    start_page: int = Field(default=1, ge=0)
    cursor_param: str = "cursor"
    cursor_path: str = "next_cursor"


class RestEntity(EntityConfig):
    path: str
    #: dotted path to the record list inside the JSON body (``null`` => body is the list)
    records_path: str | None = "data"
    pagination: PaginationConfig = Field(default_factory=PaginationConfig)
    #: query parameter that receives the incremental watermark (e.g. ``updated_since``)
    since_param: str | None = None
    query: dict[str, str] = Field(default_factory=dict)


# --------------------------------------------------------------------------- sources
class _SourceBase(_Strict):
    system: SourceSystem
    tenant_id: str | None = None
    #: ``synthetic`` sources must carry the SYNTHETIC_TEST_DATA marker on every record
    data_origin: Literal["synthetic", "production"] = "synthetic"
    enabled: bool = True
    retry: RetryConfig | None = None
    quality_gate: QualityGateConfig | None = None


class FileDropSource(_SourceBase):
    type: Literal["file_drop"]
    drop_dir: str
    max_file_mb: int = Field(default=200, ge=1)
    entities: list[FileDropEntity]


class AuthConfig(_Strict):
    type: Literal["none", "bearer", "api_key_header"] = "none"
    #: NAME of the environment variable holding the secret (never the secret itself)
    token_env: str | None = None
    header_name: str = "X-API-Key"

    @model_validator(mode="after")
    def _needs_env(self) -> AuthConfig:
        if self.type != "none" and not self.token_env:
            raise ValueError("auth.token_env is required when auth.type is not 'none'")
        return self


class RestApiSource(_SourceBase):
    type: Literal["rest_api"]
    base_url: str
    auth: AuthConfig = Field(default_factory=AuthConfig)
    timeout_seconds: float = Field(default=30.0, gt=0)
    #: https is required unless the host is loopback or this is set
    allow_insecure_http: bool = False
    allowed_hosts: list[str] | None = None
    max_pages: int = Field(default=10000, ge=1)
    max_response_mb: int = Field(default=50, ge=1)
    entities: list[RestEntity]


class FaultConfig(_Strict):
    """Deterministic fault injection for mock connectors (tests and demos only)."""

    fail_first_n_calls: int = Field(default=0, ge=0)
    bad_row_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    duplicate_row_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    drop_fields: list[str] = Field(default_factory=list)
    add_fields: list[str] = Field(default_factory=list)


class MockOptions(_Strict):
    seed: int = 20260101
    tenants: int = Field(default=2, ge=1, le=50)
    schools_per_tenant: int = Field(default=3, ge=1, le=20)
    students_per_school: int = Field(default=60, ge=1, le=5000)
    attendance_days: int = Field(default=30, ge=1, le=200)
    lms_events_per_student: int = Field(default=20, ge=0, le=500)
    page_size: int = Field(default=500, ge=1, le=10000)
    #: the source system's "now"; records modified after this are not yet visible
    as_of: datetime | None = None
    faults: FaultConfig = Field(default_factory=FaultConfig)


class MockSource(_SourceBase):
    type: Literal["mock_sis", "mock_lms", "mock_assessment", "mock_attendance_intervention"]
    mock: MockOptions = Field(default_factory=MockOptions)
    entities: list[EntityConfig]


SourceConfig = Annotated[
    FileDropSource | RestApiSource | MockSource,
    Field(discriminator="type"),
]


class IngestionConfig(_Strict):
    version: int = 1
    storage: StorageConfig = Field(default_factory=StorageConfig)
    defaults: Defaults = Field(default_factory=Defaults)
    sources: dict[str, SourceConfig]

    # populated by load_config(); not part of the YAML
    base_dir: Path = Field(default=Path("."), exclude=True)

    def source(self, name: str) -> Any:
        try:
            return self.sources[name]
        except KeyError:
            known = ", ".join(sorted(self.sources)) or "(none)"
            raise ConfigError(f"unknown source '{name}'. Known sources: {known}") from None

    def retry_for(self, name: str) -> RetryConfig:
        return self.source(name).retry or self.defaults.retry

    def gate_for(self, name: str) -> QualityGateConfig:
        return self.source(name).quality_gate or self.defaults.quality_gate

    def mapping_path(self, entity: EntityConfig) -> Path:
        path = Path(entity.mapping)
        return path if path.is_absolute() else (self.base_dir / path)

    def load_mapping(self, entity: EntityConfig) -> MappingSpec:
        return load_mapping(self.mapping_path(entity))


def _apply_overrides(raw: dict[str, Any], overrides: list[str]) -> None:
    """Apply ``dotted.path=value`` overrides (value parsed as YAML) to the raw config."""
    for item in overrides:
        if "=" not in item:
            raise ConfigError(f"override '{item}' must look like dotted.path=value")
        dotted, _, value = item.partition("=")
        node: Any = raw
        parts = dotted.split(".")
        for part in parts[:-1]:
            if not isinstance(node, dict) or part not in node:
                raise ConfigError(f"override path '{dotted}' does not exist in the config")
            node = node[part]
        if not isinstance(node, dict):
            raise ConfigError(f"override path '{dotted}' does not exist in the config")
        node[parts[-1]] = yaml.safe_load(value)


def load_config(
    path: str | Path, *, overrides: list[str] | None = None, check_mappings: bool = True
) -> IngestionConfig:
    """Load, validate and cross-check an ingestion config file."""
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read config {path}: {exc.strerror or exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"config {path} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"config {path} must contain a YAML mapping at top level")
    _apply_overrides(raw, overrides or [])
    try:
        config = IngestionConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"invalid config {path}:\n{format_validation_error(exc)}") from exc
    config.base_dir = path.resolve().parent
    _cross_check(config, check_mappings=check_mappings)
    return config


def _cross_check(config: IngestionConfig, *, check_mappings: bool) -> None:
    for name, source in config.sources.items():
        names = [e.name for e in source.entities]
        if len(names) != len(set(names)):
            raise ConfigError(f"source '{name}': duplicate entity names")
        if not check_mappings:
            continue
        for entity in source.entities:
            mapping = config.load_mapping(entity)
            if mapping.entity != entity.name:
                raise ConfigError(
                    f"source '{name}': entity '{entity.name}' points at a mapping for "
                    f"'{mapping.entity}'"
                )
            if source.data_origin == "synthetic":
                marker = next(
                    (f for f in mapping.fields if f.target == SYNTHETIC_MARKER_FIELD), None
                )
                if marker is None:
                    raise ConfigError(
                        f"source '{name}' is synthetic, so mapping for '{entity.name}' must "
                        f"produce a '{SYNTHETIC_MARKER_FIELD}' field"
                    )
            if source.tenant_id is None and any(
                f.from_config == "tenant_id" for f in mapping.fields
            ):
                raise ConfigError(
                    f"source '{name}': mapping for '{entity.name}' uses from_config: tenant_id "
                    "but the source has no tenant_id"
                )
