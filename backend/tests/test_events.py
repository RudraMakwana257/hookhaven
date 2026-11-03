import uuid

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_settings
from app.core.config import Settings
from app.models import Delivery, Event
from app.services import events as events_service

from .conftest import register_and_login
from .fakes import FakeQueue

EVENT = {"type": "order.created", "payload": {"order_id": 42, "amount": 1999}}


async def _endpoint(client: AsyncClient, headers: dict[str, str], **body: object) -> dict[str, str]:
    response = await client.post(
        "/v1/endpoints", json={"url": "https://hooks.example.com/in", **body}, headers=headers
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _count(maker: async_sessionmaker[AsyncSession], model: type) -> int:
    async with maker() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


# ---------------------------------------------------------------- fan-out


async def test_fans_out_to_matching_active_endpoints_only(
    client: AsyncClient, auth_headers: dict[str, str], queue: FakeQueue
) -> None:
    wildcard = await _endpoint(client, auth_headers)
    exact = await _endpoint(client, auth_headers, event_types=["order.created"])
    other_type = await _endpoint(client, auth_headers, event_types=["invoice.paid"])
    disabled = await _endpoint(client, auth_headers)
    await client.patch(
        f"/v1/endpoints/{disabled['id']}", json={"is_active": False}, headers=auth_headers
    )

    response = await client.post("/v1/events", json=EVENT, headers=auth_headers)

    assert response.status_code == 202
    body = response.json()
    targeted = {d["endpoint_id"] for d in body["deliveries"]}
    assert targeted == {wildcard["id"], exact["id"]}
    assert other_type["id"] not in targeted and disabled["id"] not in targeted
    assert all(d["status"] == "pending" and d["attempt_count"] == 0 for d in body["deliveries"])
    # exactly those deliveries were queued
    assert set(queue.enqueued) == {uuid.UUID(d["id"]) for d in body["deliveries"]}
    assert body["payload"] == EVENT["payload"]


async def test_event_is_stored_even_with_no_matching_endpoint(
    client: AsyncClient, auth_headers: dict[str, str], queue: FakeQueue
) -> None:
    response = await client.post("/v1/events", json=EVENT, headers=auth_headers)
    assert response.status_code == 202
    assert response.json()["deliveries"] == []
    assert queue.enqueued == []


async def test_events_never_fan_out_to_another_users_endpoints(
    client: AsyncClient, auth_headers: dict[str, str], queue: FakeQueue
) -> None:
    other = await register_and_login(client, "other@example.com")
    await _endpoint(client, other)  # wildcard endpoint owned by someone else

    response = await client.post("/v1/events", json=EVENT, headers=auth_headers)
    assert response.json()["deliveries"] == []
    assert queue.enqueued == []


# ------------------------------------------------------------ idempotency


async def test_same_key_and_payload_returns_original_without_new_deliveries(
    client: AsyncClient,
    auth_headers: dict[str, str],
    queue: FakeQueue,
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    await _endpoint(client, auth_headers)
    headers = {**auth_headers, "Idempotency-Key": "order-42-created"}

    first = await client.post("/v1/events", json=EVENT, headers=headers)
    second = await client.post("/v1/events", json=EVENT, headers=headers)

    assert first.status_code == second.status_code == 202
    assert "Idempotent-Replayed" not in first.headers
    assert second.headers["Idempotent-Replayed"] == "true"
    assert second.json()["id"] == first.json()["id"]
    assert [(d["id"], d["status"]) for d in second.json()["deliveries"]] == [
        (d["id"], d["status"]) for d in first.json()["deliveries"]
    ]
    assert await _count(session_maker, Event) == 1
    assert await _count(session_maker, Delivery) == 1
    assert len(queue.batches) == 1  # the replay did not enqueue again


async def test_payload_key_order_does_not_change_identity(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    headers = {**auth_headers, "Idempotency-Key": "k1"}
    one = {"type": "a.b", "payload": {"x": 1, "y": {"p": 1, "q": 2}}}
    two = {"type": "a.b", "payload": {"y": {"q": 2, "p": 1}, "x": 1}}
    a = await client.post("/v1/events", json=one, headers=headers)
    b = await client.post("/v1/events", json=two, headers=headers)
    assert b.json()["id"] == a.json()["id"]


async def test_same_key_with_different_payload_is_a_conflict(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    headers = {**auth_headers, "Idempotency-Key": "k1"}
    await client.post("/v1/events", json=EVENT, headers=headers)
    changed = {**EVENT, "payload": {"order_id": 43}}
    conflict = await client.post("/v1/events", json=changed, headers=headers)
    assert conflict.status_code == 409
    other_type = await client.post(
        "/v1/events", json={**EVENT, "type": "order.updated"}, headers=headers
    )
    assert other_type.status_code == 409


async def test_idempotency_keys_are_scoped_per_user(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    other = await register_and_login(client, "other@example.com")
    mine = await client.post(
        "/v1/events", json=EVENT, headers={**auth_headers, "Idempotency-Key": "k"}
    )
    theirs = await client.post("/v1/events", json=EVENT, headers={**other, "Idempotency-Key": "k"})
    assert mine.json()["id"] != theirs.json()["id"]
    assert "Idempotent-Replayed" not in theirs.headers


async def test_without_a_key_every_request_is_a_new_event(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    await client.post("/v1/events", json=EVENT, headers=auth_headers)
    await client.post("/v1/events", json=EVENT, headers=auth_headers)
    assert await _count(session_maker, Event) == 2


async def test_losing_the_insert_race_resolves_to_a_replay_not_a_500(
    client: AsyncClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Two requests pass the SELECT before either commits; the unique constraint decides."""
    await _endpoint(client, auth_headers)
    headers = {**auth_headers, "Idempotency-Key": "race"}
    original = await client.post("/v1/events", json=EVENT, headers=headers)

    real_find = events_service._find_by_key
    calls = 0

    async def blind_first_lookup(*args: object, **kwargs: object) -> Event | None:
        nonlocal calls
        calls += 1
        if calls == 1:
            return None  # the racing request has not seen the winner yet
        return await real_find(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(events_service, "_find_by_key", blind_first_lookup)
    racing = await client.post("/v1/events", json=EVENT, headers=headers)

    assert racing.status_code == 202
    assert racing.json()["id"] == original.json()["id"]
    assert racing.headers["Idempotent-Replayed"] == "true"
    assert await _count(session_maker, Event) == 1
    assert await _count(session_maker, Delivery) == 1  # loser's deliveries were rolled back


async def test_losing_the_race_with_a_different_payload_is_still_a_conflict(
    client: AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = {**auth_headers, "Idempotency-Key": "race2"}
    await client.post("/v1/events", json=EVENT, headers=headers)

    real_find = events_service._find_by_key
    calls = 0

    async def blind_first_lookup(*args: object, **kwargs: object) -> Event | None:
        nonlocal calls
        calls += 1
        return None if calls == 1 else await real_find(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(events_service, "_find_by_key", blind_first_lookup)
    changed = {**EVENT, "payload": {"different": True}}
    assert (await client.post("/v1/events", json=changed, headers=headers)).status_code == 409


# ------------------------------------------------------------ durability


async def test_queue_outage_does_not_lose_or_reject_the_event(
    client: AsyncClient,
    auth_headers: dict[str, str],
    queue: FakeQueue,
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    await _endpoint(client, auth_headers)
    queue.down = True

    response = await client.post("/v1/events", json=EVENT, headers=auth_headers)

    assert response.status_code == 202  # durably stored, so we can promise delivery
    assert queue.enqueued == []
    async with session_maker() as session:
        delivery = (await session.execute(select(Delivery))).scalar_one()
    assert delivery.status == "pending"  # the reconciler will pick this up
    assert delivery.next_attempt_at is not None


# ------------------------------------------------------------- validation


@pytest.mark.parametrize(
    "body",
    [
        {"payload": {}},  # missing type
        {"type": "order.created"},  # missing payload
        {"type": "Order.Created", "payload": {}},  # uppercase
        {"type": "has space", "payload": {}},
        {"type": "*", "payload": {}},  # wildcards are for subscriptions, not events
        {"type": "a" * 65, "payload": {}},
        {"type": "order.created", "payload": [1, 2, 3]},  # payload must be an object
        {"type": "order.created", "payload": "text"},
    ],
)
async def test_invalid_events_are_rejected(
    client: AsyncClient, auth_headers: dict[str, str], body: dict[str, object]
) -> None:
    assert (await client.post("/v1/events", json=body, headers=auth_headers)).status_code == 422


@pytest.mark.parametrize("key", ["x" * 256, "has space", "", "ключ"])
async def test_invalid_idempotency_keys_are_rejected(
    client: AsyncClient, auth_headers: dict[str, str], key: str
) -> None:
    try:
        response = await client.post(
            "/v1/events", json=EVENT, headers={**auth_headers, "Idempotency-Key": key}
        )
    except UnicodeEncodeError:
        return  # the HTTP client itself refuses non-ASCII header values
    assert response.status_code == 422


async def test_oversized_payload_is_rejected(
    app: FastAPI, client: AsyncClient, auth_headers: dict[str, str], queue: FakeQueue
) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, max_event_payload_bytes=200
    )
    big = {"type": "a.b", "payload": {"blob": "x" * 500}}
    assert (await client.post("/v1/events", json=big, headers=auth_headers)).status_code == 413
    small = {"type": "a.b", "payload": {"blob": "x" * 50}}
    assert (await client.post("/v1/events", json=small, headers=auth_headers)).status_code == 202


async def test_oversized_content_length_is_rejected_before_parsing(
    app: FastAPI, client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, max_event_payload_bytes=100
    )
    body = b'{"type":"a.b","payload":{"blob":"' + b"x" * 5000 + b'"}}'
    response = await client.post(
        "/v1/events",
        content=body,
        headers={**auth_headers, "Content-Type": "application/json"},
    )
    assert response.status_code == 413


async def test_pathologically_nested_json_is_a_client_error_not_a_crash(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    body = b'{"type":"a.b","payload":{"x":' + b"[" * 50_000 + b"]" * 50_000 + b"}}"
    response = await client.post(
        "/v1/events", content=body, headers={**auth_headers, "Content-Type": "application/json"}
    )
    assert 400 <= response.status_code < 500


# ------------------------------------------------------------------- auth


async def test_api_key_can_ingest_events(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    created = await client.post("/v1/api-keys", json={"name": "backend"}, headers=auth_headers)
    machine = {"X-API-Key": created.json()["key"]}
    assert (await client.post("/v1/events", json=EVENT, headers=machine)).status_code == 202


async def test_ingestion_requires_authentication(client: AsyncClient) -> None:
    assert (await client.post("/v1/events", json=EVENT)).status_code == 401
    bad = await client.post("/v1/events", json=EVENT, headers={"X-API-Key": "hh_deadbeef_x"})
    assert bad.status_code == 401


# --------------------------------------------------------------- reading


async def test_list_and_get_events(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    await _endpoint(client, auth_headers)
    first = (await client.post("/v1/events", json=EVENT, headers=auth_headers)).json()
    await client.post(
        "/v1/events", json={"type": "invoice.paid", "payload": {"n": 1}}, headers=auth_headers
    )

    listed = await client.get("/v1/events", headers=auth_headers)
    assert [e["type"] for e in listed.json()] == ["invoice.paid", "order.created"]  # newest first
    assert "payload" not in listed.json()[0]  # list view stays light

    filtered = await client.get("/v1/events?type=order.created", headers=auth_headers)
    assert [e["id"] for e in filtered.json()] == [first["id"]]

    page = await client.get("/v1/events?limit=1&offset=1", headers=auth_headers)
    assert [e["type"] for e in page.json()] == ["order.created"]

    detail = await client.get(f"/v1/events/{first['id']}", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["payload"] == EVENT["payload"]
    assert len(detail.json()["deliveries"]) == 1


async def test_events_are_private_to_their_owner(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    mine = (await client.post("/v1/events", json=EVENT, headers=auth_headers)).json()
    other = await register_and_login(client, "other@example.com")
    assert (await client.get(f"/v1/events/{mine['id']}", headers=other)).status_code == 404
    assert (await client.get("/v1/events", headers=other)).json() == []
    assert (await client.get(f"/v1/events/{uuid.uuid4()}", headers=auth_headers)).status_code == 404
