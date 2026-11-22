# Data dictionary — synthetic source extracts (schema v1.0.0)

Everything below is produced by `python -m ingestion.synthetic generate` and is **entirely
fictional**. Every record carries `synthetic_marker = SYNTHETIC_TEST_DATA`. These are *source
system* shapes (what Bronze will land), not the Silver canonical model.

Conventions: IDs are prefixed `SYN-`; geography is state `ZZ`, postal code `00000`; no e-mail
addresses, phone numbers or SSNs are generated (the validator fails if any appear). Dates are
ISO-8601; timestamps are UTC with a `Z` suffix. Output is deterministic for a given config+seed.

**Sensitivity** (used later by masking/RBAC): `restricted` = direct identifier or protected
demographic/program status; `confidential` = student-level educational record; `internal` =
non-student reference data.

## sis / schools — CSV
| Column | Type | Sensitivity | Notes |
|---|---|---|---|
| tenant_id | string | internal | `tenant-syn-NN` |
| school_id | string | internal | `SYN-TNN-SCNN` |
| school_name | string | internal | always ends with `(Synthetic)` |
| school_type | enum | internal | ELEMENTARY, MIDDLE, HIGH |
| lowest_grade / highest_grade | string | internal | `KG`, `01`…`12` |
| city, state_code, postal_code | string | internal | `Synthville`, `ZZ`, `00000` |
| synthetic_marker | string | internal | `SYNTHETIC_TEST_DATA` |

## sis / students — CSV
| Column | Type | Sensitivity | Notes |
|---|---|---|---|
| tenant_id, school_id | string | internal | |
| student_id | string | confidential | `SYN-TNN-STNNNNN` |
| state_student_id | string | restricted | fictional state ID |
| first_name, last_name | string | restricted | invented names |
| birth_date | date | restricted | consistent with grade level |
| grade_level | string | confidential | `KG`, `01`…`12` |
| gender | enum | restricted | FEMALE, MALE, NON_BINARY |
| race_ethnicity | enum | restricted | 7 categories |
| english_learner, special_education, economically_disadvantaged | bool | restricted | drawn independently; no real-world meaning |
| enrollment_start_date | date | confidential | |
| enrollment_status | enum | confidential | ACTIVE |

## assessment / assessment_definitions — CSV
`tenant_id`, `assessment_id` (`SYN-ASM-<SUBJECT>-<WINDOW>-<YEAR>`), `assessment_name`,
`subject` (ELA, MATH), `administration_window` (BOY, MOY), `window_start`, `window_end`,
`scale_min` (200), `scale_max` (800), `synthetic_marker`. All *internal*.

## assessment / assessment_results — CSV
| Column | Notes |
|---|---|
| tenant_id, school_id, student_id | keys (confidential) |
| result_id | unique |
| assessment_id | → assessment_definitions |
| administered_date | within the administration window |
| grade_level | grade at administration |
| completion_status | COMPLETED, NOT_COMPLETED, EXCUSED |
| scale_score | 200–800; empty unless COMPLETED |
| proficiency_band | BELOW_BASIC, BASIC, PROFICIENT, ADVANCED as emitted by the "source"; empty unless COMPLETED |

## attendance / attendance_daily — CSV
`tenant_id`, `school_id`, `student_id`, `attendance_event_id` (unique), `attendance_date`
(weekdays from 1 Sept), `attendance_status` (PRESENT, ABSENT_EXCUSED, ABSENT_UNEXCUSED, TARDY),
`synthetic_marker`. A minority of students are generated with a much higher absence rate.

## grading / grades — JSON Lines
`tenant_id`, `school_id`, `student_id`, `grade_record_id` (unique), `course_code`
(`ELA|MATH|SCI|SOC-<grade>`), `course_title`, `reporting_period` (Q1, Q2), `numeric_grade`
(35–100), `letter_grade` (A–F), `posted_at` (UTC), `synthetic_marker`.

## lms / lms_activity — JSON Lines
`tenant_id`, `school_id`, `student_id`, `lms_event_id` (unique), `course_code`, `event_type`
(LOGIN, RESOURCE_VIEWED, ASSIGNMENT_VIEWED, ASSIGNMENT_SUBMITTED, QUIZ_COMPLETED),
`assignment_id` (null for LOGIN/RESOURCE_VIEWED), `occurred_at` (UTC), `duration_seconds`,
`synthetic_marker`.

## intervention / interventions — CSV
`tenant_id`, `school_id`, `student_id`, `intervention_id` (unique), `intervention_type`
(READING_TUTORING, MATH_SMALL_GROUP, ATTENDANCE_MENTORING), `subject_focus` (ELA, MATH,
ATTENDANCE), `tier` (TIER_2, TIER_3), `start_date`, `end_date` (empty while ACTIVE), `status`
(ACTIVE, COMPLETED, DISCONTINUED), `minutes_per_week`, `synthetic_marker`. Weaker students are
more likely to receive one.

## Dataset-level files
- `manifest.json` — marker, schema version, generator config, injected-defect counts, and per-file
  row counts and SHA-256 checksums. Contains no timestamps, so identical config+seed gives an
  identical manifest.
- `README_SYNTHETIC_TEST_DATA.txt` — human-readable warning banner.

## Optional defects (`--defect-rate`)
For data-quality testing only (default 0): duplicate attendance rows, and COMPLETED assessments
with a missing score. Injected counts are recorded in `manifest.json` → `defects_injected`.

## Relationships
`students.school_id → schools`; every child extract's `(tenant_id, school_id, student_id)` →
`students`; `assessment_results.assessment_id → assessment_definitions`. The validator checks
these within each tenant.
