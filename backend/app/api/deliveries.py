import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import AuthenticatedUser, SessionDep
from app.schemas.event import AttemptOut, DeliveryDetail, DeliveryOut
from app.services import deliveries

router = APIRouter(prefix="/deliveries", tags=["deliveries"])


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
