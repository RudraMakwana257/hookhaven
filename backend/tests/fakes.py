import uuid
from collections.abc import Sequence

from redis.exceptions import ConnectionError as RedisConnectionError


class FakeQueue:
    """Records what the API enqueued. Can simulate a Redis outage."""

    def __init__(self) -> None:
        self.batches: list[list[uuid.UUID]] = []
        self.down = False

    async def enqueue(self, delivery_ids: Sequence[uuid.UUID]) -> None:
        if self.down:
            raise RedisConnectionError("redis is down")
        self.batches.append(list(delivery_ids))

    @property
    def enqueued(self) -> list[uuid.UUID]:
        return [d for batch in self.batches for d in batch]
