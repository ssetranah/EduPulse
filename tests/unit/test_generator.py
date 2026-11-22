import datetime as dt

import pytest

from ingestion.synthetic import SYNTHETIC_MARKER, GeneratorConfig, generate
from ingestion.synthetic.generator import GRADE_LEVELS, school_days
from ingestion.synthetic.spec import (
    ASSESSMENT_RESULTS,
    ATTENDANCE,
    ENTITIES,
    GRADES,
    INTERVENTIONS,
    LMS_ACTIVITY,
    SCHOOLS,
    STUDENTS,
)

pytestmark = pytest.mark.unit


def test_every_entity_is_generated_and_non_empty(clean_data):
    assert set(clean_data.tables) == {spec.key for spec in ENTITIES}
    for key, rows in clean_data.tables.items():
        assert rows, f"{key} produced no rows"


def test_row_counts_follow_config(clean_data, clean_config):
    n_schools = clean_config.tenants * clean_config.schools_per_tenant
    n_students = n_schools * clean_config.students_per_school
    tables = clean_data.tables
    assert len(tables[SCHOOLS.key]) == n_schools
    assert len(tables[STUDENTS.key]) == n_students
    assert len(tables[ATTENDANCE.key]) == n_students * clean_config.attendance_days
    # 2 subjects x 2 windows
    assert len(tables[ASSESSMENT_RESULTS.key]) == n_students * 4
    # 4 courses x 2 reporting periods
    assert len(tables[GRADES.key]) == n_students * 8


def test_rows_match_declared_columns_exactly(clean_data):
    for spec in ENTITIES:
        for row in clean_data.tables[spec.key][:200]:
            assert tuple(row) == spec.fields


def test_every_row_is_marked_synthetic(clean_data):
    for key, rows in clean_data.tables.items():
        assert all(r["synthetic_marker"] == SYNTHETIC_MARKER for r in rows), key


def test_same_seed_gives_identical_output(clean_config, clean_data):
    assert generate(clean_config).tables == clean_data.tables


def test_different_seed_changes_output(clean_config, clean_data):
    other = GeneratorConfig(**{**clean_config.as_dict(), "seed": clean_config.seed + 1})
    assert generate(other).tables[STUDENTS.key] != clean_data.tables[STUDENTS.key]


def test_identifiers_are_obviously_synthetic(clean_data):
    for row in clean_data.tables[STUDENTS.key]:
        assert row["student_id"].startswith("SYN-")
        assert row["state_student_id"].startswith("SYN")
        assert row["tenant_id"].startswith("tenant-syn-")
    for row in clean_data.tables[SCHOOLS.key]:
        assert row["school_name"].endswith("(Synthetic)")
        assert row["state_code"] == "ZZ"
        assert row["postal_code"] == "00000"


def test_ids_are_unique_within_each_entity(clean_data):
    id_fields = {
        STUDENTS.key: "student_id",
        ASSESSMENT_RESULTS.key: "result_id",
        ATTENDANCE.key: "attendance_event_id",
        GRADES.key: "grade_record_id",
        LMS_ACTIVITY.key: "lms_event_id",
        INTERVENTIONS.key: "intervention_id",
    }
    for key, field in id_fields.items():
        values = [r[field] for r in clean_data.tables[key]]
        assert len(values) == len(set(values)), key


def test_students_sit_in_grades_matching_their_school_band(clean_data):
    bands = {
        s["school_id"]: (s["lowest_grade"], s["highest_grade"])
        for s in clean_data.tables[SCHOOLS.key]
    }
    for st in clean_data.tables[STUDENTS.key]:
        low, high = bands[st["school_id"]]
        assert (
            GRADE_LEVELS.index(low)
            <= GRADE_LEVELS.index(st["grade_level"])
            <= GRADE_LEVELS.index(high)
        )


def test_school_days_are_weekdays_and_ordered():
    days = school_days(2025, 30)
    assert len(days) == 30
    assert all(d.weekday() < 5 for d in days)
    assert days == sorted(days)
    assert days[0] >= dt.date(2025, 9, 1)


def test_clean_config_injects_no_defects(clean_data):
    assert clean_data.defects == {
        "duplicate_attendance_rows": 0,
        "completed_assessments_missing_score": 0,
    }


def test_defect_rate_injects_detectable_defects(defective_data):
    assert defective_data.defects["duplicate_attendance_rows"] > 0
    assert defective_data.defects["completed_assessments_missing_score"] > 0
    ids = [r["attendance_event_id"] for r in defective_data.tables[ATTENDANCE.key]]
    assert len(ids) > len(set(ids))
    missing = [
        r
        for r in defective_data.tables[ASSESSMENT_RESULTS.key]
        if r["completion_status"] == "COMPLETED" and r["scale_score"] is None
    ]
    assert len(missing) == defective_data.defects["completed_assessments_missing_score"]


def test_invalid_config_is_refused_by_generate():
    with pytest.raises(ValueError, match="tenants must be"):
        generate(GeneratorConfig(tenants=0))
