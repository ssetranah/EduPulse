"""Configuration for the synthetic-data generator.

Everything that shapes the output is a field here -- nothing is hard-coded per school or per
tenant -- so the same generator can produce a one-school smoke dataset or a multi-tenant load
test. Configuration can be supplied from the CLI or from a JSON file (see ``from_mapping``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from typing import Any


@dataclass(frozen=True)
class GeneratorConfig:
    """Parameters controlling the volume, shape and determinism of generated data."""

    #: Master seed. Same config + same seed => byte-identical output.
    seed: int = 20260101
    #: Number of fictional districts ("tenants"). Used to exercise multi-tenant isolation.
    tenants: int = 2
    #: Schools per tenant. Schools cycle through elementary / middle / high grade bands.
    schools_per_tenant: int = 3
    #: Students enrolled per school.
    students_per_school: int = 60
    #: First calendar year of the school year, e.g. 2025 => 2025-2026.
    school_year_start: int = 2025
    #: Number of consecutive weekdays of attendance to generate, starting 1 September.
    attendance_days: int = 30
    #: Average LMS events per student (individual students vary around this).
    lms_events_per_student: int = 20
    #: Fraction (0..0.2) of records to corrupt on purpose (duplicates, missing scores) so that
    #: data-quality tests have something real to catch. 0 means a clean dataset.
    defect_rate: float = 0.0

    @property
    def school_year(self) -> str:
        return f"{self.school_year_start}-{self.school_year_start + 1}"

    def validate(self) -> None:
        """Raise ``ValueError`` if any parameter is outside its supported range."""
        bounds = {
            "tenants": (1, 50),
            "schools_per_tenant": (1, 20),
            "students_per_school": (1, 5000),
            "attendance_days": (1, 200),
            "lms_events_per_student": (0, 500),
            "school_year_start": (2000, 2100),
        }
        for name, (low, high) in bounds.items():
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
                raise ValueError(f"{name} must be an integer in [{low}, {high}], got {value!r}")
        if not isinstance(self.seed, int) or isinstance(self.seed, bool):
            raise ValueError(f"seed must be an integer, got {self.seed!r}")
        if not 0.0 <= self.defect_rate <= 0.2:
            raise ValueError(f"defect_rate must be within [0, 0.2], got {self.defect_rate!r}")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> GeneratorConfig:
        """Build a config from a mapping (e.g. parsed JSON), rejecting unknown keys."""
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValueError(f"unknown config keys: {', '.join(unknown)}")
        config = cls(**data)
        config.validate()
        return config
