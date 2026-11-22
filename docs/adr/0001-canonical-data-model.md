# ADR-0001: Canonical data model — Ed-Fi-aligned, not full Ed-Fi

**Status:** Accepted (MVP) · **Date:** 2026-11-21

## Context
EduPulse unifies SIS, LMS, assessment, grading, attendance and intervention data. We need a
Silver-layer model that districts recognise and that can interoperate later, without the cost of
standing up and operating an Ed-Fi ODS/API for an MVP.

## Decision
Silver entities and descriptor values follow **Ed-Fi Data Standard concepts and naming**
(e.g. student, school, student–school association, student assessment, attendance event, grade,
intervention) but are implemented as plain dbt models. We do **not** deploy the Ed-Fi ODS, and we
do **not** claim Ed-Fi certification or conformance.

## Consequences
- (+) Familiar vocabulary; lower mapping effort for districts already on Ed-Fi.
- (+) No extra infrastructure; models stay portable between DuckDB and Databricks.
- (−) Alignment is by convention; a formal Ed-Fi export would need an additional mapping layer.
- Revisit if a customer requires a certified Ed-Fi API.

The synthetic extracts (`docs/data-dictionary.md`) deliberately look like *source* systems, not
like Ed-Fi, so the Bronze→Silver conformance work is real.
