"""JSON REST connector against the in-process mock vendor API (loopback only)."""

from __future__ import annotations

import pytest

from ingestion.bronze import BronzeStore
from ingestion.config import load_config
from ingestion.connectors import build_connector
from ingestion.errors import ConfigError
from ingestion.retry import RetryPolicy
from ingestion.runner import IngestionRunner
from ingestion.synthetic import GeneratorConfig
from ingestion.synthetic.rest_server import MockRestServer
from tests.ing_support import MAPPINGS_DIR, assert_invariants

pytestmark = pytest.mark.integration

GEN = GeneratorConfig(seed=5, tenants=2, schools_per_tenant=2, students_per_school=30)


def source(base_url: str, **kw):
    entity = {
        "name": "students",
        "mapping": str(MAPPINGS_DIR / "sis_students.yaml"),
        "path": "/v1/students",
        "since_param": "updated_since",
        "pagination": {"type": "page", "page_size": 50},
    }
    entity.update(kw.pop("entity", {}))
    return {
        "type": "rest_api",
        "system": {"system": "SIS", "vendor": "Mock Vendor"},
        "base_url": base_url,
        "entities": [entity],
        **kw,
    }


@pytest.fixture
def run_rest(write_ingest_config, tmp_path):
    def _run(src, *, env=None, expect_report=True):
        path = write_ingest_config({"api": src})
        runner = IngestionRunner(
            load_config(path),
            output_dir=tmp_path / "store",
            quiet=True,
            sleep=lambda _s: None,
            env=env or {},
        )
        return runner.run(["api"])

    return _run


def ent(report):
    return report.sources[0].entities[0]


def test_page_pagination_loads_everything(run_rest) -> None:
    with MockRestServer(GEN) as srv:
        e = ent(run_rest(source(srv.base_url)))
        assert e.status == "SUCCEEDED" and e.counters.loaded == 120
        assert_invariants(e.counters.to_dict())
        pages = [q["page"] for p, q in srv.requests if p == "/v1/students"]
        # 120 rows / 50 per page = 3 pages; the connector reads until an EMPTY page, which stays
        # correct even when a server silently caps the page size below what was requested.
        assert pages == ["1", "2", "3", "4"]


def test_cursor_pagination(run_rest) -> None:
    src = source(
        "x", entity={"path": "/v2/students", "pagination": {"type": "cursor", "page_size": 40}}
    )
    with MockRestServer(GEN) as srv:
        src["base_url"] = srv.base_url
        e = ent(run_rest(src))
        assert e.counters.loaded == 120
        assert sum(1 for p, _ in srv.requests if p == "/v2/students") == 3


def test_incremental_since_parameter_and_idempotent_rerun(run_rest) -> None:
    with MockRestServer(GEN) as srv:
        first = ent(run_rest(source(srv.base_url)))
        second = ent(run_rest(source(srv.base_url)))
        assert first.counters.loaded == 120
        # second run sends updated_since=<watermark>; only the boundary record returns, unchanged
        assert any("updated_since" in q for _, q in srv.requests)
        assert second.counters.loaded == 0 and second.counters.unchanged_skipped >= 1
        assert second.status == "NO_NEW_DATA"


def test_retries_on_503_and_429_then_succeeds(run_rest) -> None:
    with MockRestServer(GEN, fail_first_n=1, rate_limit_first_n=1) as srv:
        e = ent(run_rest(source(srv.base_url)))
        assert e.status == "SUCCEEDED" and e.counters.retries == 2


def test_gives_up_after_max_attempts(run_rest) -> None:
    with MockRestServer(GEN, fail_first_n=50) as srv:
        e = ent(run_rest(source(srv.base_url)))
        assert e.status == "FAILED" and "gave up" in e.failure["message"]
        assert e.counters.loaded == 0


def test_401_is_permanent_and_aborts_the_source(run_rest) -> None:
    with MockRestServer(GEN, token="right") as srv:
        src = source(srv.base_url, auth={"type": "bearer", "token_env": "API_TOKEN"})
        src["entities"].append(
            {
                **src["entities"][0],
                "name": "schools",
                "mapping": str(MAPPINGS_DIR / "sis_schools.yaml"),
                "path": "/v1/schools",
                "since_param": None,
            }
        )
        report = run_rest(src, env={"API_TOKEN": "wrong"})
        assert report.sources[0].status == "FAILED"
        assert len(report.sources[0].entities) == 1  # second entity never attempted
        assert len(srv.requests) == 1  # no retry on 401
        assert "credentials rejected" in report.sources[0].entities[0].failure["message"]


def test_bearer_token_is_sent_and_never_logged(run_rest, tmp_path) -> None:
    with MockRestServer(GEN, token="s3cr3t-token-value") as srv:
        src = source(srv.base_url, auth={"type": "bearer", "token_env": "API_TOKEN"})
        report = run_rest(src, env={"API_TOKEN": "s3cr3t-token-value"})
        assert ent(report).status == "SUCCEEDED"
    run_dir = tmp_path / "store" / "runs" / report.run_id
    for file in run_dir.iterdir():
        assert "s3cr3t-token-value" not in file.read_text()


def test_missing_token_env_is_a_config_failure(run_rest) -> None:
    src = source("http://127.0.0.1:1", auth={"type": "bearer", "token_env": "NOT_SET"})
    report = run_rest(src, env={})
    assert report.sources[0].status == "FAILED"
    assert "NOT_SET" in report.sources[0].failure["message"]


def test_redirects_are_refused(run_rest) -> None:
    with MockRestServer(GEN) as srv:
        e = ent(run_rest(source(srv.base_url, entity={"path": "/v1/redirect"})))
        assert e.status == "FAILED" and "redirect refused" in e.failure["message"]


def test_non_json_body_is_a_schema_failure(run_rest) -> None:
    with MockRestServer(GEN) as srv:
        e = ent(run_rest(source(srv.base_url, entity={"path": "/v1/badjson"})))
        assert e.status == "FAILED" and "not valid JSON" in e.failure["message"]


def test_404_is_permanent_not_retried(run_rest) -> None:
    with MockRestServer(GEN) as srv:
        e = ent(run_rest(source(srv.base_url, entity={"path": "/v1/nope"})))
        assert e.status == "FAILED" and len(srv.requests) == 1


def test_connection_refused_is_transient_then_exhausted(run_rest) -> None:
    e = ent(run_rest(source("http://127.0.0.1:1")))
    assert e.status == "FAILED" and e.counters.retries == 3  # max_attempts 4 -> 3 retries


def test_max_pages_guard(run_rest) -> None:
    with MockRestServer(GEN) as srv:
        src = source(srv.base_url, max_pages=1)
        e = ent(run_rest(src))
        assert e.status == "FAILED" and "max_pages" in e.failure["message"]


def test_failed_pagination_leaves_no_partial_bronze(run_rest, tmp_path) -> None:
    with MockRestServer(GEN) as srv:
        srv.fail_first_n = 0
        src = source(srv.base_url, max_pages=2)  # fails on page 3 after pages 1-2 were read
        e = ent(run_rest(src))
        assert e.status == "FAILED" and e.counters.loaded == 0
        assert BronzeStore(tmp_path / "store").iter_records() == []


# ----------------------------------------------------------------------------- transport safety
@pytest.fixture
def make_connector(write_ingest_config):
    def _make(**kw):
        cfg = load_config(write_ingest_config({"api": source(**kw)})).source("api")
        return build_connector("api", cfg, retry_policy=RetryPolicy(), env={})

    return _make


def test_plain_http_to_a_remote_host_is_refused(make_connector) -> None:
    with pytest.raises(ConfigError, match="plain http"):
        make_connector(base_url="http://sis.example.org")


def test_plain_http_allowed_only_with_explicit_opt_in(make_connector) -> None:
    make_connector(base_url="http://sis.example.org", allow_insecure_http=True)
    make_connector(base_url="http://localhost:9")
    make_connector(base_url="https://sis.example.org")


def test_host_allowlist_and_embedded_credentials(make_connector) -> None:
    with pytest.raises(ConfigError, match="allowed_hosts"):
        make_connector(base_url="https://evil.example.org", allowed_hosts=["sis.example.org"])
    with pytest.raises(ConfigError, match="credentials"):
        make_connector(base_url="https://user:pw@sis.example.org")


def test_secret_value_in_config_is_rejected_by_schema(write_ingest_config) -> None:
    bad = source("https://sis.example.org")
    bad["auth"] = {"type": "bearer", "token": "abc"}  # not a known key: secrets never in config
    with pytest.raises(ConfigError):
        load_config(write_ingest_config({"api": bad}))
