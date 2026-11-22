"""Source-*native* views of the synthetic data, for the mock connectors and the mock REST server.

Phase 1 generates canonical-looking extracts. Real source systems never look like that: they use
their own field names, date formats, flag/code vocabularies and timestamps. This module renders
the same synthetic records the way four fictional vendor systems would expose them:

=================== ==================================================================
system              native quirks
=================== ==================================================================
SIS                 Ed-Fi-style names, ``MM/DD/YYYY`` dates, ``Y``/``N`` flags, gender codes
LMS                 ``TenantKey``/``LearnerId`` names, numeric seconds, nullable assignment ref
ASSESSMENT          ``C``/``NC``/``EX`` status codes, ``"N/A"`` for missing scores, title-case bands
ATTENDANCE_INTERVENTION  ``YYYYMMDD`` dates, ``P``/``AE``/``AU``/``T`` codes, numeric tiers
=================== ==================================================================

Time model (deterministic): every record gets a *modified timestamp* spread evenly over a
10-day window starting 2026-01-01T00:00:00Z. About 4% of records of mutable entities also have a
*later version* (3 days after creation) with one changed field. ``as_of`` is the source system's
"now": versions modified after it are invisible. Running an ingestion at an early ``as_of`` and
again at a later one therefore exercises incremental loading, updates and idempotency.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .generator import GeneratedData

Row = dict[str, Any]

BASE_TS = datetime(2026, 1, 1, tzinfo=UTC)
SPAN_SECONDS = 10 * 24 * 3600
UPDATE_DELAY = timedelta(days=3)
UPDATE_MODULUS = 25  # ~4% of mutable records get a second version


def _iso_z(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def _naive(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def _mdy(iso_date: str) -> str:
    year, month, day = iso_date.split("-")
    return f"{month}/{day}/{year}"


def _compact(iso_date: str) -> str:
    return iso_date.replace("-", "")


def _yn(flag: bool) -> str:
    return "Y" if flag else "N"


# ----------------------------------------------------------------------------- converters
def _schools(r: Row, ts: datetime) -> Row:
    return {
        "DistrictId": r["tenant_id"],
        "SchoolId": r["school_id"],
        "SchoolName": r["school_name"],
        "SchoolTypeDescriptor": r["school_type"],
        "LowestGradeLevel": r["lowest_grade"],
        "HighestGradeLevel": r["highest_grade"],
        "City": r["city"],
        "StateAbbreviation": r["state_code"],
        "PostalCode": r["postal_code"],
        "SyntheticMarker": r["synthetic_marker"],
        "LastModifiedDate": _iso_z(ts),
    }


_GENDER = {"FEMALE": "F", "MALE": "M", "NON_BINARY": "X"}


def _students(r: Row, ts: datetime) -> Row:
    return {
        "DistrictId": r["tenant_id"],
        "SchoolId": r["school_id"],
        "StudentUniqueId": r["student_id"],
        "StateStudentId": r["state_student_id"],
        "FirstName": r["first_name"],
        "LastSurname": r["last_name"],
        "BirthDate": _mdy(r["birth_date"]),
        "GradeLevelDescriptor": r["grade_level"],
        "GenderDescriptor": _GENDER[r["gender"]],
        "RaceDescriptor": r["race_ethnicity"],
        "EnglishLearnerFlag": _yn(r["english_learner"]),
        "SpecialEducationFlag": _yn(r["special_education"]),
        "EconomicDisadvantageFlag": _yn(r["economically_disadvantaged"]),
        "EntryDate": _mdy(r["enrollment_start_date"]),
        "EnrollmentStatus": r["enrollment_status"].title(),
        "SyntheticMarker": r["synthetic_marker"],
        "LastModifiedDate": _iso_z(ts),
    }


def _students_mutate(r: Row) -> Row | None:
    return {**r, "enrollment_status": "WITHDRAWN"}


def _lms(r: Row, ts: datetime) -> Row:
    return {
        "TenantKey": r["tenant_id"],
        "BuildingId": r["school_id"],
        "LearnerId": r["student_id"],
        "EventId": r["lms_event_id"],
        "CourseCode": r["course_code"],
        "ActivityType": r["event_type"],
        "AssignmentRef": r["assignment_id"],
        "EventTimestamp": r["occurred_at"],
        "SecondsSpent": r["duration_seconds"],
        "SyntheticMarker": r["synthetic_marker"],
        "ModifiedAt": _iso_z(ts),
    }


def _assess_defs(r: Row, ts: datetime) -> Row:
    return {
        "TenantCode": r["tenant_id"],
        "AssessmentCode": r["assessment_id"],
        "AssessmentTitle": r["assessment_name"],
        "SubjectArea": r["subject"],
        "Window": r["administration_window"],
        "WindowOpens": _mdy(r["window_start"]),
        "WindowCloses": _mdy(r["window_end"]),
        "MinScale": r["scale_min"],
        "MaxScale": r["scale_max"],
        "SyntheticMarker": r["synthetic_marker"],
        "UpdatedAt": _iso_z(ts),
    }


_STATUS = {"COMPLETED": "C", "NOT_COMPLETED": "NC", "EXCUSED": "EX"}
_BAND = {
    "BELOW_BASIC": "Below Basic",
    "BASIC": "Basic",
    "PROFICIENT": "Proficient",
    "ADVANCED": "Advanced",
}


def _assess_results(r: Row, ts: datetime) -> Row:
    score = r["scale_score"]
    return {
        "TenantCode": r["tenant_id"],
        "SchoolCode": r["school_id"],
        "StudentRef": r["student_id"],
        "ResultId": r["result_id"],
        "AssessmentCode": r["assessment_id"],
        "TestDate": _mdy(r["administered_date"]),
        "GradeLevel": r["grade_level"],
        "Status": _STATUS[r["completion_status"]],
        "ScaleScore": "N/A" if score is None else str(score),
        "PerformanceLevel": _BAND.get(r["proficiency_band"], ""),
        "SyntheticMarker": r["synthetic_marker"],
        "UpdatedAt": _iso_z(ts),
    }


def _assess_results_mutate(r: Row) -> Row | None:
    if r["scale_score"] is None:
        return None
    return {**r, "scale_score": min(800, r["scale_score"] + 5)}


_ATT_CODE = {"PRESENT": "P", "ABSENT_EXCUSED": "AE", "ABSENT_UNEXCUSED": "AU", "TARDY": "T"}


def _attendance(r: Row, ts: datetime) -> Row:
    return {
        "Tenant": r["tenant_id"],
        "School": r["school_id"],
        "Student": r["student_id"],
        "AttendanceId": r["attendance_event_id"],
        "Date": _compact(r["attendance_date"]),
        "Code": _ATT_CODE[r["attendance_status"]],
        "SyntheticMarker": r["synthetic_marker"],
        "RecordedAt": _naive(ts),
    }


def _attendance_mutate(r: Row) -> Row | None:
    return {**r, "attendance_status": "TARDY"} if r["attendance_status"] == "PRESENT" else None


def _interventions(r: Row, ts: datetime) -> Row:
    return {
        "Tenant": r["tenant_id"],
        "School": r["school_id"],
        "Student": r["student_id"],
        "InterventionId": r["intervention_id"],
        "Program": r["intervention_type"],
        "Focus": r["subject_focus"],
        "Tier": r["tier"].split("_")[1],
        "BeginDate": r["start_date"],
        "EndDate": r["end_date"] or "",
        "State": r["status"].title(),
        "MinutesWeekly": str(r["minutes_per_week"]),
        "SyntheticMarker": r["synthetic_marker"],
        "Updated": _iso_z(ts),
    }


def _interventions_mutate(r: Row) -> Row | None:
    if r["status"] != "ACTIVE":
        return None
    start = datetime.fromisoformat(r["start_date"])
    return {**r, "status": "COMPLETED", "end_date": (start + timedelta(days=30)).date().isoformat()}


# ----------------------------------------------------------------------------- registry
@dataclass(frozen=True)
class NativeSpec:
    system: str
    entity: str
    table_key: str  # key into GeneratedData.tables
    id_field: str  # canonical field identifying a record (with tenant_id) for version selection
    modified_field: str  # native name of the modification timestamp
    to_native: Callable[[Row, datetime], Row]
    mutate: Callable[[Row], Row | None] | None
    #: native field names used by deliberate-fault injection (see mock connectors)
    fault_id: str
    fault_date: str | None
    fault_code: str | None


_SPECS = [
    NativeSpec("SIS", "schools", "sis/schools", "school_id", "LastModifiedDate",
               _schools, None, "SchoolId", None, "SchoolTypeDescriptor"),
    NativeSpec("SIS", "students", "sis/students", "student_id", "LastModifiedDate",
               _students, _students_mutate, "StudentUniqueId", "BirthDate", "GenderDescriptor"),
    NativeSpec("LMS", "lms_activity", "lms/lms_activity", "lms_event_id", "ModifiedAt",
               _lms, None, "EventId", "EventTimestamp", "ActivityType"),
    NativeSpec("ASSESSMENT", "assessment_definitions", "assessment/assessment_definitions",
               "assessment_id", "UpdatedAt", _assess_defs, None,
               "AssessmentCode", "WindowOpens", "SubjectArea"),
    NativeSpec("ASSESSMENT", "assessment_results", "assessment/assessment_results", "result_id",
               "UpdatedAt", _assess_results, _assess_results_mutate,
               "ResultId", "TestDate", "Status"),
    NativeSpec("ATTENDANCE_INTERVENTION", "attendance_daily", "attendance/attendance_daily",
               "attendance_event_id", "RecordedAt", _attendance, _attendance_mutate,
               "AttendanceId", "Date", "Code"),
    NativeSpec("ATTENDANCE_INTERVENTION", "interventions", "intervention/interventions",
               "intervention_id", "Updated", _interventions, _interventions_mutate,
               "InterventionId", "BeginDate", "State"),
]  # fmt: skip

NATIVE_SPECS: dict[tuple[str, str], NativeSpec] = {(s.system, s.entity): s for s in _SPECS}

SYSTEM_ENTITIES: dict[str, list[str]] = {}
for _s in _SPECS:
    SYSTEM_ENTITIES.setdefault(_s.system, []).append(_s.entity)


def native_spec_for_entity(entity: str) -> NativeSpec:
    """Entity names are unique across systems; used by the mock REST server's routing."""
    for spec in _SPECS:
        if spec.entity == entity:
            return spec
    raise KeyError(entity)


def _selected_for_update(spec: NativeSpec, row: Row) -> bool:
    key = f"{row['tenant_id']}:{row[spec.id_field]}".encode()
    return int(hashlib.sha256(key).hexdigest()[:8], 16) % UPDATE_MODULUS == 0


def build_native_records(
    data: GeneratedData, system: str, entity: str, *, as_of: datetime | None = None
) -> list[tuple[datetime, Row]]:
    """Current visible version of every record, as ``(modified_ts, native_record)`` sorted by time.

    ``as_of=None`` means "everything, latest versions". Deterministic for a given dataset.
    """
    spec = NATIVE_SPECS[(system, entity)]
    rows = data.tables[spec.table_key]
    count = max(len(rows), 1)
    out: list[tuple[datetime, Row]] = []
    for index, row in enumerate(rows):
        created = BASE_TS + timedelta(seconds=(index * SPAN_SECONDS) // count)
        chosen_row, chosen_ts = row, created
        if spec.mutate is not None and _selected_for_update(spec, row):
            mutated = spec.mutate(row)
            if mutated is not None:
                updated = created + UPDATE_DELAY
                if as_of is None or updated <= as_of:
                    chosen_row, chosen_ts = mutated, updated
        if as_of is not None and created > as_of:
            continue
        out.append((chosen_ts, spec.to_native(chosen_row, chosen_ts)))
    out.sort(key=lambda item: item[0])
    return out
