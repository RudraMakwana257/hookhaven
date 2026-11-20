import asyncio
from collections.abc import AsyncIterator

import pytest
from fakeredis import FakeAsyncRedis

from app.core.rate_limit import KEY_PREFIX, RateLimiter, RateLimitPolicy

T = 1_800_000_000.0
POLICY = RateLimitPolicy(capacity=3, refill_per_second=1.0)


@pytest.fixture
async def redis() -> AsyncIterator[FakeAsyncRedis]:
    client = FakeAsyncRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture
def limiter(redis: FakeAsyncRedis) -> RateLimiter:
    return RateLimiter(redis)


async def test_allows_a_burst_then_rejects_with_a_retry_hint(limiter: RateLimiter) -> None:
    results = [await limiter.consume("api", "u1", POLICY, now=T) for _ in range(4)]

    assert [r.allowed for r in results] == [True, True, True, False]
    assert [r.remaining for r in results] == [2, 1, 0, 0]
    assert results[3].limit == 3
    assert results[3].retry_after_seconds == pytest.approx(1.0)  # one token takes 1s at 1/s


async def test_tokens_refill_at_the_configured_rate(limiter: RateLimiter) -> None:
    for _ in range(3):
        await limiter.consume("api", "u1", POLICY, now=T)

    assert not (await limiter.consume("api", "u1", POLICY, now=T + 0.5)).allowed
    assert (await limiter.consume("api", "u1", POLICY, now=T + 1.0)).allowed  # one token is back
    assert not (await limiter.consume("api", "u1", POLICY, now=T + 1.0)).allowed


async def test_fractional_rates(limiter: RateLimiter) -> None:
    slow = RateLimitPolicy(capacity=1, refill_per_second=0.5)  # one request per 2 seconds
    assert (await limiter.consume("s", "u", slow, now=T)).allowed
    denied = await limiter.consume("s", "u", slow, now=T + 1)
    assert not denied.allowed
    assert denied.retry_after_seconds == pytest.approx(1.0)
    assert (await limiter.consume("s", "u", slow, now=T + 2)).allowed


async def test_idle_time_never_banks_more_than_the_capacity(limiter: RateLimiter) -> None:
    await limiter.consume("api", "u1", POLICY, now=T)
    later = [await limiter.consume("api", "u1", POLICY, now=T + 10_000) for _ in range(4)]
    assert [r.allowed for r in later] == [True, True, True, False]


async def test_identities_and_scopes_have_separate_buckets(limiter: RateLimiter) -> None:
    for _ in range(3):
        await limiter.consume("api", "u1", POLICY, now=T)
    assert not (await limiter.consume("api", "u1", POLICY, now=T)).allowed
    assert (await limiter.consume("api", "u2", POLICY, now=T)).allowed  # other identity
    assert (await limiter.consume("ingest", "u1", POLICY, now=T)).allowed  # other scope


async def test_cost_spends_multiple_tokens(limiter: RateLimiter) -> None:
    assert (await limiter.consume("b", "u", POLICY, now=T, cost=2)).remaining == 1
    assert not (await limiter.consume("b", "u", POLICY, now=T, cost=2)).allowed
    assert (await limiter.consume("b", "u", POLICY, now=T, cost=1)).allowed  # partial still works


async def test_a_clock_that_goes_backwards_does_not_mint_tokens(limiter: RateLimiter) -> None:
    for _ in range(3):
        await limiter.consume("api", "u1", POLICY, now=T)

    assert not (await limiter.consume("api", "u1", POLICY, now=T - 100)).allowed
    assert not (await limiter.consume("api", "u1", POLICY, now=T)).allowed
    assert (await limiter.consume("api", "u1", POLICY, now=T + 1)).allowed  # refill from the real T


async def test_idle_buckets_expire(limiter: RateLimiter, redis: FakeAsyncRedis) -> None:
    await limiter.consume("api", "u1", POLICY, now=T)
    ttl_ms = await redis.pttl(f"{KEY_PREFIX}api:u1")
    assert 0 < ttl_ms <= 4_000  # capacity/rate = 3s, plus 1s slack: no unbounded key growth


async def test_concurrent_requests_cannot_overspend(limiter: RateLimiter) -> None:
    """The check-and-spend is one atomic script: 50 racing requests, exactly `capacity` pass."""
    policy = RateLimitPolicy(capacity=5, refill_per_second=0.0001)
    results = await asyncio.gather(
        *(limiter.consume("api", "u1", policy, now=T) for _ in range(50))
    )
    assert sum(r.allowed for r in results) == 5
