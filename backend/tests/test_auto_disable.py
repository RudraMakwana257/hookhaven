from collections.abc import Callable

from httpx import AsyncClient

from app.queue.delivery_queue import DeliveryQueue
from app.workers.delivery_worker import Worker

from .support import Receiver, get_delivery, get_endpoint, setup_deliveries


async def test_endpoint_is_disabled_after_consecutive_dead_deliveries(
    client: AsyncClient,
    auth_headers: dict[str, str],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
    receiver: Receiver,
) -> None:
    endpoint, _, ids = await setup_deliveries(client, auth_headers, delivery_queue, events=5)
    receiver.default = 500
    worker = make_worker(delivery_max_attempts=1, endpoint_disable_threshold=3)

    for _ in ids:
        await worker.run_once()

    state = await get_endpoint(client, auth_headers, endpoint["id"])
    assert state["is_active"] is False
    assert state["consecutive_failures"] == 3  # the skipped deliveries did not inflate it
    assert state["disabled_reason"] == "3 consecutive deliveries failed"
    assert state["disabled_at"] is not None

    assert len(receiver.requests) == 3  # no traffic to a disabled endpoint
    skipped = await get_delivery(client, auth_headers, ids[3])
    assert skipped["status"] == "dead"
    assert skipped["attempts"][0]["error"] == "endpoint is disabled"


async def test_a_success_resets_the_failure_streak(
    client: AsyncClient,
    auth_headers: dict[str, str],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
    receiver: Receiver,
) -> None:
    endpoint, _, ids = await setup_deliveries(client, auth_headers, delivery_queue, events=5)
    receiver.script = [500, 500, 200, 500, 500]
    worker = make_worker(delivery_max_attempts=1, endpoint_disable_threshold=3)

    for _ in ids:
        await worker.run_once()

    state = await get_endpoint(client, auth_headers, endpoint["id"])
    assert state["is_active"] is True  # 2 + success + 2 never reached 3 in a row
    assert state["consecutive_failures"] == 2


async def test_reenabling_resets_state_and_dead_letters_can_be_replayed(
    client: AsyncClient,
    auth_headers: dict[str, str],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
    receiver: Receiver,
) -> None:
    """The recovery workflow: receiver was broken -> endpoint auto-disabled -> fix it ->
    re-enable -> replay everything that was dead-lettered."""
    endpoint, _, ids = await setup_deliveries(client, auth_headers, delivery_queue, events=5)
    receiver.default = 500
    worker = make_worker(delivery_max_attempts=1, endpoint_disable_threshold=3)
    for _ in ids:
        await worker.run_once()
    url = f"/v1/endpoints/{endpoint['id']}"
    assert (await get_endpoint(client, auth_headers, endpoint["id"]))["is_active"] is False

    # Replaying into a disabled endpoint is refused until it is re-enabled.
    blocked = await client.post(f"{url}/replay-dead", headers=auth_headers)
    assert blocked.status_code == 409

    receiver.default = 200  # the receiver is fixed
    await client.patch(url, json={"is_active": True}, headers=auth_headers)
    state = await get_endpoint(client, auth_headers, endpoint["id"])
    assert state["consecutive_failures"] == 0
    assert state["disabled_at"] is None
    assert state["disabled_reason"] is None

    replay = await client.post(f"{url}/replay-dead", headers=auth_headers)
    assert replay.json() == {"replayed": 5}
    await delivery_queue.enqueue(ids)
    for _ in ids:
        await worker.run_once()

    statuses = {(await get_delivery(client, auth_headers, i))["status"] for i in ids}
    assert statuses == {"succeeded"}
    assert (await get_endpoint(client, auth_headers, endpoint["id"]))["is_active"] is True


async def test_manual_disable_is_recorded_and_stops_fan_out(
    client: AsyncClient, auth_headers: dict[str, str], delivery_queue: DeliveryQueue
) -> None:
    endpoint, _, _ = await setup_deliveries(client, auth_headers, delivery_queue, enqueue=False)
    fresh = await get_endpoint(client, auth_headers, endpoint["id"])
    assert (fresh["consecutive_failures"], fresh["disabled_at"]) == (0, None)

    await client.patch(
        f"/v1/endpoints/{endpoint['id']}", json={"is_active": False}, headers=auth_headers
    )
    state = await get_endpoint(client, auth_headers, endpoint["id"])
    assert state["disabled_reason"] == "disabled by user"
    assert state["disabled_at"] is not None

    event = await client.post(
        "/v1/events", json={"type": "a.b", "payload": {}}, headers=auth_headers
    )
    assert event.json()["deliveries"] == []
