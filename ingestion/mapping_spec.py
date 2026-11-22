"""Field-mapping specification (YAML) -> validated pydantic models.

A mapping file describes, for *one source entity*, how raw source fields become canonical
Bronze fields: renaming, type coercion, null handling, code translation, constraints and
sensitivity labels. All behavior is data, so onboarding a new district or vendor means writing
YAML, not code.

Example::

    entity: students
    bronze_table: sis_students
    schema_version: "1.0.0"
    primary_key: [tenant_id, student_id]
    fields:
      - {source: StudentUniqueId, target: student_id, type: string, required: true}
      - {source: BirthDate, target: birth_date, type: date, formats: ["%m/%d/%Y"], required: true}
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .errors import ConfigError

FieldType = Literal["string", "integer", "float", "boolean", "date", "datetime"]
Sensitivity = Literal["public", "internal", "confidential", "restricted"]

#: Values treated as null (case-insensitive, after trimming) unless a mapping overrides them.
DEFAULT_NULL_VALUES: tuple[str, ...] = ("", "NULL", "N/A", "NA", "-")

_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


class FieldSpec(BaseModel):
    """How one canonical (target) field is produced."""

    model_config = ConfigDict(extra="forbid")

    target: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    #: source field name, or a list of aliases (first non-null wins)
    source: str | list[str] | None = None
    #: fixed value for every record (use with care; e.g. a marker column)
    constant: Any = None
    #: take the value from the source-level config (currently: ``tenant_id``)
    from_config: Literal["tenant_id"] | None = None

    type: FieldType = "string"
    required: bool = False
    #: used when the value is null; coerced with the same rules as real values
    default: Any = None
    #: overrides the mapping-level null token list for this field
    null_values: list[str] | None = None
    trim: bool = True
    case: Literal["upper", "lower"] | None = None
    #: strptime formats for date/datetime (tried in order)
    formats: list[str] | None = None
    true_values: list[str] | None = None
    false_values: list[str] | None = None
    allowed_values: list[str] | None = None
    #: translate source codes into canonical values, e.g. {"P": "PRESENT"}
    value_map: dict[str, str] | None = None
    pattern: str | None = None
    min: float | None = None
    max: float | None = None
    max_length: int | None = Field(default=None, ge=1)
    sensitivity: Sensitivity = "internal"
    description: str | None = None

    @model_validator(mode="after")
    def _exactly_one_origin(self) -> FieldSpec:
        has_constant = "constant" in self.model_fields_set
        origins = [self.source is not None, has_constant, self.from_config is not None]
        if sum(origins) != 1:
            raise ValueError(
                f"field '{self.target}': exactly one of source / constant / from_config is required"
            )
        if self.pattern is not None:
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise ValueError(f"field '{self.target}': invalid pattern ({exc})") from exc
        return self

    @property
    def aliases(self) -> list[str]:
        if self.source is None:
            return []
        return [self.source] if isinstance(self.source, str) else list(self.source)

    @property
    def has_constant(self) -> bool:
        return "constant" in self.model_fields_set


class MappingSpec(BaseModel):
    """Mapping for one source entity."""

    model_config = ConfigDict(extra="forbid")

    entity: str
    bronze_table: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    schema_version: str
    description: str | None = None
    primary_key: list[str] = Field(default_factory=list)
    #: canonical datetime field used as the incremental watermark
    incremental_field: str | None = None
    null_values: list[str] = Field(default_factory=lambda: list(DEFAULT_NULL_VALUES))
    #: what to do with source fields the mapping does not mention
    unknown_fields: Literal["warn", "ignore", "reject_entity"] = "warn"
    #: same key + different content inside one batch: keep the first, or reject the later rows
    duplicate_policy: Literal["keep_first", "reject"] = "keep_first"
    fields: list[FieldSpec]

    @model_validator(mode="after")
    def _consistent(self) -> MappingSpec:
        if not _SEMVER.match(self.schema_version):
            raise ValueError("schema_version must look like 1.0.0")
        targets = [f.target for f in self.fields]
        if len(targets) != len(set(targets)):
            raise ValueError("duplicate target field names")
        for key in self.primary_key:
            if key not in targets:
                raise ValueError(f"primary_key field '{key}' is not a target field")
        if self.incremental_field:
            spec = next((f for f in self.fields if f.target == self.incremental_field), None)
            if spec is None or spec.type != "datetime":
                raise ValueError("incremental_field must be a target field of type datetime")
        if "tenant_id" not in targets:
            raise ValueError("mapping must produce a tenant_id field (multi-tenant isolation)")
        return self

    @property
    def field_sensitivity(self) -> dict[str, str]:
        return {f.target: f.sensitivity for f in self.fields}

    @property
    def expected_source_fields(self) -> set[str]:
        return {alias for f in self.fields for alias in f.aliases}

    @property
    def targets(self) -> list[str]:
        return [f.target for f in self.fields]


def load_mapping(path: str | Path) -> MappingSpec:
    """Load and validate a mapping YAML file (``yaml.safe_load`` only)."""
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read mapping file {path}: {exc.strerror or exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"mapping file {path} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"mapping file {path} must contain a YAML mapping at top level")
    try:
        return MappingSpec.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"invalid mapping {path}:\n{format_validation_error(exc)}") from exc


def format_validation_error(exc: ValidationError) -> str:
    """Readable, value-free summary of a pydantic error (locations and messages only)."""
    lines = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "<root>"
        lines.append(f"  - {loc}: {err['msg']}")
    return "\n".join(lines)
