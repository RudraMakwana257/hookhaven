"""Fan-out of state changes to browsers: worker/API -> Redis pub/sub -> SSE streams.

Pub/sub is fire-and-forget: a subscriber that is not connected misses messages. That is fine
because messages are *hints* ("something changed"); clients refetch on (re)connect, and
Postgres remains the source of truth. One channel per user, so a stream only ever receives
its owner's updates.
"""

import json
import time
import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

log = structlog.get_logger(__name__)
CHANNEL_PREFIX = "hookhaven:rt:"


def channel_for(user_id: uuid.UUID) -> str:
    return f"{CHANNEL_PREFIX}{user_id}"


class Subscription:
    def __init__(self, pubsub: Any) -> None:
        self._pubsub = pubsub

    async def next_message(self, wait_seconds: float) -> dict[str, Any] | None:
        """The next message, or None if nothing arrived within `wait_seconds`.

        redis-py returns None straight away when the frame it read was the subscribe
        confirmation (even if a real message is queued behind it), so keep reading until the
        deadline instead of trusting a single None.
        """
        deadline = time.monotonic() + wait_seconds
        while True:
            raw = await self._pubsub.get_message(
                ignore_subscribe_messages=True, timeout=max(0.0, deadline - time.monotonic())
            )
            if raw is not None:
                break
            if time.monotonic() >= deadline:
                return None
        data = raw["data"]
        try:
            parsed = json.loads(data.decode() if isinstance(data, bytes) else data)
        except (ValueError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else None


class EventBus:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def publish(self, user_id: uuid.UUID, message: Mapping[str, Any]) -> None:
        await self._redis.publish(
            channel_for(user_id), json.dumps(message, separators=(",", ":"), default=str)
        )

    @asynccontextmanager
    async def subscribe(self, user_id: uuid.UUID) -> AsyncIterator[Subscription]:
        pubsub = self._redis.pubsub()
        channel = channel_for(user_id)
        await pubsub.subscribe(channel)
        try:
            yield Subscription(pubsub)
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()  # type: ignore[no-untyped-call]


async def publish_best_effort(
    bus: EventBus | None, user_id: uuid.UUID, message: Mapping[str, Any]
) -> None:
    """Live updates are a convenience: a Redis hiccup must never fail a delivery or a request."""
    if bus is None:
        return
    try:
        await bus.publish(user_id, message)
    except (RedisError, OSError):
        log.warning("realtime_publish_failed", type=message.get("type"), exc_info=True)
