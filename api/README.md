# api

Phase 1 ships `health.py`, a stdlib-only liveness/version server (`/healthz`, `/version`) used by the
compose healthcheck and the dashboard. It serves no student data. The real REST API (framework,
authentication, tenant-scoped RBAC, masking, audit logging) arrives in a later phase.

`python -m api.health` — binds `EDUPULSE_API_HOST:EDUPULSE_API_PORT` (default `127.0.0.1:8000`).
