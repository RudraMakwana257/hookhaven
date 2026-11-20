from collections.abc import Callable
from datetime import timedelta

from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.time import utcnow
from app.models import Delivery, Event
from app.queue.delivery_queue import DeliveryQueue
from app.workers.delivery_worker import Worker

from .conftest import register_and_login
from .support import Receiver, setup_deliveries


async def test_empty_account(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    stats = (await client.get("/v1/stats", headers=auth_headers)).json()
    assert stats == {
        "window_hours": 24,
        "events": 0,
        "deliveries": {"pending": 0, "succeeded": 0, "dead": 0},
        "dead_total": 0,
        "endpoints": {"total": 0, "active": 0, "disabled": 0},
    }


async def test_counts_reflect_activity(
    client: AsyncClient,
    auth_headers: dict[str, str],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
    receiver: Receiver,
) -> None:
    endpoint, _, _ids = await setup_deliveries(client, auth_headers, delivery_queue, events=3)
    receiver.script = [200, 500, 200]
    worker = make_worker(delivery_max_attempts=1)
    await worker.run_once()  # succeeded
    await worker.run_once()  # dead
    # the third stays pending (not processed)
    await client.post(
        "/v1/endpoints",
        json={"url": "https://other.example.com/h", "event_types": ["x.y"]},
        headers=auth_headers,
    )
    await client.patch(
        f"/v1/endpoints/{endpoint['id']}", json={"is_active": False}, headers=auth_headers
    )

    stats = (await client.get("/v1/stats", headers=auth_headers)).json()

    assert stats["events"] == 3
    assert stats["deliveries"] == {"pending": 1, "succeeded": 1, "dead": 1}
    assert stats["dead_total"] == 1
    assert stats["endpoints"] == {"total": 2, "active": 1, "disabled": 1}


async def test_dead_total_is_all_time_while_window_counts_are_recent(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_maker: async_sessionmaker[AsyncSession],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
    receiver: Receiver,
) -> None:
    await setup_deliveries(client, auth_headers, delivery_queue)
    receiver.default = 500
    await make_worker(delivery_max_attempts=1).run_once()  # one dead delivery
    long_ago = utcnow() - timedelta(days=3)
    async with session_maker() as session:
        await session.execute(update(Event).values(created_at=long_ago))
        await session.execute(update(Delivery).values(created_at=long_ago))
        await session.commit()

    stats = (await client.get("/v1/stats", headers=auth_headers)).json()

    assert stats["events"] == 0
    assert stats["deliveries"]["dead"] == 0
    assert stats["dead_total"] == 1  # an old failure still needs attention


async def test_stats_are_private_and_authenticated(
    client: AsyncClient, auth_headers: dict[str, str], delivery_queue: DeliveryQueue
) -> None:
    await setup_deliveries(client, auth_headers, delivery_queue)
    other = await register_and_login(client, "other@example.com")
    theirs = (await client.get("/v1/stats", headers=other)).json()
    assert theirs["events"] == 0
    assert theirs["endpoints"]["total"] == 0
    assert (await client.get("/v1/stats")).status_code == 401


async def test_event_list_includes_per_event_delivery_counts(
    client: AsyncClient,
    auth_headers: dict[str, str],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
    receiver: Receiver,
) -> None:
    await setup_deliveries(client, auth_headers, delivery_queue, events=2)
    receiver.script = [200, 500]
    worker = make_worker(delivery_max_attempts=1)
    await worker.run_once()
    await worker.run_once()

    items = (await client.get("/v1/events", headers=auth_headers)).json()

    by_status = sorted(tuple(sorted(i["delivery_counts"].items())) for i in items)
    assert by_status == [
        (("dead", 0), ("pending", 0), ("succeeded", 1)),
        (("dead", 1), ("pending", 0), ("succeeded", 0)),
    ]
