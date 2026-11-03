"""Redis-backed delivery queue (see docs/adr/0001).

Redis only carries delivery *ids*. Postgres is the source of truth, so losing Redis loses no
data: the reconciler (phase 6) re-enqueues anything still pending.
"""

import uuid
from collections.abc import Sequence
from typing import Protocol

from redis.asyncio import Redis

READY_KEY = "hookhaven:deliveries:ready"


class DeliveryPublisher(Protocol):
    async def enqueue(self, delivery_ids: Sequence[uuid.UUID]) -> None: ...


class DeliveryQueue:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def enqueue(self, delivery_ids: Sequence[uuid.UUID]) -> None:
        """Push to the head; consumers pop from the tail, so the queue is FIFO."""
        if not delivery_ids:
            return
        await self._redis.lpush(READY_KEY, *(str(d) for d in delivery_ids))
