import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import AuthenticatedUser, BusDep, QueueDep, SessionDep
from app.api.limits import limit_api
from app.core.errors import DeliveryNotReplayableError, EndpointDisabledError
from app.core.time import utcnow
from app.realtime.bus import publish_best_effort as publish_realtime
from app.realtime.messages import delivery_updated
from app.schemas.event import AttemptOut, DeliveryDetail, DeliveryOut
from app.services import deliveries
from app.services.publishing import publish_best_effort

router = APIRouter(prefix="/deliveries", tags=["deliveries"], dependencies=[Depends(limit_api)])


@router.get("", response_model=list[DeliveryOut])
async def list_deliveries(
    user: AuthenticatedUser,
    session: SessionDep,
    status: Annotated[
        Literal["pending", "succeeded", "dead"] | None,
        Query(description="`dead` is the dead-letter queue."),
    ] = None,
    endpoint_id: uuid.UUID | None = None,
    event_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[DeliveryOut]:
    rows = await deliveries.list_deliveries(
        session,
        user.id,
        status=status,
        endpoint_id=endpoint_id,
        event_id=event_id,
        limit=limit,
        offset=offset,
    )
    return [DeliveryOut.model_validate(row) for row in rows]


@router.get("/{delivery_id}", response_model=DeliveryDetail)
async def get_delivery(
    delivery_id: uuid.UUID, user: AuthenticatedUser, session: SessionDep
) -> DeliveryDetail:
    """A delivery with its full attempt history (status codes, errors, response snippets)."""
    found = await deliveries.get_delivery_with_attempts(session, user.id, delivery_id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Delivery not found")
    delivery, attempts = found
    return DeliveryDetail(
        **DeliveryOut.model_validate(delivery).model_dump(),
        event_id=delivery.event_id,
        attempts=[AttemptOut.model_validate(a) for a in attempts],
    )


@router.post(
    "/{delivery_id}/replay", response_model=DeliveryOut, status_code=status.HTTP_202_ACCEPTED
)
async def replay_delivery(
    delivery_id: uuid.UUID,
    user: AuthenticatedUser,
    session: SessionDep,
    queue: QueueDep,
    bus: BusDep,
) -> DeliveryOut:
    """Re-send a dead delivery with a fresh retry budget (same `Hookhaven-Delivery-Id`)."""
    user_id = user.id
    try:
        delivery = await deliveries.replay_delivery(session, user.id, delivery_id, utcnow())
    except EndpointDisabledError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Endpoint is disabled; re-enable it before replaying"
        ) from None
    except DeliveryNotReplayableError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Only dead deliveries can be replayed (current status: {exc.status})",
        ) from None
    if delivery is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Delivery not found")
    await publish_best_effort(queue, [delivery.id], reason="replay")
    await publish_realtime(
        bus,
        user_id,
        delivery_updated(
            delivery_id=delivery.id,
            event_id=delivery.event_id,
            endpoint_id=delivery.endpoint_id,
            status=delivery.status,
            attempt_count=delivery.attempt_count,
            next_attempt_at=delivery.next_attempt_at,
            last_status_code=delivery.last_status_code,
            last_error=delivery.last_error,
        ),
    )
    return DeliveryOut.model_validate(delivery)
