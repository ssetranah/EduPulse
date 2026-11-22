"""Deterministic generator of clearly fictional K-12 source-system extracts.

Design notes
------------
* **Fictional by construction.** Surnames are assembled from invented syllables, schools carry a
  "(Synthetic)" suffix, IDs are prefixed ``SYN-``, geography uses the non-existent state ``ZZ``
  and postal code ``00000``, and *no* e-mail addresses, phone numbers or SSNs are generated.
  Any resemblance to a real person is coincidental. Every row carries ``synthetic_marker``.
* **Deterministic.** All randomness flows from ``GeneratorConfig.seed`` through per-tenant,
  per-entity ``random.Random`` instances, so output is reproducible and diff-able.
* **Plausible, not realistic.** Each student has latent ELA/Math ability and an attendance
  propensity that correlate across entities (low scores <-> more interventions, etc.) so that
  later analytics phases have signal to find. This is test data, not a statistical model of any
  real population, and the demographic fields are drawn independently and carry no meaning.
* **No business logic.** Proficiency bands, letter grades etc. are emitted the way a source
  system would emit them. EduPulse's own definitions belong to Silver/Gold, not here.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from .config import GeneratorConfig
from .constants import FAKE_POSTAL_CODE, FAKE_STATE_CODE, SYNTHETIC_MARKER
from .spec import (
    ASSESSMENT_DEFINITIONS,
    ASSESSMENT_RESULTS,
    ATTENDANCE,
    ENTITIES,
    GRADES,
    INTERVENTIONS,
    LMS_ACTIVITY,
    SCHOOLS,
    STUDENTS,
)

Row = dict[str, Any]

GRADE_LEVELS = ["KG", *[f"{n:02d}" for n in range(1, 13)]]

#: (school type, grade band) cycled across the schools of a tenant.
SCHOOL_BANDS = [
    ("ELEMENTARY", "Elementary", GRADE_LEVELS[0:6]),  # KG-05
    ("MIDDLE", "Middle", GRADE_LEVELS[6:9]),  # 06-08
    ("HIGH", "High", GRADE_LEVELS[9:13]),  # 09-12
]

_PLACE_NAMES = [
    "Maple Hollow",
    "Cedar Ridge",
    "Brightwater",
    "Lantern Hill",
    "Quillfeather",
    "Stonebridge",
    "Larkspur",
    "Foxglove",
    "Ambergate",
    "Willowmere",
]
_FIRST_NAMES = [
    "Ada", "Bram", "Cleo", "Dara", "Eli", "Fern", "Gus", "Hana", "Ira", "Jules",
    "Kiri", "Leo", "Mina", "Nico", "Odette", "Pax", "Quinn", "Rhea", "Sol", "Tamsin",
    "Uri", "Vera", "Wren", "Xan", "Yara", "Zane", "Alder", "Briar", "Corin", "Dell",
]  # fmt: skip
_SURNAME_PREFIXES = [
    "Ar", "Bel", "Cor", "Dal", "Em", "Fen", "Gar", "Hal", "Iv", "Jor", "Kes", "Lun", "Mor",
    "Nor", "Or", "Pel", "Quin", "Ros", "Sel", "Tor", "Ul", "Var", "Wen", "Yar", "Zel",
]  # fmt: skip
_SURNAME_SUFFIXES = [
    "wick", "mere", "ford", "stone", "lund", "vale", "brook", "haven", "croft", "dell",
]  # fmt: skip
_GENDERS = ["FEMALE", "MALE", "NON_BINARY"]
_RACE_ETHNICITY = [
    "AMERICAN_INDIAN_OR_ALASKA_NATIVE",
    "ASIAN",
    "BLACK_OR_AFRICAN_AMERICAN",
    "HISPANIC_OR_LATINO",
    "NATIVE_HAWAIIAN_OR_PACIFIC_ISLANDER",
    "WHITE",
    "TWO_OR_MORE_RACES",
]
_COURSES = [
    ("ELA", "English Language Arts"),
    ("MATH", "Mathematics"),
    ("SCI", "Science"),
    ("SOC", "Social Studies"),
]
_LMS_EVENT_TYPES = [
    ("LOGIN", 0.20),
    ("RESOURCE_VIEWED", 0.30),
    ("ASSIGNMENT_VIEWED", 0.20),
    ("ASSIGNMENT_SUBMITTED", 0.20),
    ("QUIZ_COMPLETED", 0.10),
]
_PERIODS = ["Q1", "Q2"]
_ASSESSMENT_SUBJECTS = ["ELA", "MATH"]


@dataclass
class _Student:
    """A synthetic student plus the hidden traits that drive correlated behavior."""

    row: Row
    grade_index: int
    ability_ela: float
    ability_math: float
    chronic_absence_prone: bool


@dataclass
class GeneratedData:
    """Result of a generation run: rows per entity key plus a summary of injected defects."""

    tables: dict[str, list[Row]]
    config: GeneratorConfig
    defects: dict[str, int] = field(default_factory=dict)

    def row_counts(self) -> dict[str, int]:
        return {key: len(rows) for key, rows in self.tables.items()}


def _rng(config: GeneratorConfig, *parts: object) -> random.Random:
    """Independent, reproducible RNG stream for ``(seed, *parts)``."""
    return random.Random(":".join(str(p) for p in (config.seed, *parts)))


def school_days(start_year: int, count: int) -> list[date]:
    """First ``count`` weekdays on/after 1 September of ``start_year`` (holidays not modeled)."""
    days: list[date] = []
    current = date(start_year, 9, 1)
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _letter(pct: float) -> str:
    for cutoff, letter in ((90, "A"), (80, "B"), (70, "C"), (60, "D")):
        if pct >= cutoff:
            return letter
    return "F"


def _assessment_ids(config: GeneratorConfig) -> list[tuple[str, str, str, date, date]]:
    """(assessment_id, subject, window, window_start, window_end) for the year."""
    boy = date(config.school_year_start, 9, 15)
    moy = date(config.school_year_start + 1, 1, 20)
    out = []
    for window, start in (("BOY", boy), ("MOY", moy)):
        for subject in _ASSESSMENT_SUBJECTS:
            aid = f"SYN-ASM-{subject}-{window}-{config.school_year_start}"
            out.append((aid, subject, window, start, start + timedelta(days=9)))
    return out


def _make_surname(rng: random.Random) -> str:
    return rng.choice(_SURNAME_PREFIXES) + rng.choice(_SURNAME_SUFFIXES)


def _generate_schools(config: GeneratorConfig, tenant_id: str, t: int) -> list[Row]:
    rng = _rng(config, tenant_id, "schools")
    places = _PLACE_NAMES[:]
    rng.shuffle(places)
    rows = []
    for s in range(1, config.schools_per_tenant + 1):
        school_type, label, grades = SCHOOL_BANDS[(s - 1) % len(SCHOOL_BANDS)]
        place = places[(s - 1) % len(places)]
        rows.append(
            {
                "tenant_id": tenant_id,
                "school_id": f"SYN-T{t:02d}-SC{s:02d}",
                "school_name": f"{place} {label} School (Synthetic)",
                "school_type": school_type,
                "lowest_grade": grades[0],
                "highest_grade": grades[-1],
                "city": "Synthville",
                "state_code": FAKE_STATE_CODE,
                "postal_code": FAKE_POSTAL_CODE,
                "synthetic_marker": SYNTHETIC_MARKER,
            }
        )
    return rows


def _generate_students(
    config: GeneratorConfig, tenant_id: str, t: int, schools: list[Row]
) -> list[_Student]:
    rng = _rng(config, tenant_id, "students")
    students: list[_Student] = []
    counter = 0
    for school in schools:
        band = next(b for b in SCHOOL_BANDS if b[0] == school["school_type"])[2]
        for _ in range(config.students_per_school):
            counter += 1
            grade = rng.choice(band)
            gi = GRADE_LEVELS.index(grade)
            # Kindergarten is ~age 5 at the start of the school year.
            birth_year = config.school_year_start - (5 + gi)
            birth = date(birth_year, rng.randint(1, 12), rng.randint(1, 28))
            common = rng.gauss(0, 1)
            row: Row = {
                "tenant_id": tenant_id,
                "school_id": school["school_id"],
                "student_id": f"SYN-T{t:02d}-ST{counter:05d}",
                "state_student_id": f"SYN{t:02d}{counter:07d}",
                "first_name": rng.choice(_FIRST_NAMES),
                "last_name": _make_surname(rng),
                "birth_date": birth.isoformat(),
                "grade_level": grade,
                "gender": rng.choice(_GENDERS),
                "race_ethnicity": rng.choice(_RACE_ETHNICITY),
                "english_learner": rng.random() < 0.12,
                "special_education": rng.random() < 0.13,
                "economically_disadvantaged": rng.random() < 0.45,
                "enrollment_start_date": date(config.school_year_start, 8, 25).isoformat(),
                "enrollment_status": "ACTIVE",
                "synthetic_marker": SYNTHETIC_MARKER,
            }
            students.append(
                _Student(
                    row=row,
                    grade_index=gi,
                    ability_ela=0.7 * common + 0.714 * rng.gauss(0, 1),
                    ability_math=0.7 * common + 0.714 * rng.gauss(0, 1),
                    chronic_absence_prone=rng.random() < 0.10,
                )
            )
    return students


def _generate_assessments(
    config: GeneratorConfig, tenant_id: str, students: list[_Student]
) -> tuple[list[Row], list[Row]]:
    rng = _rng(config, tenant_id, "assessment")
    definitions: list[Row] = []
    results: list[Row] = []
    n = 0
    for aid, subject, window, start, end in _assessment_ids(config):
        definitions.append(
            {
                "tenant_id": tenant_id,
                "assessment_id": aid,
                "assessment_name": f"Synthetic Interim {subject} ({window})",
                "subject": subject,
                "administration_window": window,
                "window_start": start.isoformat(),
                "window_end": end.isoformat(),
                "scale_min": 200,
                "scale_max": 800,
                "synthetic_marker": SYNTHETIC_MARKER,
            }
        )
        growth = 12 if window == "MOY" else 0
        for st in students:
            n += 1
            roll = rng.random()
            status = "COMPLETED" if roll < 0.93 else "NOT_COMPLETED" if roll < 0.97 else "EXCUSED"
            score: int | None = None
            band: str | None = None
            if status == "COMPLETED":
                ability = st.ability_ela if subject == "ELA" else st.ability_math
                benchmark = 300 + 25 * st.grade_index + growth
                score = int(_clip(round(benchmark + 40 * ability + rng.gauss(0, 15)), 200, 800))
                delta = score - benchmark
                band = (
                    "BELOW_BASIC"
                    if delta < -45
                    else "BASIC"
                    if delta < -15
                    else "PROFICIENT"
                    if delta < 35
                    else "ADVANCED"
                )
            results.append(
                {
                    "tenant_id": tenant_id,
                    "school_id": st.row["school_id"],
                    "student_id": st.row["student_id"],
                    "result_id": f"{tenant_id}-RES{n:07d}",
                    "assessment_id": aid,
                    "administered_date": (start + timedelta(days=rng.randint(0, 9))).isoformat(),
                    "grade_level": st.row["grade_level"],
                    "completion_status": status,
                    "scale_score": score,
                    "proficiency_band": band,
                    "synthetic_marker": SYNTHETIC_MARKER,
                }
            )
    return definitions, results


def _generate_attendance(
    config: GeneratorConfig, tenant_id: str, students: list[_Student]
) -> list[Row]:
    rng = _rng(config, tenant_id, "attendance")
    days = school_days(config.school_year_start, config.attendance_days)
    rows: list[Row] = []
    n = 0
    for st in students:
        p_absent = 0.055 * (4 if st.chronic_absence_prone else 1)
        for day in days:
            n += 1
            roll = rng.random()
            if roll < p_absent * 0.55:
                status = "ABSENT_EXCUSED"
            elif roll < p_absent:
                status = "ABSENT_UNEXCUSED"
            elif roll < p_absent + 0.015:
                status = "TARDY"
            else:
                status = "PRESENT"
            rows.append(
                {
                    "tenant_id": tenant_id,
                    "school_id": st.row["school_id"],
                    "student_id": st.row["student_id"],
                    "attendance_event_id": f"{tenant_id}-ATT{n:08d}",
                    "attendance_date": day.isoformat(),
                    "attendance_status": status,
                    "synthetic_marker": SYNTHETIC_MARKER,
                }
            )
    return rows


def _generate_grades(
    config: GeneratorConfig, tenant_id: str, students: list[_Student]
) -> list[Row]:
    rng = _rng(config, tenant_id, "grades")
    rows: list[Row] = []
    n = 0
    posted = {
        "Q1": datetime(config.school_year_start, 11, 5, 12, 0),
        "Q2": datetime(config.school_year_start + 1, 1, 28, 12, 0),
    }
    for st in students:
        for code, title in _COURSES:
            if code == "ELA":
                ability = st.ability_ela
            elif code == "MATH":
                ability = st.ability_math
            else:
                ability = (st.ability_ela + st.ability_math) / 2
            for period in _PERIODS:
                n += 1
                pct = round(_clip(78 + 9 * ability + rng.gauss(0, 5), 35, 100), 1)
                rows.append(
                    {
                        "tenant_id": tenant_id,
                        "school_id": st.row["school_id"],
                        "student_id": st.row["student_id"],
                        "grade_record_id": f"{tenant_id}-GRD{n:07d}",
                        "course_code": f"{code}-{st.row['grade_level']}",
                        "course_title": title,
                        "reporting_period": period,
                        "numeric_grade": pct,
                        "letter_grade": _letter(pct),
                        "posted_at": posted[period].isoformat() + "Z",
                        "synthetic_marker": SYNTHETIC_MARKER,
                    }
                )
    return rows


def _generate_lms(config: GeneratorConfig, tenant_id: str, students: list[_Student]) -> list[Row]:
    rng = _rng(config, tenant_id, "lms")
    days = school_days(config.school_year_start, config.attendance_days)
    types = [t for t, _ in _LMS_EVENT_TYPES]
    weights = [w for _, w in _LMS_EVENT_TYPES]
    rows: list[Row] = []
    n = 0
    for st in students:
        count = int(config.lms_events_per_student * rng.uniform(0.3, 1.7))
        for _ in range(count):
            n += 1
            code, _title = rng.choice(_COURSES)
            event_type = rng.choices(types, weights)[0]
            day = rng.choice(days)
            stamp = datetime(day.year, day.month, day.day, rng.randint(8, 21), rng.randint(0, 59))
            rows.append(
                {
                    "tenant_id": tenant_id,
                    "school_id": st.row["school_id"],
                    "student_id": st.row["student_id"],
                    "lms_event_id": f"{tenant_id}-LMS{n:08d}",
                    "course_code": f"{code}-{st.row['grade_level']}",
                    "event_type": event_type,
                    "assignment_id": (
                        f"SYN-A{rng.randint(1, 40):03d}"
                        if event_type.startswith(("ASSIGNMENT", "QUIZ"))
                        else None
                    ),
                    "occurred_at": stamp.isoformat() + "Z",
                    "duration_seconds": (0 if event_type == "LOGIN" else rng.randint(30, 2400)),
                    "synthetic_marker": SYNTHETIC_MARKER,
                }
            )
    return rows


def _generate_interventions(
    config: GeneratorConfig, tenant_id: str, students: list[_Student]
) -> list[Row]:
    rng = _rng(config, tenant_id, "interventions")
    first_day = date(config.school_year_start, 9, 1)
    rows: list[Row] = []
    n = 0
    for st in students:
        weakest = min(st.ability_ela, st.ability_math)
        probability = 0.30 if weakest < -0.8 else 0.05
        if not rng.random() < probability:
            continue
        n += 1
        if st.chronic_absence_prone and rng.random() < 0.4:
            itype, focus = "ATTENDANCE_MENTORING", "ATTENDANCE"
        elif st.ability_ela <= st.ability_math:
            itype, focus = "READING_TUTORING", "ELA"
        else:
            itype, focus = "MATH_SMALL_GROUP", "MATH"
        start = first_day + timedelta(days=rng.randint(7, 60))
        roll = rng.random()
        status = "ACTIVE" if roll < 0.70 else "COMPLETED" if roll < 0.90 else "DISCONTINUED"
        end = None if status == "ACTIVE" else start + timedelta(days=rng.randint(30, 90))
        rows.append(
            {
                "tenant_id": tenant_id,
                "school_id": st.row["school_id"],
                "student_id": st.row["student_id"],
                "intervention_id": f"{tenant_id}-INT{n:06d}",
                "intervention_type": itype,
                "subject_focus": focus,
                "tier": "TIER_2" if rng.random() < 0.7 else "TIER_3",
                "start_date": start.isoformat(),
                "end_date": end.isoformat() if end else None,
                "status": status,
                "minutes_per_week": rng.choice([60, 90, 120, 150]),
                "synthetic_marker": SYNTHETIC_MARKER,
            }
        )
    return rows


def _inject_defects(config: GeneratorConfig, tables: dict[str, list[Row]]) -> dict[str, int]:
    """Deliberately corrupt a fraction of rows so data-quality checks have real failures to find."""
    summary = {"duplicate_attendance_rows": 0, "completed_assessments_missing_score": 0}
    if config.defect_rate <= 0:
        return summary
    rng = _rng(config, "defects")

    results = tables[ASSESSMENT_RESULTS.key]
    for row in results:
        if row["completion_status"] == "COMPLETED" and rng.random() < config.defect_rate:
            row["scale_score"] = None
            row["proficiency_band"] = None
            summary["completed_assessments_missing_score"] += 1

    attendance = tables[ATTENDANCE.key]
    duplicates = [dict(r) for r in attendance if rng.random() < config.defect_rate]
    attendance.extend(duplicates)
    summary["duplicate_attendance_rows"] = len(duplicates)
    return summary


def generate(config: GeneratorConfig) -> GeneratedData:
    """Generate every synthetic source extract described in :mod:`.spec`."""
    config.validate()
    tables: dict[str, list[Row]] = {spec.key: [] for spec in ENTITIES}

    for t in range(1, config.tenants + 1):
        tenant_id = f"tenant-syn-{t:02d}"
        schools = _generate_schools(config, tenant_id, t)
        students = _generate_students(config, tenant_id, t, schools)
        definitions, results = _generate_assessments(config, tenant_id, students)

        tables[SCHOOLS.key].extend(schools)
        tables[STUDENTS.key].extend(s.row for s in students)
        tables[ASSESSMENT_DEFINITIONS.key].extend(definitions)
        tables[ASSESSMENT_RESULTS.key].extend(results)
        tables[ATTENDANCE.key].extend(_generate_attendance(config, tenant_id, students))
        tables[GRADES.key].extend(_generate_grades(config, tenant_id, students))
        tables[LMS_ACTIVITY.key].extend(_generate_lms(config, tenant_id, students))
        tables[INTERVENTIONS.key].extend(_generate_interventions(config, tenant_id, students))

    defects = _inject_defects(config, tables)
    return GeneratedData(tables=tables, config=config, defects=defects)
