import asyncio
from collections.abc import Callable

from fakeredis import FakeAsyncRedis
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.queue.delivery_queue import LOCK_PREFIX, RECONCILED_MARKER_PREFIX, DeliveryQueue
from app.workers.delivery_worker import Worker
from app.workers.reconciler import Reconciler

from .support import EMPTY, Clock, Receiver, get_delivery, setup_deliveries, wait_until


def _reconciler(
    session_maker: async_sessionmaker[AsyncSession],
    queue: DeliveryQueue,
    clock: Clock,
    **settings: int,
) -> Reconciler:
    return Reconciler(
        session_factory=session_maker,
        queue=queue,
        settings=Settings(_env_file=None, reconcile_grace_seconds=120, **settings),
        clock=clock,
    )


async def test_recovers_a_delivery_whose_enqueue_was_lost(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_maker: async_sessionmaker[AsyncSession],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
    receiver: Receiver,
    clock: Clock,
) -> None:
    """Crash between COMMIT and enqueue: the delivery is in Postgres but not in Redis."""
    _, _, [did] = await setup_deliveries(client, auth_headers, delivery_queue, enqueue=False)
    reconciler = _reconciler(session_maker, delivery_queue, clock)

    assert await reconciler.sweep() == 0  # fresh work is the worker's business, not ours
    clock.advance(130)  # now overdue by more than the grace period
    assert await reconciler.sweep() == 1

    await make_worker().run_once()
    assert (await get_delivery(client, auth_headers, did))["status"] == "succeeded"
    assert len(receiver.requests) == 1


async def test_sweeps_do_not_pile_up_duplicates(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_maker: async_sessionmaker[AsyncSession],
    delivery_queue: DeliveryQueue,
    redis: FakeAsyncRedis,
    clock: Clock,
) -> None:
    _, _, [did] = await setup_deliveries(client, auth_headers, delivery_queue, enqueue=False)
    reconciler = _reconciler(session_maker, delivery_queue, clock)
    clock.advance(130)

    assert await reconciler.sweep() == 1
    assert await reconciler.sweep() == 0  # still pending (workers busy), but already re-queued
    assert (await delivery_queue.stats())["ready"] == 1
    assert 0 < await redis.ttl(f"{RECONCILED_MARKER_PREFIX}{did}") <= 120


async def test_recovers_after_redis_loses_everything(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_maker: async_sessionmaker[AsyncSession],
    delivery_queue: DeliveryQueue,
    redis: FakeAsyncRedis,
    make_worker: Callable[..., Worker],
    receiver: Receiver,
    clock: Clock,
) -> None:
    _, _, [did] = await setup_deliveries(client, auth_headers, delivery_queue)
    receiver.script = [500, 200]
    worker = make_worker()
    await worker.run_once()  # fails; retry is parked in Redis only
    assert (await delivery_queue.stats())["scheduled"] == 1

    await redis.flushall()  # Redis restarts without persistence
    assert await delivery_queue.stats() == EMPTY

    clock.advance(10 + 120 + 1)  # retry time + grace
    reconciler = _reconciler(session_maker, delivery_queue, clock)
    assert await reconciler.sweep() == 1
    await worker.run_once()

    delivery = await get_delivery(client, auth_headers, did)
    assert delivery["status"] == "succeeded"
    assert [a["attempt_number"] for a in delivery["attempts"]] == [1, 2]


async def test_leaves_healthy_and_finished_work_alone(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_maker: async_sessionmaker[AsyncSession],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
    receiver: Receiver,
    clock: Clock,
) -> None:
    _, _, ids = await setup_deliveries(client, auth_headers, delivery_queue, events=2)
    receiver.script = [200, 500]
    worker = make_worker()
    await worker.run_once()  # one succeeded
    await worker.run_once()  # one failed -> retry due in 10s
    reconciler = _reconciler(session_maker, delivery_queue, clock)

    clock.advance(20)  # the retry is due, but well within the grace period
    assert await reconciler.sweep() == 0

    clock.advance(1000)  # long after: the succeeded one must still be ignored
    assert await reconciler.sweep() == 1  # only the pending retry
    assert (await get_delivery(client, auth_headers, ids[0]))["status"] == "succeeded"


async def test_batch_size_bounds_each_sweep(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_maker: async_sessionmaker[AsyncSession],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
    clock: Clock,
) -> None:
    await setup_deliveries(client, auth_headers, delivery_queue, events=5, enqueue=False)
    reconciler = _reconciler(session_maker, delivery_queue, clock, reconcile_batch_size=2)
    clock.advance(130)
    worker = make_worker()

    assert await reconciler.sweep() == 2
    await worker.run_once()
    await worker.run_once()
    assert await reconciler.sweep() == 2  # the next oldest, as the first two completed
    assert (await delivery_queue.stats())["ready"] == 2


async def test_only_the_lock_holder_sweeps(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_maker: async_sessionmaker[AsyncSession],
    delivery_queue: DeliveryQueue,
    redis: FakeAsyncRedis,
    clock: Clock,
) -> None:
    await setup_deliveries(client, auth_headers, delivery_queue, enqueue=False)
    clock.advance(130)
    reconciler = _reconciler(session_maker, delivery_queue, clock)

    assert await delivery_queue.acquire_lock("reconciler", ttl_seconds=30)  # another replica
    assert await reconciler.sweep_if_leader() == 0
    assert (await delivery_queue.stats())["ready"] == 0

    await redis.delete(f"{LOCK_PREFIX}reconciler")
    assert await reconciler.sweep_if_leader() == 1


async def test_worker_run_loop_reconciles_on_its_own(
    client: AsyncClient,
    auth_headers: dict[str, str],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
    clock: Clock,
) -> None:
    _, _, [did] = await setup_deliveries(client, auth_headers, delivery_queue, enqueue=False)
    clock.advance(130)
    stop = asyncio.Event()
    task = asyncio.create_task(make_worker(worker_concurrency=1).run(stop))

    async def delivered() -> bool:
        return (await get_delivery(client, auth_headers, did))["status"] == "succeeded"

    try:
        await wait_until(delivered)
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=5)
