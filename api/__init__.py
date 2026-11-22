"""EduPulse REST API.

Phase 1 ships only a health/version endpoint (``api.health``) so the container, compose stack
and CI have something real to start and probe. Business endpoints, authentication, RBAC and
audit logging arrive in later phases.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("edupulse-ai")
except PackageNotFoundError:  # running from a source checkout without installing
    __version__ = "0.0.0+local"
