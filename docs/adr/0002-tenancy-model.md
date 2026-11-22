# ADR-0002: Tenancy — shared tables with `tenant_id`

**Status:** Accepted (MVP) · **Date:** 2026-11-21

## Context
EduPulse serves multiple school networks ("tenants"). Options: schema-per-tenant, or shared
tables with a `tenant_id` column.

## Decision
**Shared tables with a mandatory `tenant_id` column in every layer**, with isolation enforced at
several levels:

1. **Data:** `tenant_id` is non-null in Bronze, Silver and Gold; dbt tests assert it.
2. **API:** every query is scoped by the caller's tenant from the authenticated identity — never
   from a request parameter.
3. **Serving store:** PostgreSQL row-level security policies on tenant-scoped tables.
   (DuckDB has no RLS, so in local mode the API predicate is the enforcement point.)
4. **Object storage:** paths are tenant-prefixed (`<bucket>/<tenant_id>/…`), enabling
   per-prefix policies.

## Why not schema-per-tenant
One dbt project would have to run once per tenant (or be templated per schema), multiplying
operational surface, migrations and CI time for little MVP benefit.

## Consequences
- (+) One set of models and tests; cross-tenant benchmarking stays possible where permitted.
- (−) A missing predicate leaks across tenants, so tenant scoping gets dedicated tests,
  including negative cross-tenant tests, from the first API phase.
- Revisit if a customer needs contractual physical isolation; the extract/path layout already
  allows lifting a tenant into its own catalog.

The Phase 1 synthetic generator emits multiple tenants so isolation can be tested from day one.
