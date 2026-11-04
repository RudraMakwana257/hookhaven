import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DeliveryNotReplayableError, EndpointDisabledError
from app.models import Delivery, DeliveryAttempt, DeliveryStatus, Endpoint, Event


async def get_delivery_with_attempts(
    session: AsyncSession, user_id: uuid.UUID, delivery_id: uuid.UUID
) -> tuple[Delivery, list[DeliveryAttempt]] | None:
    """Ownership is enforced through the event: deliveries have no user_id of their own."""
    result = await session.execute(
        select(Delivery)
        .join(Event, Event.id == Delivery.event_id)
        .where(Delivery.id == delivery_id, Event.user_id == user_id)
    )
    delivery = result.scalar_one_or_none()
    if delivery is None:
        return None
    attempts = await session.execute(
        select(DeliveryAttempt)
        .where(DeliveryAttempt.delivery_id == delivery.id)
        .order_by(DeliveryAttempt.attempt_number)
    )
    return delivery, list(attempts.scalars())


async def list_deliveries(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    status: str | None,
    endpoint_id: uuid.UUID | None,
    event_id: uuid.UUID | None,
    limit: int,
    offset: int,
) -> list[Delivery]:
    query = (
        select(Delivery).join(Event, Event.id == Delivery.event_id).where(Event.user_id == user_id)
    )
    if status is not None:
        query = query.where(Delivery.status == status)
    if endpoint_id is not None:
        query = query.where(Delivery.endpoint_id == endpoint_id)
    if event_id is not None:
        query = query.where(Delivery.event_id == event_id)
    result = await session.execute(
        query.order_by(Delivery.created_at.desc(), Delivery.id).limit(limit).offset(offset)
    )
    return list(result.scalars())


def _requeue_values(now: datetime) -> dict[str, object]:
    return {
        "status": DeliveryStatus.PENDING.value,
        "next_attempt_at": now,
        # New retry cycle: the budget counts attempts from here, attempt numbers keep growing.
        "cycle_start_attempt": Delivery.attempt_count,
    }


async def replay_delivery(
    session: AsyncSession, user_id: uuid.UUID, delivery_id: uuid.UUID, now: datetime
) -> Delivery | None:
    """Put a dead delivery back in play. None if it does not exist / is not the user's."""
    row = (
        await session.execute(
            select(Delivery, Endpoint)
            .join(Event, Event.id == Delivery.event_id)
            .join(Endpoint, Endpoint.id == Delivery.endpoint_id)
            .where(Delivery.id == delivery_id, Event.user_id == user_id)
        )
    ).first()
    if row is None:
        return None
    delivery, endpoint = row
    if not endpoint.is_active:
        raise EndpointDisabledError
    if delivery.status != DeliveryStatus.DEAD.value:
        raise DeliveryNotReplayableError(delivery.status)

    # Compare-and-set on status: two concurrent replays cannot both succeed.
    result = await session.execute(
        update(Delivery)
        .where(Delivery.id == delivery_id, Delivery.status == DeliveryStatus.DEAD.value)
        .values(**_requeue_values(now))
        .returning(Delivery.id)
        .execution_options(synchronize_session=False)
    )
    if result.scalar_one_or_none() is None:
        await session.rollback()
        raise DeliveryNotReplayableError("already replayed")
    await session.commit()
    await session.refresh(delivery)
    return delivery


async def replay_dead_for_endpoint(
    session: AsyncSession,
    user_id: uuid.UUID,
    endpoint_id: uuid.UUID,
    now: datetime,
    *,
    limit: int = 1000,
) -> list[uuid.UUID] | None:
    """Replay up to `limit` dead deliveries of one endpoint (oldest first).

    None if the endpoint does not exist / is not the user's.
    """
    endpoint = (
        await session.execute(
            select(Endpoint).where(Endpoint.id == endpoint_id, Endpoint.user_id == user_id)
        )
    ).scalar_one_or_none()
    if endpoint is None:
        return None
    if not endpoint.is_active:
        raise EndpointDisabledError

    dead_ids = list(
        (
            await session.execute(
                select(Delivery.id)
                .where(
                    Delivery.endpoint_id == endpoint_id,
                    Delivery.status == DeliveryStatus.DEAD.value,
                )
                .order_by(Delivery.created_at, Delivery.id)
                .limit(limit)
            )
        ).scalars()
    )
    if not dead_ids:
        return []
    result = await session.execute(
        update(Delivery)
        .where(Delivery.id.in_(dead_ids), Delivery.status == DeliveryStatus.DEAD.value)
        .values(**_requeue_values(now))
        .returning(Delivery.id)
        .execution_options(synchronize_session=False)
    )
    replayed = list(result.scalars())
    await session.commit()
    return replayed
