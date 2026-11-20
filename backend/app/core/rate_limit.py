"""Token-bucket rate limiter on Redis.

A bucket holds up to `capacity` tokens and refills at `refill_per_second`. Each request spends
one token. That allows bursts up to `capacity` while enforcing the long-run rate, which is what
API clients actually need (unlike a fixed window, there is no boundary spike).

The read-refill-spend-write cycle is one Lua script, so it is atomic across API replicas.
`now` is passed in (not read from Redis) so tests control time; replicas' clocks should be
NTP-synced, and a clock that goes backwards never mints tokens.
"""

import math
from dataclasses import dataclass
from typing import Final

from redis.asyncio import Redis

KEY_PREFIX: Final = "hookhaven:rl:"

_BUCKET_SCRIPT = """
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])

local data = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts = tonumber(data[2])
local stored_ts = ARGV[3]
if tokens == nil then
  tokens = capacity
  ts = now
end
if now >= ts then
  tokens = math.min(capacity, tokens + (now - ts) * rate)
else
  stored_ts = data[2]  -- clock went backwards: no refill, keep the newer ts
end

local allowed = 0
local retry_after = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
else
  retry_after = (cost - tokens) / rate
end

redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'ts', stored_ts)
-- an idle bucket is full again after capacity/rate seconds, so it can be forgotten
redis.call('PEXPIRE', KEYS[1], math.ceil(capacity / rate * 1000) + 1000)
return {allowed, tostring(tokens), tostring(retry_after)}
"""


@dataclass(frozen=True)
class RateLimitPolicy:
    capacity: int
    refill_per_second: float


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: float


class RateLimiter:
    def __init__(self, redis: Redis) -> None:
        self._script = redis.register_script(_BUCKET_SCRIPT)

    async def consume(
        self, scope: str, identity: str, policy: RateLimitPolicy, *, now: float, cost: int = 1
    ) -> RateLimitResult:
        allowed, tokens, retry_after = await self._script(
            keys=[f"{KEY_PREFIX}{scope}:{identity}"],
            args=[str(policy.capacity), str(policy.refill_per_second), str(now), str(cost)],
        )
        return RateLimitResult(
            allowed=bool(int(allowed)),
            limit=policy.capacity,
            remaining=max(0, math.floor(float(tokens))),
            retry_after_seconds=float(retry_after),
        )
