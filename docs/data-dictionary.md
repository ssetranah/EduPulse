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

## Bronze tables (Phase 2)

Bronze stores each accepted source record twice: `raw` (exactly as received) and `canonical` (typed and
translated per the YAML mapping). Every Bronze row also carries the envelope fields listed in
[`ingestion.md`](ingestion.md#9-bronze-storage) (`_source_name`, `_ingestion_ts`, `_batch_id`,
`_source_record_hash`, `_schema_version`, `tenant_id`, …). The tables below are generated from
`ingestion/configs/mappings/*.yaml` (vendor-style sources); the file-drop mappings produce the same
canonical fields for `sis_students` and `assessment_definitions`, and `grades` from the JSONL gradebook.
`source_modified_at` is the source system's modification time and drives incremental loading.

### `sis_schools`  (entity `schools`, schema 1.0.0, key: tenant_id, school_id)

| Canonical field | Source field | Type | Required | Sensitivity | Notes |
|---|---|---|---|---|---|
| `tenant_id` | `DistrictId` | string | yes | internal |  |
| `school_id` | `SchoolId` | string | yes | internal |  |
| `school_name` | `SchoolName` | string | yes | internal |  |
| `school_type` | `SchoolTypeDescriptor` | string | yes | internal | allowed: ELEMENTARY, MIDDLE, HIGH, K8, K12; upper-cased |
| `lowest_grade` | `LowestGradeLevel` | string | yes | internal |  |
| `highest_grade` | `HighestGradeLevel` | string | yes | internal |  |
| `city` | `City` | string | no | internal |  |
| `state_code` | `StateAbbreviation` | string | no | internal | pattern `^[A-Z]{2}$` |
| `postal_code` | `PostalCode` | string | no | internal | pattern `^[0-9]{5}$` |
| `synthetic_marker` | `SyntheticMarker` | string | yes | internal |  |
| `source_modified_at` | `LastModifiedDate` | datetime | yes | internal |  |

### `sis_students`  (entity `students`, schema 1.0.0, key: tenant_id, student_id)

| Canonical field | Source field | Type | Required | Sensitivity | Notes |
|---|---|---|---|---|---|
| `tenant_id` | `DistrictId` | string | yes | internal |  |
| `school_id` | `SchoolId` | string | yes | internal |  |
| `student_id` | `StudentUniqueId` | string | yes | confidential |  |
| `state_student_id` | `StateStudentId` | string | no | confidential |  |
| `first_name` | `FirstName` | string | yes | restricted |  |
| `last_name` | `LastSurname` | string | yes | restricted |  |
| `birth_date` | `BirthDate` | date | yes | restricted | format %m/%d/%Y |
| `grade_level` | `GradeLevelDescriptor` | string | yes | internal |  |
| `gender` | `GenderDescriptor` | string | yes | confidential | value map: F→FEMALE, M→MALE, X→NON_BINARY |
| `race_ethnicity` | `RaceDescriptor` | string | no | confidential |  |
| `english_learner` | `EnglishLearnerFlag` | boolean | yes | confidential |  |
| `special_education` | `SpecialEducationFlag` | boolean | yes | restricted |  |
| `economically_disadvantaged` | `EconomicDisadvantageFlag` | boolean | yes | confidential |  |
| `enrollment_start_date` | `EntryDate` | date | yes | internal | format %m/%d/%Y |
| `enrollment_status` | `EnrollmentStatus` | string | yes | internal | allowed: ACTIVE, WITHDRAWN, GRADUATED, TRANSFERRED; upper-cased |
| `synthetic_marker` | `SyntheticMarker` | string | yes | internal |  |
| `source_modified_at` | `LastModifiedDate` | datetime | yes | internal |  |

### `lms_activity`  (entity `lms_activity`, schema 1.0.0, key: tenant_id, lms_event_id)

| Canonical field | Source field | Type | Required | Sensitivity | Notes |
|---|---|---|---|---|---|
| `tenant_id` | `TenantKey` | string | yes | internal |  |
| `school_id` | `BuildingId` | string | yes | internal |  |
| `student_id` | `LearnerId` | string | yes | confidential |  |
| `lms_event_id` | `EventId` | string | yes | internal |  |
| `course_code` | `CourseCode` | string | yes | internal |  |
| `event_type` | `ActivityType` | string | yes | internal | upper-cased |
| `assignment_id` | `AssignmentRef` | string | no | internal |  |
| `occurred_at` | `EventTimestamp` | datetime | yes | internal |  |
| `duration_seconds` | `SecondsSpent` | integer | yes | internal | range 0–86400 |
| `synthetic_marker` | `SyntheticMarker` | string | yes | internal |  |
| `source_modified_at` | `ModifiedAt` | datetime | yes | internal |  |

### `assessment_definitions`  (entity `assessment_definitions`, schema 1.0.0, key: tenant_id, assessment_id)

| Canonical field | Source field | Type | Required | Sensitivity | Notes |
|---|---|---|---|---|---|
| `tenant_id` | `TenantCode` | string | yes | internal |  |
| `assessment_id` | `AssessmentCode` | string | yes | internal |  |
| `assessment_name` | `AssessmentTitle` | string | yes | internal |  |
| `subject` | `SubjectArea` | string | yes | internal | upper-cased |
| `administration_window` | `Window` | string | yes | internal |  |
| `window_start` | `WindowOpens` | date | yes | internal | format %m/%d/%Y |
| `window_end` | `WindowCloses` | date | yes | internal | format %m/%d/%Y |
| `scale_min` | `MinScale` | integer | yes | internal |  |
| `scale_max` | `MaxScale` | integer | yes | internal |  |
| `synthetic_marker` | `SyntheticMarker` | string | yes | internal |  |
| `source_modified_at` | `UpdatedAt` | datetime | yes | internal |  |

### `assessment_results`  (entity `assessment_results`, schema 1.0.0, key: tenant_id, result_id)

| Canonical field | Source field | Type | Required | Sensitivity | Notes |
|---|---|---|---|---|---|
| `tenant_id` | `TenantCode` | string | yes | internal |  |
| `school_id` | `SchoolCode` | string | yes | internal |  |
| `student_id` | `StudentRef` | string | yes | confidential |  |
| `result_id` | `ResultId` | string | yes | internal |  |
| `assessment_id` | `AssessmentCode` | string | yes | internal |  |
| `administered_date` | `TestDate` | date | yes | internal | format %m/%d/%Y |
| `grade_level` | `GradeLevel` | string | yes | internal |  |
| `completion_status` | `Status` | string | yes | internal | value map: C→COMPLETED, NC→NOT_COMPLETED, EX→EXCUSED |
| `scale_score` | `ScaleScore` | integer | no | confidential | range 0–1000 |
| `proficiency_band` | `PerformanceLevel` | string | no | internal | value map: BELOW BASIC→BELOW_BASIC, BASIC→BASIC, PROFICIENT→PROFICIENT, ADVANCED→ADVANCED; upper-cased |
| `synthetic_marker` | `SyntheticMarker` | string | yes | internal |  |
| `source_modified_at` | `UpdatedAt` | datetime | yes | internal |  |

### `attendance_daily`  (entity `attendance_daily`, schema 1.0.0, key: tenant_id, attendance_event_id)

| Canonical field | Source field | Type | Required | Sensitivity | Notes |
|---|---|---|---|---|---|
| `tenant_id` | `Tenant` | string | yes | internal |  |
| `school_id` | `School` | string | yes | internal |  |
| `student_id` | `Student` | string | yes | confidential |  |
| `attendance_event_id` | `AttendanceId` | string | yes | internal |  |
| `attendance_date` | `Date` | date | yes | internal | format %Y%m%d |
| `attendance_status` | `Code` | string | yes | internal | value map: P→PRESENT, AE→ABSENT_EXCUSED, AU→ABSENT_UNEXCUSED, T→TARDY |
| `synthetic_marker` | `SyntheticMarker` | string | yes | internal |  |
| `source_modified_at` | `RecordedAt` | datetime | yes | internal | format %Y-%m-%d %H:%M:%S |

### `interventions`  (entity `interventions`, schema 1.0.0, key: tenant_id, intervention_id)

| Canonical field | Source field | Type | Required | Sensitivity | Notes |
|---|---|---|---|---|---|
| `tenant_id` | `Tenant` | string | yes | internal |  |
| `school_id` | `School` | string | yes | internal |  |
| `student_id` | `Student` | string | yes | confidential |  |
| `intervention_id` | `InterventionId` | string | yes | internal |  |
| `intervention_type` | `Program` | string | yes | internal |  |
| `subject_focus` | `Focus` | string | no | internal |  |
| `tier` | `Tier` | string | yes | internal | value map: 1→TIER_1, 2→TIER_2, 3→TIER_3 |
| `start_date` | `BeginDate` | date | yes | internal |  |
| `end_date` | `EndDate` | date | no | internal |  |
| `status` | `State` | string | yes | internal | allowed: ACTIVE, COMPLETED, DISCONTINUED; upper-cased |
| `minutes_per_week` | `MinutesWeekly` | integer | no | internal | range 0–1000 |
| `synthetic_marker` | `SyntheticMarker` | string | yes | internal |  |
| `source_modified_at` | `Updated` | datetime | yes | internal |  |

### `grades`  (file drop only; entity `grades`)

Fields: `tenant_id`, `school_id`, `student_id`, `grade_record_id`, `course_code`, `course_title`, `reporting_period`, `numeric_grade`, `letter_grade`, `posted_at`, `synthetic_marker`. Incremental field: `posted_at`.

