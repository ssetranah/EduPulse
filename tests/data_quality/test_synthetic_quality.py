"""Data-quality assertions on generated data: ranges, domains, completeness, plausibility.

These are the same *kinds* of checks that dbt tests will run against Silver/Gold later
(not_null, accepted_values, relationships, range checks), expressed here against the sources.
"""

from collections import Counter
from datetime import date

import pytest

from ingestion.synthetic.spec import (
    ASSESSMENT_RESULTS,
    ATTENDANCE,
    GRADES,
    INTERVENTIONS,
    LMS_ACTIVITY,
    STUDENTS,
)

pytestmark = pytest.mark.data_quality


def test_required_keys_are_never_null(clean_data):
    required = ("tenant_id", "school_id", "student_id")
    for key, rows in clean_data.tables.items():
        if "student_id" in rows[0]:
            for row in rows:
                assert all(row[f] not in (None, "") for f in required), key


def test_attendance_domain_and_one_row_per_student_day(clean_data):
    rows = clean_data.tables[ATTENDANCE.key]
    assert {r["attendance_status"] for r in rows} <= {
        "PRESENT", "ABSENT_EXCUSED", "ABSENT_UNEXCUSED", "TARDY",
    }  # fmt: skip
    pairs = Counter((r["tenant_id"], r["student_id"], r["attendance_date"]) for r in rows)
    assert max(pairs.values()) == 1


def test_attendance_rate_is_plausible(clean_data):
    rows = clean_data.tables[ATTENDANCE.key]
    absent = sum(r["attendance_status"].startswith("ABSENT") for r in rows) / len(rows)
    assert 0.03 <= absent <= 0.15


def test_assessment_scores_in_scale_and_consistent_with_status(clean_data):
    for row in clean_data.tables[ASSESSMENT_RESULTS.key]:
        if row["completion_status"] == "COMPLETED":
            assert 200 <= row["scale_score"] <= 800
            assert row["proficiency_band"] in {"BELOW_BASIC", "BASIC", "PROFICIENT", "ADVANCED"}
        else:
            assert row["scale_score"] is None
            assert row["proficiency_band"] is None


def test_assessment_completion_rate_is_plausible(clean_data):
    rows = clean_data.tables[ASSESSMENT_RESULTS.key]
    done = sum(r["completion_status"] == "COMPLETED" for r in rows) / len(rows)
    assert 0.88 <= done <= 0.98


def test_scores_rise_with_grade_level(clean_data):
    by_grade: dict[str, list[int]] = {}
    for r in clean_data.tables[ASSESSMENT_RESULTS.key]:
        if r["scale_score"] is not None:
            by_grade.setdefault(r["grade_level"], []).append(r["scale_score"])
    mean = {g: sum(v) / len(v) for g, v in by_grade.items() if len(v) >= 20}
    assert mean["KG"] < mean["05"] < mean["12"]


def test_grades_are_in_range_and_letters_agree(clean_data):
    cutoffs = [(90, "A"), (80, "B"), (70, "C"), (60, "D"), (0, "F")]
    for row in clean_data.tables[GRADES.key]:
        assert 35 <= row["numeric_grade"] <= 100
        expected = next(letter for cut, letter in cutoffs if row["numeric_grade"] >= cut)
        assert row["letter_grade"] == expected


def test_lms_events_have_valid_types_and_durations(clean_data):
    valid = {
        "LOGIN", "RESOURCE_VIEWED", "ASSIGNMENT_VIEWED", "ASSIGNMENT_SUBMITTED", "QUIZ_COMPLETED",
    }  # fmt: skip
    for row in clean_data.tables[LMS_ACTIVITY.key]:
        assert row["event_type"] in valid
        assert row["duration_seconds"] >= 0
        assert row["occurred_at"].endswith("Z")


def test_interventions_have_consistent_dates_and_status(clean_data):
    for row in clean_data.tables[INTERVENTIONS.key]:
        start = date.fromisoformat(row["start_date"])
        if row["status"] == "ACTIVE":
            assert row["end_date"] is None
        else:
            assert date.fromisoformat(row["end_date"]) > start
        assert row["tier"] in {"TIER_2", "TIER_3"}
        assert row["minutes_per_week"] > 0


def test_intervention_share_is_a_minority_but_not_empty(clean_data):
    share = len(clean_data.tables[INTERVENTIONS.key]) / len(clean_data.tables[STUDENTS.key])
    assert 0.03 <= share <= 0.40


def test_birth_dates_agree_with_grade_level(clean_data):
    for row in clean_data.tables[STUDENTS.key]:
        born = date.fromisoformat(row["birth_date"])
        age = 2025 - born.year
        expected = 5 if row["grade_level"] == "KG" else 5 + int(row["grade_level"])
        assert age == expected


def test_all_tenants_are_isolated_in_ids(clean_data):
    """A student's tenant must be recoverable from, and consistent with, its ID."""
    for row in clean_data.tables[STUDENTS.key]:
        tenant_no = int(row["tenant_id"].rsplit("-", 1)[1])
        assert row["student_id"].startswith(f"SYN-T{tenant_no:02d}-")
