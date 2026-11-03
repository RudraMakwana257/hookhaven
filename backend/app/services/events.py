import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import IdempotencyConflictError, PayloadTooLargeError
from app.models import Delivery, DeliveryStatus, Endpoint, Event, User
from app.queue.delivery_queue import DeliveryPublisher

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class IngestResult:
    event: Event
    deliveries: list[Delivery]
    replayed: bool


def canonical_json(event_type: str, payload: dict[str, Any]) -> str:
    """Stable serialisation: key order and whitespace must not change an event's identity."""
    return json.dumps(
        {"type": event_type, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def hash_event(event_type: str, payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(event_type, payload).encode()).hexdigest()


def _subscribes(endpoint: Endpoint, event_type: str) -> bool:
    return "*" in endpoint.event_types or event_type in endpoint.event_types


async def _find_by_key(session: AsyncSession, user_id: uuid.UUID, key: str) -> Event | None:
    result = await session.execute(
        select(Event).where(Event.user_id == user_id, Event.idempotency_key == key)
    )
    return result.scalar_one_or_none()


async def _deliveries_for(session: AsyncSession, event_id: uuid.UUID) -> list[Delivery]:
    result = await session.execute(
        select(Delivery)
        .where(Delivery.event_id == event_id)
        .order_by(Delivery.created_at, Delivery.id)
    )
    return list(result.scalars())


async def _replay(session: AsyncSession, existing: Event, payload_hash: str) -> IngestResult:
    if existing.payload_hash != payload_hash:
        raise IdempotencyConflictError
    return IngestResult(existing, await _deliveries_for(session, existing.id), replayed=True)


async def _publish_best_effort(
    queue: DeliveryPublisher, event_id: uuid.UUID, deliveries: list[Delivery]
) -> None:
    """Enqueue after commit. A queue outage must not fail an event we already stored."""
    try:
        await queue.enqueue([d.id for d in deliveries])
    except (RedisError, OSError):
        log.warning(
            "enqueue_failed_will_be_reconciled",
            event_id=str(event_id),
            delivery_count=len(deliveries),
            exc_info=True,
        )


async def ingest_event(
    session: AsyncSession,
    queue: DeliveryPublisher,
    user: User,
    *,
    event_type: str,
    payload: dict[str, Any],
    idempotency_key: str | None,
    max_payload_bytes: int,
) -> IngestResult:
    """Store the event and one delivery per matching endpoint, atomically; then enqueue.

    Ordering is deliberate:  commit to Postgres FIRST, enqueue to Redis SECOND.
    - Crash between the two  -> deliveries are durable and pending; the reconciler re-enqueues.
    - Enqueue before commit  -> a worker could pick up a delivery that doesn't exist yet.
    """
    # Capture the id now: rollback() expires ORM instances, and touching `user.id` afterwards
    # would trigger a lazy load, which is illegal in async code (MissingGreenlet).
    user_id = user.id

    if len(canonical_json(event_type, payload).encode()) > max_payload_bytes:
        raise PayloadTooLargeError

    payload_hash = hash_event(event_type, payload)

    if idempotency_key is not None:
        existing = await _find_by_key(session, user_id, idempotency_key)
        if existing is not None:
            return await _replay(session, existing, payload_hash)

    result = await session.execute(
        select(Endpoint).where(Endpoint.user_id == user_id, Endpoint.is_active.is_(True))
    )
    targets = [e for e in result.scalars() if _subscribes(e, event_type)]

    now = datetime.now(UTC)
    event = Event(
        id=uuid.uuid4(),
        user_id=user_id,
        type=event_type,
        created_at=now,
        payload=payload,
        payload_hash=payload_hash,
        idempotency_key=idempotency_key,
    )
    deliveries = [
        Delivery(
            id=uuid.uuid4(),
            event_id=event.id,
            endpoint_id=endpoint.id,
            status=DeliveryStatus.PENDING.value,
            next_attempt_at=now,
            created_at=now,
        )
        for endpoint in targets
    ]
    session.add(event)
    session.add_all(deliveries)

    try:
        await session.commit()
    except IntegrityError:
        # Lost a race: a concurrent request with the same Idempotency-Key committed first.
        # The database constraint is the real arbiter; the SELECT above is only a fast path.
        await session.rollback()
        if idempotency_key is None:
            raise
        winner = await _find_by_key(session, user_id, idempotency_key)
        if winner is None:
            raise
        return await _replay(session, winner, payload_hash)

    await _publish_best_effort(queue, event.id, deliveries)
    return IngestResult(event, deliveries, replayed=False)


async def list_events(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    limit: int,
    offset: int,
    event_type: str | None = None,
) -> list[Event]:
    query = select(Event).where(Event.user_id == user_id)
    if event_type is not None:
        query = query.where(Event.type == event_type)
    result = await session.execute(
        query.order_by(Event.created_at.desc(), Event.id).limit(limit).offset(offset)
    )
    return list(result.scalars())


async def get_event_with_deliveries(
    session: AsyncSession, user_id: uuid.UUID, event_id: uuid.UUID
) -> tuple[Event, list[Delivery]] | None:
    result = await session.execute(
        select(Event).where(Event.id == event_id, Event.user_id == user_id)
    )
    event = result.scalar_one_or_none()
    if event is None:
        return None
    return event, await _deliveries_for(session, event.id)
