import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Delivery, DeliveryStatus, Endpoint, Event
from app.schemas.stats import EndpointCounts, StatsOut

WINDOW_HOURS = 24


async def get_stats(session: AsyncSession, user_id: uuid.UUID, now: datetime) -> StatsOut:
    since = now - timedelta(hours=WINDOW_HOURS)

    events = await session.scalar(
        select(func.count())
        .select_from(Event)
        .where(Event.user_id == user_id, Event.created_at >= since)
    )
    by_status = (
        await session.execute(
            select(Delivery.status, func.count())
            .join(Event, Event.id == Delivery.event_id)
            .where(Event.user_id == user_id, Delivery.created_at >= since)
            .group_by(Delivery.status)
        )
    ).all()
    dead_total = await session.scalar(
        select(func.count())
        .select_from(Delivery)
        .join(Event, Event.id == Delivery.event_id)
        .where(Event.user_id == user_id, Delivery.status == DeliveryStatus.DEAD.value)
    )
    endpoint_rows = (
        await session.execute(
            select(Endpoint.is_active, func.count())
            .where(Endpoint.user_id == user_id)
            .group_by(Endpoint.is_active)
        )
    ).all()

    deliveries = {s.value: 0 for s in DeliveryStatus}
    deliveries.update({status: count for status, count in by_status})
    active = sum(count for is_active, count in endpoint_rows if is_active)
    disabled = sum(count for is_active, count in endpoint_rows if not is_active)
    return StatsOut(
        window_hours=WINDOW_HOURS,
        events=events or 0,
        deliveries=deliveries,
        dead_total=dead_total or 0,
        endpoints=EndpointCounts(total=active + disabled, active=active, disabled=disabled),
    )
