import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Delivery, DeliveryAttempt, Event


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
