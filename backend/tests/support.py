"""Shared helpers for worker-related tests."""

import asyncio
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import httpx
from httpx import AsyncClient

from app.core.time import utcnow
from app.queue.delivery_queue import DeliveryQueue

URL = "https://hooks.example.com/in"
EMPTY = {"ready": 0, "scheduled": 0, "processing": 0}


class Clock:
    """Controllable time, starting just after 'now' so freshly created deliveries are due."""

    def __init__(self) -> None:
        self.now = utcnow() + timedelta(seconds=60)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class Receiver:
    """A scriptable fake webhook receiver behind httpx.MockTransport."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.script: list[int | Exception] = []
        self.default = 200
        self.delay = 0.0
        self.inflight = 0
        self.max_inflight = 0

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            outcome = self.script.pop(0) if self.script else self.default
            if isinstance(outcome, Exception):
                raise outcome
            return httpx.Response(outcome, text=f"body-{outcome}")
        finally:
            self.inflight -= 1

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


async def setup_deliveries(
    client: AsyncClient,
    headers: dict[str, str],
    queue: DeliveryQueue,
    *,
    events: int = 1,
    enqueue: bool = True,
) -> tuple[dict[str, Any], dict[str, Any], list[uuid.UUID]]:
    """Register an endpoint, ingest events, and (optionally) put their deliveries on the queue."""
    endpoint = (await client.post("/v1/endpoints", json={"url": URL}, headers=headers)).json()
    delivery_ids: list[uuid.UUID] = []
    event: dict[str, Any] = {}
    for n in range(events):
        body = {"type": "order.created", "payload": {"n": n}}
        event = (await client.post("/v1/events", json=body, headers=headers)).json()
        delivery_ids += [uuid.UUID(d["id"]) for d in event["deliveries"]]
    if enqueue:
        await queue.enqueue(delivery_ids)
    return endpoint, event, delivery_ids


async def get_delivery(client: AsyncClient, headers: dict[str, str], delivery_id: uuid.UUID) -> Any:
    response = await client.get(f"/v1/deliveries/{delivery_id}", headers=headers)
    assert response.status_code == 200
    return response.json()


async def get_endpoint(client: AsyncClient, headers: dict[str, str], endpoint_id: str) -> Any:
    response = await client.get(f"/v1/endpoints/{endpoint_id}", headers=headers)
    assert response.status_code == 200
    return response.json()


async def wait_until(predicate: Callable[[], Any], limit_seconds: float = 10.0) -> None:
    """Poll a condition (it depends on DB state, so there is no event to wait on)."""
    async with asyncio.timeout(limit_seconds):
        while not await predicate():  # noqa: ASYNC110
            await asyncio.sleep(0.02)
