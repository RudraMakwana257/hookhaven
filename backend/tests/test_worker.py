import asyncio
import json
import uuid
from collections.abc import Callable
from datetime import timedelta

import httpx
import pytest
from fakeredis import FakeAsyncRedis
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.signing import verify_signature
from app.models import Delivery, DeliveryAttempt, Endpoint
from app.queue.delivery_queue import SCHEDULED_KEY, DeliveryQueue
from app.workers.delivery_worker import Worker

from .conftest import PUBLIC_IP, register_and_login
from .support import EMPTY, Clock, Receiver, get_delivery, setup_deliveries, wait_until

_setup = setup_deliveries
_delivery = get_delivery
_wait_until = wait_until


# ------------------------------------------------------------------ happy path


async def test_successful_delivery_is_signed_pinned_and_recorded(
    client: AsyncClient,
    auth_headers: dict[str, str],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
    receiver: Receiver,
    clock: Clock,
) -> None:
    endpoint, event, [delivery_id] = await _setup(client, auth_headers, delivery_queue)

    assert await make_worker().run_once() is True

    request = receiver.requests[0]
    assert request.method == "POST"
    assert request.url.host == PUBLIC_IP  # pinned to the validated address
    assert request.headers["host"] == "hooks.example.com"
    assert request.extensions["sni_hostname"] == "hooks.example.com"

    body = request.content
    assert verify_signature(
        endpoint["secret"], request.headers["Hookhaven-Signature"], body, now=clock().timestamp()
    )
    envelope = json.loads(body)
    assert envelope["id"] == event["id"]
    assert envelope["type"] == "order.created"
    assert envelope["data"] == {"n": 0}
    assert request.headers["Hookhaven-Delivery-Id"] == str(delivery_id)
    assert request.headers["Hookhaven-Event-Id"] == event["id"]
    assert request.headers["Hookhaven-Attempt"] == "1"
    assert request.headers["Content-Type"] == "application/json"

    delivery = await _delivery(client, auth_headers, delivery_id)
    assert delivery["status"] == "succeeded"
    assert delivery["attempt_count"] == 1
    assert delivery["delivered_at"] is not None
    assert delivery["next_attempt_at"] is None
    [attempt] = delivery["attempts"]
    assert attempt["success"] is True
    assert attempt["status_code"] == 200
    assert attempt["response_snippet"] == "body-200"
    assert await delivery_queue.stats() == EMPTY
    assert await make_worker().run_once() is False  # queue drained


# --------------------------------------------------------------------- retries


async def test_http_error_schedules_a_retry_with_backoff(
    client: AsyncClient,
    auth_headers: dict[str, str],
    delivery_queue: DeliveryQueue,
    redis: FakeAsyncRedis,
    make_worker: Callable[..., Worker],
    receiver: Receiver,
    clock: Clock,
) -> None:
    _, _, [delivery_id] = await _setup(client, auth_headers, delivery_queue)
    receiver.script = [500]

    await make_worker().run_once()

    delivery = await _delivery(client, auth_headers, delivery_id)
    assert delivery["status"] == "pending"
    assert delivery["attempt_count"] == 1
    assert delivery["last_status_code"] == 500
    assert delivery["last_error"] == "HTTP 500"
    assert delivery["attempts"][0]["success"] is False
    assert delivery["attempts"][0]["response_snippet"] == "body-500"
    # first retry is 10s out (jitter factor pinned to 1.0)
    assert await redis.zscore(SCHEDULED_KEY, str(delivery_id)) == pytest.approx(
        clock().timestamp() + 10
    )
    assert await delivery_queue.stats() == {"ready": 0, "scheduled": 1, "processing": 0}


@pytest.mark.parametrize(
    ("failure", "expected_error"),
    [
        (httpx.ConnectError("connection refused"), "ConnectError: connection refused"),
        (httpx.ReadTimeout("slow"), "request timed out"),
        (httpx.RemoteProtocolError("bad response"), "RemoteProtocolError: bad response"),
    ],
    ids=["connect", "timeout", "protocol"],
)
async def test_network_failures_are_recorded_and_retried(
    client: AsyncClient,
    auth_headers: dict[str, str],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
    receiver: Receiver,
    failure: Exception,
    expected_error: str,
) -> None:
    _, _, [delivery_id] = await _setup(client, auth_headers, delivery_queue)
    receiver.script = [failure]

    await make_worker().run_once()

    delivery = await _delivery(client, auth_headers, delivery_id)
    assert delivery["status"] == "pending"
    assert delivery["attempts"][0]["error"] == expected_error
    assert delivery["attempts"][0]["status_code"] is None
    assert (await delivery_queue.stats())["scheduled"] == 1


async def test_retry_succeeds_later_with_stable_delivery_id_and_fresh_signature(
    client: AsyncClient,
    auth_headers: dict[str, str],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
    receiver: Receiver,
    clock: Clock,
) -> None:
    endpoint, _, [delivery_id] = await _setup(client, auth_headers, delivery_queue)
    receiver.script = [503, 200]
    worker = make_worker()

    await worker.run_once()
    assert await worker.run_once() is False  # not due yet: nothing to claim
    clock.advance(11)
    assert await delivery_queue.promote_due(now=clock().timestamp()) == 1
    await worker.run_once()

    first, second = receiver.requests
    assert first.headers["Hookhaven-Delivery-Id"] == second.headers["Hookhaven-Delivery-Id"]
    assert (first.headers["Hookhaven-Attempt"], second.headers["Hookhaven-Attempt"]) == ("1", "2")
    assert first.headers["Hookhaven-Signature"] != second.headers["Hookhaven-Signature"]
    assert verify_signature(
        endpoint["secret"],
        second.headers["Hookhaven-Signature"],
        second.content,
        now=clock().timestamp(),
    )
    delivery = await _delivery(client, auth_headers, delivery_id)
    assert delivery["status"] == "succeeded"
    assert [a["attempt_number"] for a in delivery["attempts"]] == [1, 2]
    assert [a["success"] for a in delivery["attempts"]] == [False, True]
    assert await delivery_queue.stats() == EMPTY


async def test_exhausted_retries_end_in_dead_status(
    client: AsyncClient,
    auth_headers: dict[str, str],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
    receiver: Receiver,
    clock: Clock,
) -> None:
    _, _, [delivery_id] = await _setup(client, auth_headers, delivery_queue)
    receiver.default = 500
    worker = make_worker(delivery_max_attempts=2)

    await worker.run_once()
    clock.advance(11)
    await delivery_queue.promote_due(now=clock().timestamp())
    await worker.run_once()

    delivery = await _delivery(client, auth_headers, delivery_id)
    assert delivery["status"] == "dead"
    assert delivery["attempt_count"] == 2
    assert delivery["next_attempt_at"] is None
    assert len(delivery["attempts"]) == 2
    assert await delivery_queue.stats() == EMPTY  # nothing left to retry


async def test_max_attempts_of_one_means_no_retries(
    client: AsyncClient,
    auth_headers: dict[str, str],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
    receiver: Receiver,
) -> None:
    _, _, [delivery_id] = await _setup(client, auth_headers, delivery_queue)
    receiver.default = 500
    await make_worker(delivery_max_attempts=1).run_once()
    assert (await _delivery(client, auth_headers, delivery_id))["status"] == "dead"


# ---------------------------------------------------------- idempotent consumer


async def test_duplicate_queue_entry_for_a_finished_delivery_sends_nothing(
    client: AsyncClient,
    auth_headers: dict[str, str],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
    receiver: Receiver,
) -> None:
    _, _, [delivery_id] = await _setup(client, auth_headers, delivery_queue)
    worker = make_worker()
    await worker.run_once()
    await delivery_queue.enqueue([delivery_id])  # e.g. a reconciler double-enqueue

    await worker.run_once()

    assert len(receiver.requests) == 1
    assert await delivery_queue.stats() == EMPTY


async def test_two_workers_holding_the_same_id_send_exactly_once(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_maker: async_sessionmaker[AsyncSession],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
    receiver: Receiver,
) -> None:
    _, _, [delivery_id] = await _setup(client, auth_headers, delivery_queue)
    await delivery_queue.enqueue([delivery_id])  # same id queued twice
    receiver.delay = 0.1  # the first attempt is still in flight when the second worker looks

    await asyncio.gather(make_worker().run_once(), make_worker().run_once())

    assert len(receiver.requests) == 1
    async with session_maker() as session:
        attempts = await session.scalar(select(func.count()).select_from(DeliveryAttempt))
    assert attempts == 1
    assert (await _delivery(client, auth_headers, delivery_id))["status"] == "succeeded"


async def test_claimed_before_it_is_due_is_parked_not_delivered_early(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_maker: async_sessionmaker[AsyncSession],
    delivery_queue: DeliveryQueue,
    redis: FakeAsyncRedis,
    make_worker: Callable[..., Worker],
    receiver: Receiver,
    clock: Clock,
) -> None:
    """E.g. a lease expired right after a retry had already been scheduled in the database."""
    _, _, [delivery_id] = await _setup(client, auth_headers, delivery_queue)
    due = clock() + timedelta(seconds=100)
    async with session_maker() as session:
        await session.execute(update(Delivery).values(next_attempt_at=due))
        await session.commit()

    await make_worker().run_once()

    assert receiver.requests == []
    assert await redis.zscore(SCHEDULED_KEY, str(delivery_id)) == pytest.approx(due.timestamp())
    assert (await _delivery(client, auth_headers, delivery_id))["attempt_count"] == 0


# -------------------------------------------------------------- crash recovery


async def test_work_abandoned_by_a_crashed_worker_is_recovered(
    client: AsyncClient,
    auth_headers: dict[str, str],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
    receiver: Receiver,
    clock: Clock,
) -> None:
    _, _, [delivery_id] = await _setup(client, auth_headers, delivery_queue)
    worker = make_worker()
    # A worker claims the id and dies before doing anything: only the lease remains.
    assert await delivery_queue.claim(now=clock().timestamp(), lease_seconds=60) == delivery_id
    assert await worker.run_once() is False

    clock.advance(61)
    assert await delivery_queue.reap_expired(now=clock().timestamp()) == 1
    await worker.run_once()

    assert (await _delivery(client, auth_headers, delivery_id))["status"] == "succeeded"
    assert len(receiver.requests) == 1


async def test_a_bug_while_processing_does_not_ack_the_delivery(
    client: AsyncClient,
    auth_headers: dict[str, str],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _setup(client, auth_headers, delivery_queue)
    worker = make_worker()

    async def boom(_: uuid.UUID) -> None:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(worker, "process", boom)

    assert await worker.run_once() is True  # the worker survives
    # still leased (not lost): it will be reaped and retried once the lease expires
    assert await delivery_queue.stats() == {"ready": 0, "scheduled": 0, "processing": 1}


# ----------------------------------------------------------- guard rails


async def test_disabled_endpoint_is_not_called_and_delivery_goes_dead(
    client: AsyncClient,
    auth_headers: dict[str, str],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
    receiver: Receiver,
) -> None:
    endpoint, _, [delivery_id] = await _setup(client, auth_headers, delivery_queue)
    await client.patch(
        f"/v1/endpoints/{endpoint['id']}", json={"is_active": False}, headers=auth_headers
    )

    await make_worker().run_once()

    assert receiver.requests == []
    delivery = await _delivery(client, auth_headers, delivery_id)
    assert delivery["status"] == "dead"
    assert delivery["attempts"][0]["error"] == "endpoint is disabled"
    assert await delivery_queue.stats() == EMPTY


async def test_destination_that_became_private_is_blocked_at_delivery_time(
    client: AsyncClient,
    auth_headers: dict[str, str],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
    receiver: Receiver,
) -> None:
    """DNS rebinding: the hostname passed validation at registration but now points inside."""
    _, _, [delivery_id] = await _setup(client, auth_headers, delivery_queue)

    async def rebound(host: str, port: int) -> list[str]:
        return ["10.0.0.5"]

    await make_worker(resolver=rebound).run_once()

    assert receiver.requests == []  # nothing was sent
    delivery = await _delivery(client, auth_headers, delivery_id)
    assert delivery["attempts"][0]["error"].startswith("blocked by destination policy")
    assert delivery["status"] == "pending"  # DNS can heal, so it is retried like any failure


async def test_undecryptable_secret_fails_the_attempt_instead_of_crashing(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_maker: async_sessionmaker[AsyncSession],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
    receiver: Receiver,
) -> None:
    _, _, [delivery_id] = await _setup(client, auth_headers, delivery_queue)
    async with session_maker() as session:
        await session.execute(update(Endpoint).values(secret_encrypted="not-a-fernet-token"))
        await session.commit()

    await make_worker().run_once()

    assert receiver.requests == []
    delivery = await _delivery(client, auth_headers, delivery_id)
    assert delivery["attempts"][0]["error"] == "cannot decrypt endpoint secret"


# ---------------------------------------------------- concurrency & lifecycle


async def test_processes_many_deliveries_concurrently_up_to_the_limit(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_maker: async_sessionmaker[AsyncSession],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
    receiver: Receiver,
) -> None:
    await _setup(client, auth_headers, delivery_queue, events=12)
    receiver.delay = 0.05
    worker = make_worker(worker_concurrency=4)
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run(stop))

    async def all_done() -> bool:
        async with session_maker() as session:
            done = await session.scalar(
                select(func.count()).select_from(Delivery).where(Delivery.status == "succeeded")
            )
        return done == 12

    try:
        await _wait_until(all_done)
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=5)

    assert len(receiver.requests) == 12  # exactly once each
    assert 2 <= receiver.max_inflight <= 4  # parallel, but bounded by worker_concurrency
    assert await delivery_queue.stats() == EMPTY


async def test_run_loop_promotes_due_retries_by_itself(
    client: AsyncClient,
    auth_headers: dict[str, str],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
    receiver: Receiver,
    clock: Clock,
) -> None:
    _, _, [delivery_id] = await _setup(client, auth_headers, delivery_queue)
    receiver.script = [500, 200]
    worker = make_worker(worker_concurrency=1)
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run(stop))

    async def failed_once() -> bool:
        return len(receiver.requests) == 1 and (await delivery_queue.stats())["scheduled"] == 1

    async def succeeded() -> bool:
        return (await _delivery(client, auth_headers, delivery_id))["status"] == "succeeded"

    try:
        await _wait_until(failed_once)
        clock.advance(11)  # the retry is now due; the maintenance loop must notice
        await _wait_until(succeeded)
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=5)


async def test_graceful_shutdown_lets_in_flight_delivery_finish(
    client: AsyncClient,
    auth_headers: dict[str, str],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
    receiver: Receiver,
) -> None:
    _, _, [delivery_id] = await _setup(client, auth_headers, delivery_queue)
    receiver.delay = 0.2
    stop = asyncio.Event()
    task = asyncio.create_task(make_worker(worker_concurrency=1).run(stop))

    async def in_flight() -> bool:
        return receiver.inflight == 1

    await _wait_until(in_flight)
    stop.set()  # SIGTERM arrives mid-request
    await asyncio.wait_for(task, timeout=5)

    assert (await _delivery(client, auth_headers, delivery_id))["status"] == "succeeded"
    assert await delivery_queue.stats() == EMPTY


async def test_worker_stops_promptly_when_idle(make_worker: Callable[..., Worker]) -> None:
    stop = asyncio.Event()
    stop.set()
    await asyncio.wait_for(make_worker().run(stop), timeout=2)


# ----------------------------------------------------------------------- api


async def test_delivery_detail_is_private_to_its_owner(
    client: AsyncClient, auth_headers: dict[str, str], delivery_queue: DeliveryQueue
) -> None:
    _, _, [delivery_id] = await _setup(client, auth_headers, delivery_queue)
    other = await register_and_login(client, "other@example.com")

    assert (await client.get(f"/v1/deliveries/{delivery_id}", headers=other)).status_code == 404
    assert (await client.get(f"/v1/deliveries/{delivery_id}")).status_code == 401
    assert (
        await client.get(f"/v1/deliveries/{uuid.uuid4()}", headers=auth_headers)
    ).status_code == 404
