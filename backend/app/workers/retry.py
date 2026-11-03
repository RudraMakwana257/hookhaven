"""Retry policy: exponential-ish backoff with jitter."""

import random
from collections.abc import Callable
from datetime import timedelta

# Delay before retry N (after attempt N failed). Attempts beyond the table reuse the last value.
RETRY_DELAYS = (
    timedelta(seconds=10),
    timedelta(seconds=30),
    timedelta(minutes=2),
    timedelta(minutes=10),
    timedelta(hours=1),
    timedelta(hours=6),
)

JITTER_SPREAD = 0.4  # delay is scaled by a factor in [0.8, 1.2]


def next_retry_delay(
    failed_attempt: int,
    max_attempts: int,
    rng: Callable[[], float] = random.random,
) -> timedelta | None:
    """Delay before the next attempt, or None if retries are exhausted (-> dead-letter).

    Jitter matters: when an endpoint recovers from an outage, thousands of deliveries that failed
    together would otherwise all retry in the same instant and knock it over again.
    """
    if failed_attempt >= max_attempts:
        return None
    base = RETRY_DELAYS[min(failed_attempt - 1, len(RETRY_DELAYS) - 1)]
    return base * (1 - JITTER_SPREAD / 2 + JITTER_SPREAD * rng())
