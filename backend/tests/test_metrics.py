import asyncio
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from prometheus_client import REGISTRY
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_queue_inspector, get_settings
from app.core.config import Settings
from app.core.time import utcnow
from app.models import Delivery
from app.queue.delivery_queue import DeliveryQueue
from app.workers.delivery_worker import Worker
from app.workers.reconciler import Reconciler

from .support import Clock, Receiver, setup_deliveries


def sample(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def _settings(app: FastAPI, **overrides: Any) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, **overrides)


# --------------------------------------------------------------------------- HTTP


async def test_http_metrics_use_route_templates_not_raw_paths(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    created = (
        await client.post(
            "/v1/endpoints", json={"url": "https://hooks.example.com/x"}, headers=auth_headers
        )
    ).json()
    labels = {"method": "GET", "route": "/v1/endpoints/{endpoint_id}", "status": "200"}
    before = sample("hookhaven_http_requests_total", **labels)

    await client.get(f"/v1/endpoints/{created['id']}", headers=auth_headers)

    assert sample("hookhaven_http_requests_total", **labels) == before + 1
    scrape = await client.get("/metrics")
    assert created["id"] not in scrape.text  # ids would explode label cardinality


async def test_unknown_paths_collapse_into_one_label(client: AsyncClient) -> None:
    labels = {"method": "GET", "route": "unmatched", "status": "404"}
    before = sample("hookhaven_http_requests_total", **labels)
    for path in ("/wp-admin", "/.env", "/v1/nope/123"):
        assert (await client.get(path)).status_code == 404
    assert sample("hookhaven_http_requests_total", **labels) == before + 3


async def test_scrape_output_and_the_endpoint_does_not_measure_itself(
    client: AsyncClient,
) -> None:
    first = await client.get("/metrics")
    assert first.status_code == 200
    assert first.headers["content-type"].startswith("text/plain")
    assert "hookhaven_build_info" in first.text
    assert "# TYPE hookhaven_http_request_duration_seconds histogram" in first.text
    assert 'route="/metrics"' not in (await client.get("/metrics")).text


async def test_metrics_endpoint_can_require_a_bearer_token(
    app: FastAPI, client: AsyncClient
) -> None:
    _settings(app, metrics_token="s3cret-scrape-token")
    assert (await client.get("/metrics")).status_code == 401
    wrong = await client.get("/metrics", headers={"Authorization": "Bearer nope"})
    assert wrong.status_code == 401
    right = await client.get("/metrics", headers={"Authorization": "Bearer s3cret-scrape-token"})
    assert right.status_code == 200


async def test_metrics_endpoint_can_be_disabled(app: FastAPI, client: AsyncClient) -> None:
    _settings(app, metrics_enabled=False)
    assert (await client.get("/metrics")).status_code == 404


# ------------------------------------------------------------------ scrape-time gauges


async def test_queue_depth_and_delivery_lag_gauges(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_maker: async_sessionmaker[AsyncSession],
    delivery_queue: DeliveryQueue,
) -> None:
    await setup_deliveries(client, auth_headers, delivery_queue, events=2)
    await client.get("/metrics")
    assert sample("hookhaven_queue_depth", state="ready") == 2
    assert sample("hookhaven_queue_depth", state="processing") == 0
    assert sample("hookhaven_delivery_lag_seconds") < 5  # freshly created: not meaningfully late

    async with session_maker() as session:  # make one delivery 5 minutes overdue
        await session.execute(
            update(Delivery).values(next_attempt_at=utcnow() - timedelta(minutes=5))
        )
        await session.commit()
    await client.get("/metrics")
    assert 295 <= sample("hookhaven_delivery_lag_seconds") <= 400


async def test_scrape_survives_redis_being_down(app: FastAPI, client: AsyncClient) -> None:
    class Broken:
        async def stats(self) -> dict[str, int]:
            raise RedisConnectionError("down")

    app.dependency_overrides[get_queue_inspector] = lambda: Broken()
    before = sample("hookhaven_metrics_scrape_errors_total", source="redis")

    response = await client.get("/metrics")

    assert response.status_code == 200  # a dead dependency must not blind monitoring
    assert sample("hookhaven_metrics_scrape_errors_total", source="redis") == before + 1


# ------------------------------------------------------------------- business metrics


async def test_event_ingestion_counters(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    accepted = sample("hookhaven_events_ingested_total", result="accepted")
    replayed = sample("hookhaven_events_ingested_total", result="replayed")
    headers = {**auth_headers, "Idempotency-Key": "k-metrics"}
    body = {"type": "a.b", "payload": {}}

    await client.post("/v1/events", json=body, headers=headers)
    await client.post("/v1/events", json=body, headers=headers)

    assert sample("hookhaven_events_ingested_total", result="accepted") == accepted + 1
    assert sample("hookhaven_events_ingested_total", result="replayed") == replayed + 1


async def test_worker_records_attempt_outcomes_and_latency(
    client: AsyncClient,
    auth_headers: dict[str, str],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
    receiver: Receiver,
) -> None:
    await setup_deliveries(client, auth_headers, delivery_queue, events=2)
    receiver.script = [500, 200]
    names = {
        "five": ("hookhaven_delivery_attempts_total", {"outcome": "http_5xx"}),
        "ok": ("hookhaven_delivery_attempts_total", {"outcome": "success"}),
        "retry": ("hookhaven_deliveries_finalized_total", {"result": "retry_scheduled"}),
        "done": ("hookhaven_deliveries_finalized_total", {"result": "succeeded"}),
        "timed": ("hookhaven_delivery_attempt_duration_seconds_count", {}),
    }
    before = {k: sample(n, **labels) for k, (n, labels) in names.items()}

    worker = make_worker()
    await worker.run_once()
    await worker.run_once()

    after = {k: sample(n, **labels) for k, (n, labels) in names.items()}
    assert {k: after[k] - before[k] for k in names} == {
        "five": 1,
        "ok": 1,
        "retry": 1,
        "done": 1,
        "timed": 2,
    }


async def test_worker_records_dead_letters_and_auto_disable(
    client: AsyncClient,
    auth_headers: dict[str, str],
    delivery_queue: DeliveryQueue,
    make_worker: Callable[..., Worker],
    receiver: Receiver,
) -> None:
    await setup_deliveries(client, auth_headers, delivery_queue, events=2)
    receiver.default = 500
    dead = sample("hookhaven_deliveries_finalized_total", result="dead")
    disabled = sample("hookhaven_endpoints_auto_disabled_total")
    skipped = sample("hookhaven_delivery_attempts_total", outcome="endpoint_disabled")
    timed = sample("hookhaven_delivery_attempt_duration_seconds_count")

    worker = make_worker(delivery_max_attempts=1, endpoint_disable_threshold=1)
    await worker.run_once()  # dies -> endpoint disabled
    await worker.run_once()  # skipped, endpoint is disabled

    assert sample("hookhaven_deliveries_finalized_total", result="dead") == dead + 2
    assert sample("hookhaven_endpoints_auto_disabled_total") == disabled + 1
    assert sample("hookhaven_delivery_attempts_total", outcome="endpoint_disabled") == skipped + 1
    # the skipped delivery made no HTTP call, so it must not pollute the latency histogram
    assert sample("hookhaven_delivery_attempt_duration_seconds_count") == timed + 1


async def test_reconciler_counts_requeued_deliveries(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_maker: async_sessionmaker[AsyncSession],
    delivery_queue: DeliveryQueue,
    clock: Clock,
) -> None:
    await setup_deliveries(client, auth_headers, delivery_queue, enqueue=False)
    clock.advance(130)
    reconciler = Reconciler(
        session_factory=session_maker,
        queue=delivery_queue,
        settings=Settings(_env_file=None),
        clock=clock,
    )
    before = sample("hookhaven_reconciler_requeued_total")
    await reconciler.sweep()
    assert sample("hookhaven_reconciler_requeued_total") == before + 1


# ------------------------------------------------------------ errors & hygiene


async def test_unhandled_errors_return_a_safe_body_with_a_correlation_id(app: FastAPI) -> None:
    @app.get("/_boom")
    async def boom() -> None:
        raise RuntimeError("secret internal detail: db password is hunter2")

    labels = {"method": "GET", "route": "/_boom", "status": "500"}
    before = sample("hookhaven_http_requests_total", **labels)
    transport = ASGITransport(app=app, raise_app_exceptions=False)

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        response = await c.get("/_boom", headers={"X-Request-ID": "trace-abc-123"})

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error", "request_id": "trace-abc-123"}
    assert response.headers["X-Request-ID"] == "trace-abc-123"
    assert "hunter2" not in response.text  # internals never reach the client
    assert sample("hookhaven_http_requests_total", **labels) == before + 1


@pytest.mark.parametrize(
    "hostile", ["x" * 100, "has space", "line\tbreak", "<script>alert(1)</script>"]
)
async def test_hostile_request_ids_are_replaced(client: AsyncClient, hostile: str) -> None:
    response = await client.get("/health", headers={"X-Request-ID": hostile})
    assert response.headers["X-Request-ID"] != hostile
    assert len(response.headers["X-Request-ID"]) == 32


async def test_security_headers(client: AsyncClient) -> None:
    health = await client.get("/health")
    assert health.headers["X-Content-Type-Options"] == "nosniff"
    assert health.headers["Referrer-Policy"] == "no-referrer"
    assert "Cache-Control" not in health.headers

    api = await client.get("/v1/auth/me")  # even error responses on the API are uncacheable
    assert api.headers["Cache-Control"] == "no-store"
    assert api.headers["X-Content-Type-Options"] == "nosniff"


async def test_slow_requests_do_not_block_the_event_loop_metrics_path(
    client: AsyncClient,
) -> None:
    """Scraping while traffic flows must work (no shared-lock deadlocks)."""
    results = await asyncio.gather(
        *(client.get("/metrics") for _ in range(5)), client.get("/health")
    )
    assert all(r.status_code == 200 for r in results)
