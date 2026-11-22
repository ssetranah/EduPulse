"""Retry/backoff behavior (no real sleeping)."""

from __future__ import annotations

import random

import pytest

from ingestion.errors import PermanentSourceError, RetryExhaustedError, TransientSourceError
from ingestion.retry import RetryPolicy, backoff_delay, retry_call

pytestmark = pytest.mark.unit


def _flaky(failures: int, exc: Exception | None = None):
    state = {"calls": 0}

    def fn() -> str:
        state["calls"] += 1
        if state["calls"] <= failures:
            raise exc or TransientSourceError("boom")
        return "ok"

    return fn, state


def test_succeeds_after_transient_failures() -> None:
    fn, state = _flaky(2)
    sleeps: list[float] = []
    assert retry_call(fn, RetryPolicy(max_attempts=4), sleep=sleeps.append) == "ok"
    assert state["calls"] == 3 and len(sleeps) == 2


def test_exhaustion_raises_with_attempt_count() -> None:
    fn, _ = _flaky(99)
    with pytest.raises(RetryExhaustedError) as info:
        retry_call(fn, RetryPolicy(max_attempts=3), sleep=lambda _: None)
    assert info.value.attempts == 3
    assert isinstance(info.value.last_error, TransientSourceError)


def test_permanent_errors_are_not_retried() -> None:
    fn, state = _flaky(5, PermanentSourceError("nope"))
    with pytest.raises(PermanentSourceError):
        retry_call(fn, RetryPolicy(max_attempts=4), sleep=lambda _: None)
    assert state["calls"] == 1


def test_backoff_is_exponential_and_capped_without_jitter() -> None:
    policy = RetryPolicy(base_delay=1, multiplier=2, max_delay=5, jitter=False)
    rng = random.Random(0)
    assert [backoff_delay(policy, n, rng) for n in (1, 2, 3, 4)] == [1, 2, 4, 5]


def test_full_jitter_stays_within_ceiling() -> None:
    policy = RetryPolicy(base_delay=1, multiplier=2, max_delay=8, jitter=True)
    rng = random.Random(1)
    assert all(0 <= backoff_delay(policy, 3, rng) <= 4 for _ in range(200))


def test_retry_after_raises_the_floor_but_respects_max() -> None:
    policy = RetryPolicy(base_delay=0.1, max_delay=10, jitter=False)
    rng = random.Random(0)
    assert backoff_delay(policy, 1, rng, retry_after=3) == 3
    assert backoff_delay(policy, 1, rng, retry_after=999) == 10


def test_on_retry_callback_receives_attempts() -> None:
    fn, _ = _flaky(2)
    seen: list[int] = []
    retry_call(fn, RetryPolicy(), sleep=lambda _: None, on_retry=lambda a, _d, _e: seen.append(a))
    assert seen == [1, 2]
