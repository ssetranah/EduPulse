"""Declarative description of every synthetic source extract.

The generator emits data shaped like the *source systems* EduPulse will ingest (SIS, LMS,
assessment, grading, attendance, intervention tracking), each in the file format such a system
would typically export. Keeping the layout in one declarative table means the data dictionary,
writers, validator and (later) ingestion-adapter configs all read from a single definition.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EntitySpec:
    source_system: str
    name: str
    fmt: str  # "csv" or "jsonl"
    fields: tuple[str, ...]
    description: str

    @property
    def key(self) -> str:
        return f"{self.source_system}/{self.name}"

    @property
    def relative_path(self) -> str:
        return f"{self.source_system}/{self.name}.{self.fmt}"


_MARK = "synthetic_marker"

SCHOOLS = EntitySpec(
    "sis",
    "schools",
    "csv",
    (
        "tenant_id",
        "school_id",
        "school_name",
        "school_type",
        "lowest_grade",
        "highest_grade",
        "city",
        "state_code",
        "postal_code",
        _MARK,
    ),
    "Fictional schools per tenant (district).",
)
STUDENTS = EntitySpec(
    "sis",
    "students",
    "csv",
    (
        "tenant_id",
        "school_id",
        "student_id",
        "state_student_id",
        "first_name",
        "last_name",
        "birth_date",
        "grade_level",
        "gender",
        "race_ethnicity",
        "english_learner",
        "special_education",
        "economically_disadvantaged",
        "enrollment_start_date",
        "enrollment_status",
        _MARK,
    ),
    "Fictional students. Includes sensitive demographic/program fields on purpose so that "
    "field-level sensitivity handling can be exercised later.",
)
ASSESSMENT_DEFINITIONS = EntitySpec(
    "assessment",
    "assessment_definitions",
    "csv",
    (
        "tenant_id",
        "assessment_id",
        "assessment_name",
        "subject",
        "administration_window",
        "window_start",
        "window_end",
        "scale_min",
        "scale_max",
        _MARK,
    ),
    "Interim assessment definitions (subject x administration window).",
)
ASSESSMENT_RESULTS = EntitySpec(
    "assessment",
    "assessment_results",
    "csv",
    (
        "tenant_id",
        "school_id",
        "student_id",
        "result_id",
        "assessment_id",
        "administered_date",
        "grade_level",
        "completion_status",
        "scale_score",
        "proficiency_band",
        _MARK,
    ),
    "One row per student per assessment, including non-completions.",
)
ATTENDANCE = EntitySpec(
    "attendance",
    "attendance_daily",
    "csv",
    (
        "tenant_id",
        "school_id",
        "student_id",
        "attendance_event_id",
        "attendance_date",
        "attendance_status",
        _MARK,
    ),
    "Daily attendance status per student.",
)
GRADES = EntitySpec(
    "grading",
    "grades",
    "jsonl",
    (
        "tenant_id",
        "school_id",
        "student_id",
        "grade_record_id",
        "course_code",
        "course_title",
        "reporting_period",
        "numeric_grade",
        "letter_grade",
        "posted_at",
        _MARK,
    ),
    "Cumulative course grades per reporting period (JSON Lines export).",
)
LMS_ACTIVITY = EntitySpec(
    "lms",
    "lms_activity",
    "jsonl",
    (
        "tenant_id",
        "school_id",
        "student_id",
        "lms_event_id",
        "course_code",
        "event_type",
        "assignment_id",
        "occurred_at",
        "duration_seconds",
        _MARK,
    ),
    "LMS activity events (JSON Lines export).",
)
INTERVENTIONS = EntitySpec(
    "intervention",
    "interventions",
    "csv",
    (
        "tenant_id",
        "school_id",
        "student_id",
        "intervention_id",
        "intervention_type",
        "subject_focus",
        "tier",
        "start_date",
        "end_date",
        "status",
        "minutes_per_week",
        _MARK,
    ),
    "Academic/attendance intervention assignments.",
)

#: Every entity the generator produces, in write order.
ENTITIES: tuple[EntitySpec, ...] = (
    SCHOOLS,
    STUDENTS,
    ASSESSMENT_DEFINITIONS,
    ASSESSMENT_RESULTS,
    ATTENDANCE,
    GRADES,
    LMS_ACTIVITY,
    INTERVENTIONS,
)

ENTITIES_BY_KEY: dict[str, EntitySpec] = {spec.key: spec for spec in ENTITIES}

#: Entities whose rows reference a student (used for referential-integrity checks).
STUDENT_CHILD_KEYS: tuple[str, ...] = tuple(
    spec.key for spec in ENTITIES if "student_id" in spec.fields and spec is not STUDENTS
)
