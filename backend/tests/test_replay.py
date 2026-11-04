import uuid
from collections.abc import Callable

from httpx import AsyncClient

from app.queue.delivery_queue import DeliveryQueue
from app.workers.delivery_worker import Worker

from .conftest import register_and_login
from .fakes import FakeQueue
from .support import Clock, Receiver, get_delivery, setup_deliveries


async def _dead_deliveries(
    client: AsyncClient,
    headers: dict[str, str],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
    receiver: Receiver,
    *,
    events: int = 1,
) -> tuple[dict[str, str], list[uuid.UUID], Worker]:
    """Ingest events whose deliveries all fail permanently (max 1 attempt)."""
    endpoint, _, ids = await setup_deliveries(client, headers, delivery_queue, events=events)
    receiver.default = 500
    worker = make_worker(delivery_max_attempts=1)
    for _ in ids:
        await worker.run_once()
    receiver.default = 200  # the receiver has been fixed
    return endpoint, ids, worker


async def test_replaying_a_dead_delivery_gets_it_delivered(
    client: AsyncClient,
    auth_headers: dict[str, str],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
    receiver: Receiver,
    queue: FakeQueue,
) -> None:
    _, [did], worker = await _dead_deliveries(
        client, auth_headers, delivery_queue, make_worker, receiver
    )
    assert (await get_delivery(client, auth_headers, did))["status"] == "dead"

    replay = await client.post(f"/v1/deliveries/{did}/replay", headers=auth_headers)

    assert replay.status_code == 202
    assert replay.json()["status"] == "pending"
    assert replay.json()["attempt_count"] == 1  # history is kept, numbering continues
    assert queue.batches[-1] == [did]  # enqueued after the commit

    await delivery_queue.enqueue([did])  # (the API test double does not feed the real queue)
    await worker.run_once()

    delivery = await get_delivery(client, auth_headers, did)
    assert delivery["status"] == "succeeded"
    assert [a["attempt_number"] for a in delivery["attempts"]] == [1, 2]
    assert [a["success"] for a in delivery["attempts"]] == [False, True]
    first, second = receiver.requests
    # same delivery id: a receiver that already deduped nothing (it never succeeded) accepts it
    assert first.headers["Hookhaven-Delivery-Id"] == second.headers["Hookhaven-Delivery-Id"]
    assert second.headers["Hookhaven-Attempt"] == "2"


async def test_replay_grants_a_fresh_retry_budget(
    client: AsyncClient,
    auth_headers: dict[str, str],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
    receiver: Receiver,
    clock: Clock,
) -> None:
    """Without a per-cycle budget, a replayed delivery would die again after a single try."""
    _, _, [did] = await setup_deliveries(client, auth_headers, delivery_queue)
    receiver.default = 500
    worker = make_worker(delivery_max_attempts=2)

    async def retry_when_due() -> None:
        clock.advance(11)
        await delivery_queue.promote_due(now=clock().timestamp())
        await worker.run_once()

    await worker.run_once()  # attempt 1
    await retry_when_due()  # attempt 2 -> exhausted
    dead = await get_delivery(client, auth_headers, did)
    assert (dead["status"], dead["attempt_count"]) == ("dead", 2)

    await client.post(f"/v1/deliveries/{did}/replay", headers=auth_headers)
    await delivery_queue.enqueue([did])
    await worker.run_once()  # attempt 3 = first attempt of the new cycle

    after_first = await get_delivery(client, auth_headers, did)
    assert (after_first["status"], after_first["attempt_count"]) == ("pending", 3)  # not dead yet

    await retry_when_due()  # attempt 4 = second attempt of the new cycle -> exhausted again
    final = await get_delivery(client, auth_headers, did)
    assert (final["status"], final["attempt_count"]) == ("dead", 4)
    assert [a["attempt_number"] for a in final["attempts"]] == [1, 2, 3, 4]


async def test_only_dead_deliveries_can_be_replayed(
    client: AsyncClient,
    auth_headers: dict[str, str],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
    receiver: Receiver,
) -> None:
    _, _, [pending] = await setup_deliveries(client, auth_headers, delivery_queue, enqueue=False)
    conflict = await client.post(f"/v1/deliveries/{pending}/replay", headers=auth_headers)
    assert conflict.status_code == 409
    assert "pending" in conflict.json()["detail"]

    await delivery_queue.enqueue([pending])
    await make_worker().run_once()  # succeeds
    done = await client.post(f"/v1/deliveries/{pending}/replay", headers=auth_headers)
    assert done.status_code == 409
    assert "succeeded" in done.json()["detail"]


async def test_double_replay_is_rejected(
    client: AsyncClient,
    auth_headers: dict[str, str],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
    receiver: Receiver,
) -> None:
    _, [did], _ = await _dead_deliveries(
        client, auth_headers, delivery_queue, make_worker, receiver
    )
    assert (
        await client.post(f"/v1/deliveries/{did}/replay", headers=auth_headers)
    ).status_code == 202
    assert (
        await client.post(f"/v1/deliveries/{did}/replay", headers=auth_headers)
    ).status_code == 409


async def test_replay_is_private_to_the_owner_and_needs_auth(
    client: AsyncClient,
    auth_headers: dict[str, str],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
    receiver: Receiver,
) -> None:
    _, [did], _ = await _dead_deliveries(
        client, auth_headers, delivery_queue, make_worker, receiver
    )
    other = await register_and_login(client, "other@example.com")
    assert (await client.post(f"/v1/deliveries/{did}/replay", headers=other)).status_code == 404
    assert (await client.post(f"/v1/deliveries/{did}/replay")).status_code == 401
    assert (await get_delivery(client, auth_headers, did))["status"] == "dead"  # untouched


async def test_cannot_replay_into_a_disabled_endpoint(
    client: AsyncClient,
    auth_headers: dict[str, str],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
    receiver: Receiver,
) -> None:
    endpoint, [did], _ = await _dead_deliveries(
        client, auth_headers, delivery_queue, make_worker, receiver
    )
    await client.patch(
        f"/v1/endpoints/{endpoint['id']}", json={"is_active": False}, headers=auth_headers
    )
    response = await client.post(f"/v1/deliveries/{did}/replay", headers=auth_headers)
    assert response.status_code == 409
    assert "disabled" in response.json()["detail"]


async def test_replay_survives_a_queue_outage(
    client: AsyncClient,
    auth_headers: dict[str, str],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
    receiver: Receiver,
    queue: FakeQueue,
) -> None:
    _, [did], _ = await _dead_deliveries(
        client, auth_headers, delivery_queue, make_worker, receiver
    )
    queue.down = True
    response = await client.post(f"/v1/deliveries/{did}/replay", headers=auth_headers)
    assert response.status_code == 202  # committed; the reconciler will pick it up
    assert (await get_delivery(client, auth_headers, did))["status"] == "pending"


# ---------------------------------------------------------- replay a whole endpoint


async def test_replay_dead_resends_only_that_endpoints_dead_deliveries(
    client: AsyncClient,
    auth_headers: dict[str, str],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
    receiver: Receiver,
    queue: FakeQueue,
) -> None:
    endpoint, _, ids = await setup_deliveries(client, auth_headers, delivery_queue, events=3)
    receiver.script = [500, 500, 200]  # two die, one succeeds
    worker = make_worker(delivery_max_attempts=1)
    for _ in ids:
        await worker.run_once()

    response = await client.post(
        f"/v1/endpoints/{endpoint['id']}/replay-dead", headers=auth_headers
    )

    assert response.status_code == 202
    assert response.json() == {"replayed": 2}
    assert set(queue.batches[-1]) == {ids[0], ids[1]}
    states = [(await get_delivery(client, auth_headers, i))["status"] for i in ids]
    assert states == ["pending", "pending", "succeeded"]  # the success was not touched


async def test_replay_dead_edge_cases(
    client: AsyncClient,
    auth_headers: dict[str, str],
    delivery_queue: DeliveryQueue,
    queue: FakeQueue,
) -> None:
    endpoint, _, _ = await setup_deliveries(client, auth_headers, delivery_queue, enqueue=False)
    url = f"/v1/endpoints/{endpoint['id']}/replay-dead"

    nothing = await client.post(url, headers=auth_headers)
    assert nothing.json() == {"replayed": 0}  # nothing dead: no-op

    other = await register_and_login(client, "other@example.com")
    assert (await client.post(url, headers=other)).status_code == 404
    assert (
        await client.post(f"/v1/endpoints/{uuid.uuid4()}/replay-dead", headers=auth_headers)
    ).status_code == 404

    await client.patch(
        f"/v1/endpoints/{endpoint['id']}", json={"is_active": False}, headers=auth_headers
    )
    assert (await client.post(url, headers=auth_headers)).status_code == 409


# -------------------------------------------------------------- listing (the DLQ)


async def test_list_deliveries_filters_and_pages(
    client: AsyncClient,
    auth_headers: dict[str, str],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
    receiver: Receiver,
) -> None:
    endpoint, _, ids = await setup_deliveries(client, auth_headers, delivery_queue, events=3)
    receiver.script = [500, 200, 500]
    worker = make_worker(delivery_max_attempts=1)
    for _ in ids:
        await worker.run_once()

    everything = (await client.get("/v1/deliveries", headers=auth_headers)).json()
    assert len(everything) == 3

    dlq = (await client.get("/v1/deliveries?status=dead", headers=auth_headers)).json()
    assert {d["id"] for d in dlq} == {str(ids[0]), str(ids[2])}

    ok = await client.get("/v1/deliveries?status=succeeded", headers=auth_headers)
    assert [d["id"] for d in ok.json()] == [str(ids[1])]

    by_endpoint = await client.get(
        f"/v1/deliveries?endpoint_id={endpoint['id']}&limit=2", headers=auth_headers
    )
    assert len(by_endpoint.json()) == 2
    page2 = await client.get(
        f"/v1/deliveries?endpoint_id={endpoint['id']}&limit=2&offset=2", headers=auth_headers
    )
    assert len(page2.json()) == 1

    none = await client.get(f"/v1/deliveries?endpoint_id={uuid.uuid4()}", headers=auth_headers)
    assert none.json() == []


async def test_list_deliveries_is_scoped_to_the_user_and_validates_input(
    client: AsyncClient,
    auth_headers: dict[str, str],
    delivery_queue: DeliveryQueue,
) -> None:
    await setup_deliveries(client, auth_headers, delivery_queue, enqueue=False)
    other = await register_and_login(client, "other@example.com")
    assert (await client.get("/v1/deliveries", headers=other)).json() == []
    assert (
        await client.get("/v1/deliveries?status=bogus", headers=auth_headers)
    ).status_code == 422
    assert (await client.get("/v1/deliveries?limit=0", headers=auth_headers)).status_code == 422
    assert (await client.get("/v1/deliveries")).status_code == 401
