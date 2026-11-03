import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status

from app.api.deps import (
    AuthenticatedUser,
    QueueDep,
    SessionDep,
    SettingsDep,
    enforce_content_length,
)
from app.core.errors import IdempotencyConflictError, PayloadTooLargeError
from app.models import Delivery, Event
from app.schemas.event import DeliveryOut, EventCreate, EventDetail, EventOut
from app.services import events

router = APIRouter(prefix="/events", tags=["events"])

IdempotencyKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=255,
        pattern=r"^[\x21-\x7e]+$",  # printable ASCII, no spaces
        description="Retries with the same key and payload return the original event.",
    ),
]


def _detail(event: Event, deliveries: list[Delivery]) -> EventDetail:
    return EventDetail(
        **EventOut.model_validate(event).model_dump(),
        payload=event.payload,
        deliveries=[DeliveryOut.model_validate(d) for d in deliveries],
    )


@router.post(
    "",
    response_model=EventDetail,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(enforce_content_length)],
)
async def ingest_event(
    body: EventCreate,
    response: Response,
    user: AuthenticatedUser,
    session: SessionDep,
    queue: QueueDep,
    settings: SettingsDep,
    idempotency_key: IdempotencyKey = None,
) -> EventDetail:
    """Accept an event for asynchronous delivery. Returns 202 once it is durably stored."""
    try:
        result = await events.ingest_event(
            session,
            queue,
            user,
            event_type=body.type,
            payload=body.payload,
            idempotency_key=idempotency_key,
            max_payload_bytes=settings.max_event_payload_bytes,
        )
    except PayloadTooLargeError:
        raise HTTPException(413, "Event payload too large") from None
    except IdempotencyConflictError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Idempotency-Key was already used with a different request payload",
        ) from None
    if result.replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return _detail(result.event, result.deliveries)


@router.get("", response_model=list[EventOut])
async def list_events(
    user: AuthenticatedUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    type: Annotated[str | None, Query(max_length=64)] = None,
) -> list[EventOut]:
    rows = await events.list_events(session, user.id, limit=limit, offset=offset, event_type=type)
    return [EventOut.model_validate(row) for row in rows]


@router.get("/{event_id}", response_model=EventDetail)
async def get_event(
    event_id: uuid.UUID, user: AuthenticatedUser, session: SessionDep
) -> EventDetail:
    found = await events.get_event_with_deliveries(session, user.id, event_id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    return _detail(*found)


__all__ = ["router"]
