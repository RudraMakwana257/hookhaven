from datetime import timedelta

import pytest

from app.workers.retry import RETRY_DELAYS, next_retry_delay


def test_delay_grows_with_each_failed_attempt() -> None:
    mid = lambda: 0.5  # noqa: E731  (jitter factor exactly 1.0)
    delays = [next_retry_delay(n, 99, mid) for n in range(1, 7)]
    assert delays == list(RETRY_DELAYS)
    assert delays == sorted(delays)  # type: ignore[type-var]


def test_delays_beyond_the_table_reuse_the_last_value() -> None:
    assert next_retry_delay(20, 99, lambda: 0.5) == RETRY_DELAYS[-1]


def test_jitter_stays_within_twenty_percent() -> None:
    base = RETRY_DELAYS[2]
    low = next_retry_delay(3, 99, lambda: 0.0)
    high = next_retry_delay(3, 99, lambda: 1.0)
    assert low == pytest.approx(base * 0.8, abs=timedelta(milliseconds=1))  # type: ignore[arg-type]
    assert high == pytest.approx(base * 1.2, abs=timedelta(milliseconds=1))  # type: ignore[arg-type]


def test_none_when_attempts_are_exhausted() -> None:
    assert next_retry_delay(3, 3) is None
    assert next_retry_delay(4, 3) is None
    assert next_retry_delay(2, 3) is not None
    assert next_retry_delay(1, 1) is None  # max_attempts=1 means "never retry"
