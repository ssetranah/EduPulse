"""Retry with exponential backoff and jitter.

Only ``TransientSourceError`` is retried. Sleeping and randomness are injectable so tests run
instantly and deterministically.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from .config import RetryConfig
from .errors import RetryExhaustedError, TransientSourceError

T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 4
    base_delay: float = 0.5
    max_delay: float = 30.0
    multiplier: float = 2.0
    jitter: bool = True

    @classmethod
    def from_config(cls, config: RetryConfig) -> RetryPolicy:
        return cls(
            max_attempts=config.max_attempts,
            base_delay=config.base_delay_seconds,
            max_delay=config.max_delay_seconds,
            multiplier=config.multiplier,
            jitter=config.jitter,
        )


def backoff_delay(
    policy: RetryPolicy,
    attempt: int,
    rng: random.Random,
    retry_after: float | None = None,
) -> float:
    """Delay before retry number ``attempt`` (1 = first retry).

    Exponential: ``base * multiplier**(attempt-1)`` capped at ``max_delay``. With jitter, "full
    jitter" is used (uniform in ``[0, ceiling]``) to avoid synchronized retry storms. A server
    ``Retry-After`` hint raises the floor but never beyond ``max_delay``.
    """
    ceiling = min(policy.max_delay, policy.base_delay * (policy.multiplier ** (attempt - 1)))
    delay = rng.uniform(0, ceiling) if policy.jitter else ceiling
    if retry_after is not None:
        delay = max(delay, min(retry_after, policy.max_delay))
    return delay


def retry_call(
    fn: Callable[[], T],
    policy: RetryPolicy,
    *,
    operation: str = "operation",
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
    on_retry: Callable[[int, float, TransientSourceError], None] | None = None,
) -> T:
    """Call ``fn`` until it succeeds, raising ``RetryExhaustedError`` after the last attempt."""
    rng = rng or random.Random()  # noqa: S311 - jitter only, not security sensitive
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return fn()
        except TransientSourceError as exc:
            if attempt == policy.max_attempts:
                raise RetryExhaustedError(
                    f"{operation}: gave up after {attempt} attempts ({exc})",
                    attempts=attempt,
                    last_error=exc,
                ) from exc
            delay = backoff_delay(policy, attempt, rng, exc.retry_after)
            if on_retry is not None:
                on_retry(attempt, delay, exc)
            sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover
