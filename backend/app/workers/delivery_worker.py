"""The delivery worker: claim -> reserve attempt -> sign & send -> record -> ack / reschedule.

Guarantees and how they are achieved (details in docs/adr/0005):
  * At-least-once delivery: an id is only released (ack) after its outcome is committed. A crash
    anywhere earlier leaves a lease that expires and is re-queued.
  * No duplicate concurrent sends: the attempt is *reserved* with a single compare-and-set UPDATE
    (`status = pending AND due`), so two workers holding the same id cannot both proceed.
  * Receivers can dedupe: `Hookhaven-Delivery-Id` is identical across all retries.
"""

import asyncio
import contextlib
import json
import random
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
import structlog
from cryptography.fernet import InvalidToken
from redis.exceptions import RedisError
from sqlalchemy import insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.crypto import decrypt_secret
from app.core.errors import UnsafeUrlError
from app.core.safe_http import send_webhook
from app.core.signing import SIGNATURE_HEADER, build_signature_header
from app.core.ssrf import Resolver, UrlPolicy, system_resolver
from app.core.time import as_utc, utcnow
from app.models import Delivery, DeliveryAttempt, DeliveryStatus, Endpoint, Event
from app.queue.delivery_queue import DeliveryQueue
from app.workers.reconciler import Reconciler
from app.workers.retry import next_retry_delay

log = structlog.get_logger(__name__)

SNIPPET_LIMIT = 2048
ERROR_LIMIT = 500
USER_AGENT = "Hookhaven/0.1"


@dataclass(frozen=True)
class AttemptOutcome:
    success: bool
    started_at: datetime
    duration_ms: int
    status_code: int | None = None
    error: str | None = None
    response_snippet: str | None = None


def build_envelope(event: Event) -> bytes:
    """The exact bytes that are signed and sent."""
    return json.dumps(
        {
            "id": str(event.id),
            "type": event.type,
            "created_at": as_utc(event.created_at).isoformat(),
            "data": event.payload,
        },
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()


class Worker:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        queue: DeliveryQueue,
        settings: Settings,
        policy: UrlPolicy,
        resolver: Resolver = system_resolver,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], datetime] = utcnow,
        rng: Callable[[], float] = random.random,
        poll_interval: float = 0.5,
        maintenance_interval: float = 1.0,
    ) -> None:
        self._session_factory = session_factory
        self._queue = queue
        self._settings = settings
        self._policy = policy
        self._resolver = resolver
        self._transport = transport
        self._clock = clock
        self._rng = rng
        self._reconciler = Reconciler(
            session_factory=session_factory, queue=queue, settings=settings, clock=clock
        )
        self._poll_interval = poll_interval
        self._maintenance_interval = maintenance_interval

    # ------------------------------------------------------------------ loops

    async def run(self, stop: asyncio.Event) -> None:
        """Run until `stop` is set. In-flight deliveries finish first (graceful shutdown)."""
        async with asyncio.TaskGroup() as group:
            group.create_task(self._maintenance_loop(stop))
            group.create_task(self._reconcile_loop(stop))
            for _ in range(self._settings.worker_concurrency):
                group.create_task(self._consumer_loop(stop))

    async def _sleep_unless_stopped(self, stop: asyncio.Event, seconds: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=seconds)

    async def _consumer_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                if not await self.run_once():
                    await self._sleep_unless_stopped(stop, self._poll_interval)
            except RedisError:
                log.warning("redis_unavailable_in_consumer", exc_info=True)
                await self._sleep_unless_stopped(stop, 1.0)

    async def _maintenance_loop(self, stop: asyncio.Event) -> None:
        """Promote due retries and recover work abandoned by crashed workers."""
        while not stop.is_set():
            try:
                now = self._clock().timestamp()
                promoted = await self._queue.promote_due(now=now)
                reaped = await self._queue.reap_expired(now=now)
                if reaped:
                    log.warning("expired_leases_requeued", count=reaped)
                if promoted:
                    log.info("retries_promoted", count=promoted)
            except RedisError:
                log.warning("redis_unavailable_in_maintenance", exc_info=True)
            await self._sleep_unless_stopped(stop, self._maintenance_interval)

    async def _reconcile_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self._reconciler.sweep_if_leader()
            except RedisError:
                log.warning("redis_unavailable_in_reconciler", exc_info=True)
            except Exception:
                log.exception("reconciler_sweep_failed")  # never take the worker down
            await self._sleep_unless_stopped(stop, self._settings.reconcile_interval_seconds)

    # ------------------------------------------------------------------ one job

    async def run_once(self) -> bool:
        """Claim and process a single delivery. Returns False if the queue was empty."""
        claimed = await self._queue.claim(
            now=self._clock().timestamp(), lease_seconds=self._settings.delivery_lease_seconds
        )
        if claimed is None:
            return False
        structlog.contextvars.bind_contextvars(delivery_id=str(claimed))
        try:
            await self.process(claimed)
        except Exception:
            # Leave the lease alone: it expires and the delivery is retried. Acking here could
            # silently drop a delivery because of a bug or a transient database error.
            log.exception("delivery_processing_crashed")
        finally:
            structlog.contextvars.unbind_contextvars("delivery_id")
        return True

    async def process(self, delivery_id: uuid.UUID) -> None:
        reserved = await self._reserve_attempt(delivery_id)
        if reserved is None:
            await self._release_unreserved(delivery_id)
            return
        attempt_number, cycle_start = reserved

        loaded = await self._load(delivery_id)
        if loaded is None:  # event or endpoint deleted in the meantime
            await self._queue.ack(delivery_id)
            return
        event, endpoint = loaded

        if not endpoint.is_active:
            started = self._clock()
            outcome = AttemptOutcome(
                success=False, started_at=started, duration_ms=0, error="endpoint is disabled"
            )
            await self._finalize(
                delivery_id, endpoint.id, attempt_number, cycle_start, outcome, force_dead=True
            )
            return

        outcome = await self._deliver(delivery_id, attempt_number, event, endpoint)
        await self._finalize(delivery_id, endpoint.id, attempt_number, cycle_start, outcome)

    # --------------------------------------------------------------- db steps

    async def _reserve_attempt(self, delivery_id: uuid.UUID) -> tuple[int, int] | None:
        """Compare-and-set: only one worker can win attempt N of a delivery.

        Winning also pushes `next_attempt_at` out to the lease deadline. That is the in-flight
        marker: while this attempt runs, the delivery is "not due", so no other worker can
        reserve attempt N+1 (duplicate id, early lease expiry...). If this worker dies, the
        deadline passes and the delivery becomes due again by itself.
        """
        now = self._clock()
        in_flight_until = now + timedelta(seconds=self._settings.delivery_lease_seconds)
        async with self._session_factory() as session:
            result = await session.execute(
                update(Delivery)
                .where(
                    Delivery.id == delivery_id,
                    Delivery.status == DeliveryStatus.PENDING.value,
                    or_(Delivery.next_attempt_at.is_(None), Delivery.next_attempt_at <= now),
                )
                .values(attempt_count=Delivery.attempt_count + 1, next_attempt_at=in_flight_until)
                .returning(Delivery.attempt_count, Delivery.cycle_start_attempt)
                .execution_options(synchronize_session=False)
            )
            row = result.one_or_none()
            await session.commit()
        return None if row is None else (int(row.attempt_count), int(row.cycle_start_attempt))

    async def _release_unreserved(self, delivery_id: uuid.UUID) -> None:
        """We did not win the reservation. Work out why and tidy the queue."""
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(Delivery.status, Delivery.next_attempt_at).where(
                        Delivery.id == delivery_id
                    )
                )
            ).first()
        if row is not None and row.status == DeliveryStatus.PENDING.value and row.next_attempt_at:
            due = as_utc(row.next_attempt_at)
            if due > self._clock():
                # Claimed early (e.g. a lease expired right after a retry was decided): park it
                # until its real due time instead of delivering ahead of schedule.
                await self._queue.schedule_retry(delivery_id, due_at=due.timestamp())
                return
        # Already succeeded / dead / deleted, or another worker holds it: nothing to do.
        await self._queue.ack(delivery_id)

    async def _load(self, delivery_id: uuid.UUID) -> tuple[Event, Endpoint] | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(Event, Endpoint)
                    .join(Delivery, Delivery.event_id == Event.id)
                    .join(Endpoint, Endpoint.id == Delivery.endpoint_id)
                    .where(Delivery.id == delivery_id)
                )
            ).first()
        return None if row is None else (row[0], row[1])

    async def _finalize(
        self,
        delivery_id: uuid.UUID,
        endpoint_id: uuid.UUID,
        attempt_number: int,
        cycle_start: int,
        outcome: AttemptOutcome,
        *,
        force_dead: bool = False,
    ) -> None:
        """Commit the outcome, THEN release/reschedule in Redis (never the other way round)."""
        now = self._clock()
        retry_due: datetime | None = None

        if outcome.success:
            status = DeliveryStatus.SUCCEEDED
            next_attempt_at = None
        else:
            delay = (
                None
                if force_dead
                else next_retry_delay(
                    attempt_number - cycle_start,  # budget is per retry cycle (replay resets it)
                    self._settings.delivery_max_attempts,
                    self._rng,
                )
            )
            if delay is None:
                status, next_attempt_at = DeliveryStatus.DEAD, None
            else:
                status = DeliveryStatus.PENDING
                retry_due = next_attempt_at = now + delay

        # Writes only, no preceding read: avoids lock-upgrade conflicts on SQLite.
        async with self._session_factory() as session:
            await session.execute(
                insert(DeliveryAttempt).values(
                    id=uuid.uuid4(),
                    delivery_id=delivery_id,
                    attempt_number=attempt_number,
                    started_at=outcome.started_at,
                    duration_ms=outcome.duration_ms,
                    success=outcome.success,
                    status_code=outcome.status_code,
                    error=outcome.error,
                    response_snippet=outcome.response_snippet,
                )
            )
            await session.execute(
                update(Delivery)
                .where(Delivery.id == delivery_id)
                .values(
                    status=status.value,
                    next_attempt_at=next_attempt_at,
                    last_status_code=outcome.status_code,
                    last_error=outcome.error,
                    delivered_at=now if outcome.success else None,
                )
                .execution_options(synchronize_session=False)
            )
            disabled_after = await self._account_endpoint_health(
                session, endpoint_id, status, outcome, force_dead, now
            )
            await session.commit()

        if disabled_after is not None:
            log.warning(
                "endpoint_auto_disabled",
                endpoint_id=str(endpoint_id),
                consecutive_failures=disabled_after,
            )
        log.info(
            "delivery_attempt",
            attempt=attempt_number,
            success=outcome.success,
            status_code=outcome.status_code,
            error=outcome.error,
            duration_ms=outcome.duration_ms,
            resulting_status=status.value,
        )
        if retry_due is not None:
            await self._queue.schedule_retry(delivery_id, due_at=retry_due.timestamp())
        else:
            await self._queue.ack(delivery_id)

    async def _account_endpoint_health(
        self,
        session: AsyncSession,
        endpoint_id: uuid.UUID,
        status: DeliveryStatus,
        outcome: AttemptOutcome,
        force_dead: bool,
        now: datetime,
    ) -> int | None:
        """Track consecutive dead deliveries per endpoint; disable it past the threshold.

        Returns the failure count if THIS call disabled the endpoint. Only real delivery
        failures count: deliveries skipped because the endpoint is already disabled do not.
        """
        if outcome.success:
            await session.execute(
                update(Endpoint)
                .where(Endpoint.id == endpoint_id, Endpoint.consecutive_failures > 0)
                .values(consecutive_failures=0)
                .execution_options(synchronize_session=False)
            )
            return None
        if status is not DeliveryStatus.DEAD or force_dead:
            return None

        failures = (
            await session.execute(
                update(Endpoint)
                .where(Endpoint.id == endpoint_id)
                .values(consecutive_failures=Endpoint.consecutive_failures + 1)
                .returning(Endpoint.consecutive_failures)
                .execution_options(synchronize_session=False)
            )
        ).scalar_one_or_none()
        if failures is None or failures < self._settings.endpoint_disable_threshold:
            return None

        disabled = await session.execute(
            update(Endpoint)
            .where(Endpoint.id == endpoint_id, Endpoint.is_active.is_(True))
            .values(
                is_active=False,
                disabled_at=now,
                disabled_reason=f"{failures} consecutive deliveries failed",
            )
            .returning(Endpoint.id)
            .execution_options(synchronize_session=False)
        )
        return int(failures) if disabled.scalar_one_or_none() is not None else None

    # ------------------------------------------------------------------- http

    async def _deliver(
        self, delivery_id: uuid.UUID, attempt_number: int, event: Event, endpoint: Endpoint
    ) -> AttemptOutcome:
        started = self._clock()
        t0 = time.perf_counter()

        def outcome(**fields: object) -> AttemptOutcome:
            elapsed = int((time.perf_counter() - t0) * 1000)
            return AttemptOutcome(started_at=started, duration_ms=elapsed, **fields)  # type: ignore[arg-type]

        try:
            secret = decrypt_secret(endpoint.secret_encrypted)
        except InvalidToken:
            return outcome(success=False, error="cannot decrypt endpoint secret")

        body = build_envelope(event)
        headers = {
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "Hookhaven-Event-Id": str(event.id),
            "Hookhaven-Event-Type": event.type,
            "Hookhaven-Delivery-Id": str(delivery_id),  # stable across retries: dedupe on this
            "Hookhaven-Attempt": str(attempt_number),
            SIGNATURE_HEADER: build_signature_header(secret, int(started.timestamp()), body),
        }
        try:
            response = await send_webhook(
                endpoint.url,
                content=body,
                headers=headers,
                policy=self._policy,
                resolver=self._resolver,
                timeout_seconds=self._settings.delivery_timeout_seconds,
                transport=self._transport,
            )
        except UnsafeUrlError as exc:
            return outcome(success=False, error=f"blocked by destination policy: {exc.reason}")
        except (httpx.TimeoutException, TimeoutError):
            return outcome(success=False, error="request timed out")
        except httpx.HTTPError as exc:
            return outcome(success=False, error=f"{type(exc).__name__}: {exc}"[:ERROR_LIMIT])

        ok = 200 <= response.status_code < 300
        return outcome(
            success=ok,
            status_code=response.status_code,
            error=None if ok else f"HTTP {response.status_code}",
            response_snippet=response.body[:SNIPPET_LIMIT] or None,
        )


__all__ = ["AttemptOutcome", "Worker", "build_envelope"]
