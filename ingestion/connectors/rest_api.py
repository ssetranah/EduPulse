"""JSON REST API connector (standard library ``urllib`` only).

Features: page-number and cursor pagination, incremental ``since`` parameter, bearer / API-key
authentication taken from an *environment variable*, per-request timeout, response-size cap,
retry with backoff on transient failures.

Security posture:
* ``https`` is required unless the host is loopback or ``allow_insecure_http`` is set.
* An optional ``allowed_hosts`` list pins the hosts the connector may talk to.
* **Redirects are refused** -- following a redirect could send credentials to another host.
* Secrets are read from the environment at start-up and never logged; URLs are logged without
  their query string.
"""

from __future__ import annotations

import ipaddress
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator, Sequence
from typing import Any, ClassVar

from ..config import RestApiSource, RestEntity
from ..errors import (
    AuthenticationError,
    ConfigError,
    PermanentSourceError,
    SchemaError,
    TransientSourceError,
)
from ..mapping_spec import MappingSpec
from ..models import (
    EntityDescriptor,
    ErrorContext,
    ErrorDecision,
    IncrementalPlan,
    RawRecord,
    RecordStream,
    SchemaCheck,
)
from .base import SourceConnector

_TRANSIENT_STATUS = {408, 425, 429, 500, 502, 503, 504}
_USER_AGENT = "EduPulse-Ingest/0.2"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None  # turns the 3xx into an HTTPError we can refuse explicitly


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class RestApiConnector(SourceConnector):
    source_type: ClassVar[str] = "rest_api"
    extraction_method: ClassVar[str] = "JSON REST API (HTTP GET, paginated)"

    config: RestApiSource

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        parsed = urllib.parse.urlparse(self.config.base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ConfigError("base_url must be an absolute http(s) URL")
        if parsed.username or parsed.password:
            raise ConfigError("credentials must not be embedded in base_url")
        if parsed.scheme == "http" and not (
            _is_loopback(parsed.hostname) or self.config.allow_insecure_http
        ):
            raise ConfigError(
                "base_url uses plain http for a non-loopback host; use https "
                "(or set allow_insecure_http: true for a trusted network)"
            )
        if (
            self.config.allowed_hosts is not None
            and parsed.hostname not in self.config.allowed_hosts
        ):
            raise ConfigError(f"host '{parsed.hostname}' is not in allowed_hosts")
        self._base = self.config.base_url.rstrip("/")
        self._headers = {"Accept": "application/json", "User-Agent": _USER_AGENT}
        self._add_auth_header()
        self._opener = urllib.request.build_opener(_NoRedirect)

    def _add_auth_header(self) -> None:
        auth = self.config.auth
        if auth.type == "none":
            return
        secret = self.env.get(auth.token_env or "")
        if not secret:
            raise ConfigError(
                f"environment variable '{auth.token_env}' is not set (auth.token_env)"
            )
        if auth.type == "bearer":
            self._headers["Authorization"] = f"Bearer {secret}"
        else:
            self._headers[auth.header_name] = secret

    # ------------------------------------------------------------------ interface
    def discover(self) -> list[EntityDescriptor]:
        # Config-driven: no network call, so discovery cannot fail on a flaky endpoint.
        return [
            EntityDescriptor(
                name=e.name,
                supports_incremental=e.since_param is not None,
                description=f"GET {e.path}",
            )
            for e in self.config.entities
            if e.enabled
        ]

    def extract(self, entity: RestEntity, since: str | None) -> Iterator[RecordStream]:  # type: ignore[override]
        yield RecordStream(
            entity=entity.name,
            unit_id=f"{urllib.parse.urlparse(self._base).netloc}{entity.path}",
            records=self._records(entity, since),
        )

    def validate_schema(
        self, entity: Any, mapping: MappingSpec, observed_fields: Sequence[str]
    ) -> SchemaCheck:
        return self.default_schema_check(mapping, observed_fields)

    def plan_incremental(
        self, entity: RestEntity, mapping: MappingSpec, last_watermark: str | None
    ) -> IncrementalPlan:
        if entity.since_param and mapping.incremental_field:
            return IncrementalPlan(
                "watermark",
                since=last_watermark,
                description=f"?{entity.since_param}=<watermark> on '{mapping.incremental_field}'",
            )
        return IncrementalPlan("full_refresh", description="no since_param/incremental_field")

    def advance_watermark(
        self, entity: Any, previous: str | None, observed_max: str | None
    ) -> str | None:
        return self.default_advance_watermark(previous, observed_max)

    def handle_error(self, error: BaseException, context: ErrorContext) -> ErrorDecision:
        return self.default_error_decision(error, context)

    # ------------------------------------------------------------------ paging
    def _records(self, entity: RestEntity, since: str | None) -> Iterator[RawRecord]:
        pg = entity.pagination
        page = pg.start_page
        cursor: str | None = None
        pages = 0
        while True:
            pages += 1
            if pages > self.config.max_pages:
                raise PermanentSourceError(
                    f"{entity.path}: exceeded max_pages={self.config.max_pages}"
                )
            params: dict[str, Any] = dict(entity.query)
            if since and entity.since_param:
                params[entity.since_param] = since
            if pg.type == "page":
                params[pg.page_param] = page
                params[pg.size_param] = pg.page_size
            elif pg.type == "cursor":
                params[pg.size_param] = pg.page_size
                if cursor:
                    params[pg.cursor_param] = cursor
            label = f"GET {entity.path} page {pages}"
            body = self.call_with_retry(label, lambda p=params: self._get_json(entity.path, p))

            items = _dig(body, entity.records_path)
            if not isinstance(items, list):
                raise SchemaError(f"{entity.path}: response has no list at '{entity.records_path}'")
            for index, item in enumerate(items):
                location = f"{entity.path}#page={pages},idx={index}"
                if isinstance(item, dict):
                    yield RawRecord(item, location)
                else:
                    yield RawRecord({}, location, parse_error="ROW_NOT_AN_OBJECT")

            if pg.type == "none" or not items:
                return
            if pg.type == "page":
                page += 1
            else:
                nxt = _dig(body, pg.cursor_path)
                if not nxt:
                    return
                cursor = str(nxt)

    # ------------------------------------------------------------------ HTTP
    def _get_json(self, path: str, params: dict[str, Any]) -> Any:
        query = urllib.parse.urlencode(params)
        url = f"{self._base}{path}" + (f"?{query}" if query else "")
        request = urllib.request.Request(url, headers=self._headers, method="GET")  # noqa: S310
        try:
            with self._opener.open(request, timeout=self.config.timeout_seconds) as response:
                limit = self.config.max_response_mb * 1024 * 1024
                raw = response.read(limit + 1)
        except urllib.error.HTTPError as exc:
            raise _classify_http_error(exc, path) from exc
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, ssl.SSLError):
                raise PermanentSourceError(f"{path}: TLS error ({type(reason).__name__})") from exc
            raise TransientSourceError(
                f"{path}: connection problem ({type(reason).__name__})"
            ) from exc
        except (TimeoutError, ConnectionError) as exc:
            raise TransientSourceError(f"{path}: {type(exc).__name__}") from exc
        if len(raw) > limit:
            raise PermanentSourceError(f"{path}: response exceeds {self.config.max_response_mb} MB")
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SchemaError(f"{path}: response is not valid JSON") from exc


def _classify_http_error(exc: urllib.error.HTTPError, path: str) -> Exception:
    code = exc.code
    if code in (401, 403):
        return AuthenticationError(f"{path}: HTTP {code} (credentials rejected)")
    if code in _TRANSIENT_STATUS:
        retry_after: float | None = None
        header = exc.headers.get("Retry-After") if exc.headers else None
        if header:
            try:
                retry_after = max(0.0, float(header))
            except ValueError:
                retry_after = None
        return TransientSourceError(f"{path}: HTTP {code}", retry_after=retry_after)
    if 300 <= code < 400:
        return PermanentSourceError(f"{path}: HTTP {code} redirect refused")
    return PermanentSourceError(f"{path}: HTTP {code}")


def _dig(body: Any, dotted: str | None) -> Any:
    if not dotted:
        return body
    node = body
    for part in dotted.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node
