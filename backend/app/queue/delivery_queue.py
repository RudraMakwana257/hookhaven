"""Reliable Redis queue for deliveries (see docs/adr/0001 and 0005).

Three structures, all holding delivery *ids* only (Postgres is the source of truth):

  READY       list  - ids to deliver now
  SCHEDULED   zset  - ids waiting for a retry, score = due time
  PROCESSING  zset  - ids claimed by a worker, score = lease deadline

A claim moves an id READY -> PROCESSING with a lease. If the worker dies, the lease expires and
`reap_expired` puts the id back. Every multi-key transition is a Lua script, so a crash can never
leave an id in zero places or in two places.

All methods take `now` explicitly (epoch seconds) so behaviour is deterministic in tests.
"""

import uuid
from collections.abc import Sequence
from typing import Protocol

from redis.asyncio import Redis

READY_KEY = "hookhaven:deliveries:ready"
SCHEDULED_KEY = "hookhaven:deliveries:scheduled"
PROCESSING_KEY = "hookhaven:deliveries:processing"

# Numbers are passed as strings and handed straight to ZADD: Lua's tostring() would round
# epoch timestamps to 14 significant digits.
_CLAIM = """
local id = redis.call('RPOP', KEYS[1])
if not id then return false end
redis.call('ZADD', KEYS[2], ARGV[1], id)
return id
"""

_SCHEDULE_RETRY = """
redis.call('ZREM', KEYS[1], ARGV[1])
redis.call('ZADD', KEYS[2], ARGV[2], ARGV[1])
return 1
"""

_PROMOTE_DUE = """
local ids = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, ARGV[2])
for _, id in ipairs(ids) do
  redis.call('ZREM', KEYS[1], id)
  redis.call('LPUSH', KEYS[2], id)
end
return #ids
"""

# Recovered ids go to the tail (RPUSH) = the end consumers pop from, so they are retried first.
_REAP_EXPIRED = """
local ids = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, ARGV[2])
for _, id in ipairs(ids) do
  redis.call('ZREM', KEYS[1], id)
  redis.call('RPUSH', KEYS[2], id)
end
return #ids
"""

# Push an id only if we have not pushed it recently (marker with TTL). Lets the reconciler run
# every few seconds without flooding the queue with duplicates while a backlog drains.
_ENQUEUE_ONCE = """
local pushed = 0
for i = 2, #ARGV do
  if redis.call('SET', KEYS[2] .. ARGV[i], '1', 'NX', 'EX', ARGV[1]) then
    redis.call('LPUSH', KEYS[1], ARGV[i])
    pushed = pushed + 1
  end
end
return pushed
"""

RECONCILED_MARKER_PREFIX = "hookhaven:reconciled:"
LOCK_PREFIX = "hookhaven:lock:"


class DeliveryPublisher(Protocol):
    async def enqueue(self, delivery_ids: Sequence[uuid.UUID]) -> None: ...


class DeliveryQueue:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._claim = redis.register_script(_CLAIM)
        self._schedule_retry = redis.register_script(_SCHEDULE_RETRY)
        self._promote_due = redis.register_script(_PROMOTE_DUE)
        self._reap_expired = redis.register_script(_REAP_EXPIRED)
        self._enqueue_once = redis.register_script(_ENQUEUE_ONCE)

    async def enqueue(self, delivery_ids: Sequence[uuid.UUID]) -> None:
        """Push to the head; consumers pop from the tail, so the queue is FIFO."""
        if not delivery_ids:
            return
        await self._redis.lpush(READY_KEY, *(str(d) for d in delivery_ids))

    async def claim(self, *, now: float, lease_seconds: float) -> uuid.UUID | None:
        """Atomically take the next ready id and lease it. None if the queue is empty."""
        raw = await self._claim(keys=[READY_KEY, PROCESSING_KEY], args=[str(now + lease_seconds)])
        if raw is None:
            return None
        return uuid.UUID(raw.decode() if isinstance(raw, bytes) else str(raw))

    async def ack(self, delivery_id: uuid.UUID) -> None:
        """Done with this id (succeeded, dead, or no longer relevant)."""
        await self._redis.zrem(PROCESSING_KEY, str(delivery_id))

    async def schedule_retry(self, delivery_id: uuid.UUID, *, due_at: float) -> None:
        """Atomically release the lease and park the id until `due_at`."""
        await self._schedule_retry(
            keys=[PROCESSING_KEY, SCHEDULED_KEY], args=[str(delivery_id), str(due_at)]
        )

    async def promote_due(self, *, now: float, limit: int = 100) -> int:
        """Move retries whose time has come to READY. Returns how many moved."""
        return int(
            await self._promote_due(keys=[SCHEDULED_KEY, READY_KEY], args=[str(now), str(limit)])
        )

    async def reap_expired(self, *, now: float, limit: int = 100) -> int:
        """Return ids whose worker died (lease expired) to READY. Returns how many moved."""
        return int(
            await self._reap_expired(keys=[PROCESSING_KEY, READY_KEY], args=[str(now), str(limit)])
        )

    async def enqueue_once(
        self, delivery_ids: Sequence[uuid.UUID], *, dedupe_ttl_seconds: int
    ) -> int:
        """Like `enqueue`, but skips ids pushed within the last `dedupe_ttl_seconds`."""
        if not delivery_ids:
            return 0
        return int(
            await self._enqueue_once(
                keys=[READY_KEY, RECONCILED_MARKER_PREFIX],
                args=[str(dedupe_ttl_seconds), *(str(d) for d in delivery_ids)],
            )
        )

    async def acquire_lock(self, name: str, *, ttl_seconds: int) -> bool:
        """Best-effort mutual exclusion between worker replicas (SET NX EX)."""
        return bool(await self._redis.set(f"{LOCK_PREFIX}{name}", "1", nx=True, ex=ttl_seconds))

    async def stats(self) -> dict[str, int]:
        return {
            "ready": int(await self._redis.llen(READY_KEY)),
            "scheduled": int(await self._redis.zcard(SCHEDULED_KEY)),
            "processing": int(await self._redis.zcard(PROCESSING_KEY)),
        }
