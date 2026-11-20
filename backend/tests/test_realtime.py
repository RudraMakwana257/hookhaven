import asyncio
import time
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from fakeredis import FakeAsyncRedis
from fastapi import FastAPI
from httpx import AsyncClient
from redis.exceptions import ConnectionError as RedisConnectionError

from app.api.deps import get_event_bus, get_settings
from app.core.config import Settings
from app.core.security import create_token
from app.queue.delivery_queue import DeliveryQueue
from app.realtime.bus import EventBus, Subscription, channel_for
from app.realtime.sse import HEARTBEAT, format_sse, stream
from app.workers.delivery_worker import Worker

from .conftest import register_and_login
from .support import Receiver, get_delivery, setup_deliveries

UID = uuid.uuid4()


async def _user_id(client: AsyncClient, headers: dict[str, str]) -> uuid.UUID:
    return uuid.UUID((await client.get("/v1/auth/me", headers=headers)).json()["id"])


async def _next(sub: Subscription, wait: float = 1.0) -> dict[str, Any]:
    message = await sub.next_message(wait)
    assert message is not None, "expected a realtime message"
    return message


# -------------------------------------------------------------------------- bus


async def test_bus_delivers_messages_to_the_owner_only(redis: FakeAsyncRedis) -> None:
    bus = EventBus(redis)
    other = uuid.uuid4()
    async with bus.subscribe(UID) as mine, bus.subscribe(other) as theirs:
        await bus.publish(UID, {"type": "event.created", "n": 1})
        assert await _next(mine) == {"type": "event.created", "n": 1}
        assert await theirs.next_message(0.1) is None  # another user's stream sees nothing


async def test_bus_ignores_garbage_on_the_channel(redis: FakeAsyncRedis) -> None:
    bus = EventBus(redis)
    async with bus.subscribe(UID) as sub:
        await redis.publish(channel_for(UID), "not json")
        await redis.publish(channel_for(UID), "[1, 2]")  # valid JSON, wrong shape
        assert await sub.next_message(0.1) is None
        assert await sub.next_message(0.1) is None


# -------------------------------------------------------------------- SSE framing


def test_sse_frames_cannot_be_injected_through_the_event_name() -> None:
    assert format_sse("delivery.updated", {"a": 1}) == 'event: delivery.updated\ndata: {"a":1}\n\n'
    evil = format_sse("x\nevent: pwned", {"a": 1})
    assert evil.startswith("event: message\n")
    assert evil.count("\n\n") == 1  # exactly one frame


async def test_stream_lifecycle_ready_heartbeats_reconnect(redis: FakeAsyncRedis) -> None:
    async def connected() -> bool:
        return False

    async with EventBus(redis).subscribe(UID) as sub:
        frames = [
            frame
            async for frame in stream(
                sub, is_disconnected=connected, heartbeat_seconds=0.05, max_lifetime_seconds=0.3
            )
        ]
    assert frames[0].startswith("event: ready")
    assert HEARTBEAT in frames  # idle connections are kept alive
    assert frames[-1].startswith("event: reconnect")  # the lifetime cap tells clients to reconnect


async def test_stream_forwards_messages_as_they_arrive(redis: FakeAsyncRedis) -> None:
    bus = EventBus(redis)

    async def connected() -> bool:
        return False

    async with bus.subscribe(UID) as sub:
        frames = stream(sub, is_disconnected=connected, heartbeat_seconds=5, max_lifetime_seconds=5)
        assert (await anext(frames)).startswith("event: ready")
        await bus.publish(UID, {"type": "delivery.updated", "status": "succeeded"})
        frame = await asyncio.wait_for(anext(frames), timeout=2)
        assert (
            frame
            == 'event: delivery.updated\ndata: {"type":"delivery.updated","status":"succeeded"}\n\n'
        )
        await frames.aclose()


async def test_stream_stops_when_the_client_disconnects(redis: FakeAsyncRedis) -> None:
    async def gone() -> bool:
        return True

    async with EventBus(redis).subscribe(UID) as sub:
        frames = [
            f
            async for f in stream(
                sub, is_disconnected=gone, heartbeat_seconds=5, max_lifetime_seconds=60
            )
        ]
    assert len(frames) == 1  # just `ready`, then it noticed and left


# --------------------------------------------------------------------- the route


def _fast_sse(app: FastAPI, **extra: Any) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, sse_max_lifetime_seconds=0.8, sse_heartbeat_seconds=0.1, **extra
    )


async def test_route_streams_updates_for_the_authenticated_user(
    app: FastAPI,
    client: AsyncClient,
    auth_headers: dict[str, str],
    redis: FakeAsyncRedis,
) -> None:
    _fast_sse(app)
    bus = EventBus(redis)
    mine = await _user_id(client, auth_headers)
    other_headers = await register_and_login(client, "other@example.com")
    other = await _user_id(client, other_headers)

    async def publish_later() -> None:
        await asyncio.sleep(0.3)
        await bus.publish(other, {"type": "delivery.updated", "secret": "not-for-you"})
        await bus.publish(mine, {"type": "delivery.updated", "status": "dead"})

    response, _ = await asyncio.gather(
        client.get("/v1/stream", headers=auth_headers), publish_later()
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["X-Accel-Buffering"] == "no"
    assert response.headers["Cache-Control"] == "no-store"
    assert (
        'event: delivery.updated\ndata: {"type":"delivery.updated","status":"dead"}'
        in response.text
    )
    assert "not-for-you" not in response.text  # tenant isolation
    assert response.text.rstrip().endswith('"max_lifetime"}')  # then told to reconnect


async def test_route_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get("/v1/stream")).status_code == 401
    bad = await client.get("/v1/stream", headers={"X-API-Key": "hh_deadbeef_nope"})
    assert bad.status_code == 401


async def test_api_keys_can_stream_too(
    app: FastAPI, client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    _fast_sse(app)
    key = (await client.post("/v1/api-keys", json={"name": "ci"}, headers=auth_headers)).json()[
        "key"
    ]
    response = await client.get("/v1/stream", headers={"X-API-Key": key})
    assert response.status_code == 200
    assert response.text.startswith("event: ready")


async def test_stream_never_outlives_its_access_token(
    app: FastAPI, client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, sse_max_lifetime_seconds=30, sse_heartbeat_seconds=0.1
    )
    user = await _user_id(client, auth_headers)
    short_lived = {
        "Authorization": f"Bearer {create_token(user, 'access', ttl=timedelta(seconds=1))}"
    }

    started = time.monotonic()
    response = await client.get("/v1/stream", headers=short_lived)

    assert time.monotonic() - started < 5  # not 30s: capped by the token's remaining life
    assert "event: reconnect" in response.text


async def test_open_streams_per_user_are_capped_and_slots_are_released(
    app: FastAPI, client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    _fast_sse(app, sse_max_streams_per_user=1)

    first = asyncio.create_task(client.get("/v1/stream", headers=auth_headers))
    await asyncio.sleep(0.3)  # the first stream is now open
    second = await client.get("/v1/stream", headers=auth_headers)
    assert second.status_code == 429
    assert second.headers["Retry-After"] == "5"

    assert (await first).status_code == 200
    again = await client.get("/v1/stream", headers=auth_headers)  # slot was released
    assert again.status_code == 200


# --------------------------------------------------------------- who publishes what


async def test_api_publishes_event_created_but_not_for_idempotent_replays(
    client: AsyncClient, auth_headers: dict[str, str], redis: FakeAsyncRedis
) -> None:
    bus = EventBus(redis)
    user = await _user_id(client, auth_headers)
    headers = {**auth_headers, "Idempotency-Key": "rt-1"}
    body = {"type": "order.created", "payload": {"n": 1}}
    async with bus.subscribe(user) as sub:
        created = (await client.post("/v1/events", json=body, headers=headers)).json()
        message = await _next(sub)
        assert message["type"] == "event.created"
        assert message["event_id"] == created["id"]
        assert message["event_type"] == "order.created"

        await client.post("/v1/events", json=body, headers=headers)  # replay: nothing new happened
        assert await sub.next_message(0.2) is None


async def test_worker_publishes_delivery_updates(
    client: AsyncClient,
    auth_headers: dict[str, str],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
    receiver: Receiver,
    redis: FakeAsyncRedis,
) -> None:
    bus = EventBus(redis)
    user = await _user_id(client, auth_headers)
    endpoint, event, [did] = await setup_deliveries(client, auth_headers, delivery_queue)
    receiver.script = [503, 200]
    worker = make_worker(bus=bus)

    async with bus.subscribe(user) as sub:
        await worker.run_once()
        failed = await _next(sub)
        assert failed["type"] == "delivery.updated"
        assert failed["delivery_id"] == str(did)
        assert failed["event_id"] == event["id"]
        assert failed["endpoint_id"] == endpoint["id"]
        assert (failed["status"], failed["last_status_code"]) == ("pending", 503)
        assert failed["next_attempt_at"] is not None  # a retry is scheduled
        assert failed["attempt"]["success"] is False

        # fast-forward to the retry
        await delivery_queue.enqueue([did])
        worker_now = make_worker(bus=bus)
        await worker_now.run_once()  # not due yet: parked, nothing published
        assert await sub.next_message(0.1) is None


async def test_worker_publishes_success_and_endpoint_auto_disable(
    client: AsyncClient,
    auth_headers: dict[str, str],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
    receiver: Receiver,
    redis: FakeAsyncRedis,
) -> None:
    bus = EventBus(redis)
    user = await _user_id(client, auth_headers)
    endpoint, _, ids = await setup_deliveries(client, auth_headers, delivery_queue, events=2)
    receiver.script = [200, 500]
    worker = make_worker(bus=bus, delivery_max_attempts=1, endpoint_disable_threshold=1)

    async with bus.subscribe(user) as sub:
        await worker.run_once()
        assert (await _next(sub))["status"] == "succeeded"
        await worker.run_once()
        dead = await _next(sub)
        assert dead["status"] == "dead"
        disabled = await _next(sub)
        assert disabled == {
            "type": "endpoint.disabled",
            "endpoint_id": endpoint["id"],
            "reason": "1 consecutive deliveries failed",
        }
    assert (await get_delivery(client, auth_headers, ids[0]))["status"] == "succeeded"


async def test_api_publishes_on_replay(
    client: AsyncClient,
    auth_headers: dict[str, str],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
    receiver: Receiver,
    redis: FakeAsyncRedis,
) -> None:
    bus = EventBus(redis)
    user = await _user_id(client, auth_headers)
    endpoint, _, ids = await setup_deliveries(client, auth_headers, delivery_queue, events=2)
    receiver.default = 500
    worker = make_worker(delivery_max_attempts=1)
    for _ in ids:
        await worker.run_once()

    async with bus.subscribe(user) as sub:
        await client.post(f"/v1/deliveries/{ids[0]}/replay", headers=auth_headers)
        single = await _next(sub)
        assert (single["type"], single["status"]) == ("delivery.updated", "pending")
        assert single["delivery_id"] == str(ids[0])

        await client.post(f"/v1/endpoints/{endpoint['id']}/replay-dead", headers=auth_headers)
        bulk = await _next(sub)
        assert bulk == {"type": "deliveries.replayed", "endpoint_id": endpoint["id"], "count": 1}


class _BrokenBus:
    async def publish(self, *args: object, **kwargs: object) -> None:
        raise RedisConnectionError("pubsub is down")


async def test_a_realtime_outage_never_fails_the_request_or_the_delivery(
    app: FastAPI,
    client: AsyncClient,
    auth_headers: dict[str, str],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
) -> None:
    app.dependency_overrides[get_event_bus] = lambda: _BrokenBus()
    _, _, [did] = await setup_deliveries(client, auth_headers, delivery_queue)  # ingest published
    worker = make_worker(bus=_BrokenBus())  # type: ignore[arg-type]

    await worker.run_once()

    assert (await get_delivery(client, auth_headers, did))["status"] == "succeeded"
