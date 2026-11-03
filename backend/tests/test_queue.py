import uuid

from fakeredis import FakeAsyncRedis

from app.queue.delivery_queue import READY_KEY, DeliveryQueue


async def test_enqueue_is_fifo() -> None:
    redis = FakeAsyncRedis(decode_responses=True)
    queue = DeliveryQueue(redis)
    first, second, third = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    await queue.enqueue([first, second])
    await queue.enqueue([third])

    assert await redis.llen(READY_KEY) == 3
    popped = [await redis.rpop(READY_KEY) for _ in range(3)]  # consumers pop from the tail
    assert popped == [str(first), str(second), str(third)]


async def test_enqueue_nothing_is_a_noop() -> None:
    redis = FakeAsyncRedis(decode_responses=True)
    await DeliveryQueue(redis).enqueue([])
    assert await redis.llen(READY_KEY) == 0
