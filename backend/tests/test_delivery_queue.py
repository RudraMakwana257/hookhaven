import uuid
from collections.abc import AsyncIterator

import pytest
from fakeredis import FakeAsyncRedis

from app.queue.delivery_queue import PROCESSING_KEY, READY_KEY, SCHEDULED_KEY, DeliveryQueue

NOW = 1_800_000_000.0


@pytest.fixture
async def redis() -> AsyncIterator[FakeAsyncRedis]:
    client = FakeAsyncRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture
def queue(redis: FakeAsyncRedis) -> DeliveryQueue:
    return DeliveryQueue(redis)


async def test_claim_moves_id_to_processing_with_a_lease(
    queue: DeliveryQueue, redis: FakeAsyncRedis
) -> None:
    a = uuid.uuid4()
    await queue.enqueue([a])

    assert await queue.claim(now=NOW, lease_seconds=60) == a
    assert await queue.stats() == {"ready": 0, "scheduled": 0, "processing": 1}
    assert await redis.zscore(PROCESSING_KEY, str(a)) == NOW + 60  # exact: no float rounding


async def test_claim_on_empty_queue_returns_none(queue: DeliveryQueue) -> None:
    assert await queue.claim(now=NOW, lease_seconds=60) is None


async def test_queue_is_fifo(queue: DeliveryQueue) -> None:
    ids = [uuid.uuid4() for _ in range(3)]
    await queue.enqueue(ids[:2])
    await queue.enqueue(ids[2:])
    claimed = [await queue.claim(now=NOW, lease_seconds=60) for _ in range(3)]
    assert claimed == ids


async def test_ack_releases_the_lease(queue: DeliveryQueue) -> None:
    a = uuid.uuid4()
    await queue.enqueue([a])
    await queue.claim(now=NOW, lease_seconds=60)
    await queue.ack(a)
    assert await queue.stats() == {"ready": 0, "scheduled": 0, "processing": 0}


async def test_schedule_retry_is_a_single_atomic_move(
    queue: DeliveryQueue, redis: FakeAsyncRedis
) -> None:
    a = uuid.uuid4()
    await queue.enqueue([a])
    await queue.claim(now=NOW, lease_seconds=60)

    await queue.schedule_retry(a, due_at=NOW + 30)

    assert await queue.stats() == {"ready": 0, "scheduled": 1, "processing": 0}
    assert await redis.zscore(SCHEDULED_KEY, str(a)) == NOW + 30


async def test_promote_due_moves_only_due_retries(queue: DeliveryQueue) -> None:
    soon, later = uuid.uuid4(), uuid.uuid4()
    await queue.enqueue([soon, later])
    await queue.claim(now=NOW, lease_seconds=60)
    await queue.claim(now=NOW, lease_seconds=60)
    await queue.schedule_retry(soon, due_at=NOW + 10)
    await queue.schedule_retry(later, due_at=NOW + 100)

    assert await queue.promote_due(now=NOW + 5) == 0
    assert await queue.promote_due(now=NOW + 10) == 1
    assert await queue.stats() == {"ready": 1, "scheduled": 1, "processing": 0}
    assert await queue.claim(now=NOW + 10, lease_seconds=60) == soon


async def test_reap_expired_requeues_only_dead_leases_and_they_go_first(
    queue: DeliveryQueue,
) -> None:
    crashed, healthy, waiting = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await queue.enqueue([crashed])
    await queue.claim(now=NOW, lease_seconds=60)  # lease ends NOW+60
    await queue.enqueue([healthy])
    await queue.claim(now=NOW + 50, lease_seconds=60)  # lease ends NOW+110
    await queue.enqueue([waiting])

    assert await queue.reap_expired(now=NOW + 59) == 0
    assert await queue.reap_expired(now=NOW + 61) == 1  # only `crashed`
    assert await queue.stats() == {"ready": 2, "scheduled": 0, "processing": 1}
    # recovered work jumps the queue
    assert await queue.claim(now=NOW + 61, lease_seconds=60) == crashed
    assert await queue.claim(now=NOW + 61, lease_seconds=60) == waiting


async def test_an_id_is_never_in_two_places(queue: DeliveryQueue, redis: FakeAsyncRedis) -> None:
    a = uuid.uuid4()
    await queue.enqueue([a])
    await queue.claim(now=NOW, lease_seconds=60)
    await queue.schedule_retry(a, due_at=NOW + 5)
    await queue.promote_due(now=NOW + 5)
    await queue.claim(now=NOW + 5, lease_seconds=60)
    places = [
        await redis.llen(READY_KEY),
        await redis.zcard(SCHEDULED_KEY),
        await redis.zcard(PROCESSING_KEY),
    ]
    assert sorted(places) == [0, 0, 1]
