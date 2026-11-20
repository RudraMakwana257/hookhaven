import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status

from app.api.deps import (
    AuthenticatedUser,
    BusDep,
    QueueDep,
    SessionDep,
    SettingsDep,
    enforce_content_length,
)
from app.api.limits import limit_api, limit_ingest
from app.core.errors import IdempotencyConflictError, PayloadTooLargeError
from app.core.metrics import EVENTS_INGESTED
from app.models import Delivery, Event
from app.realtime.bus import publish_best_effort
from app.realtime.messages import event_created
from app.schemas.event import DeliveryOut, EventCreate, EventDetail, EventListItem, EventOut
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
    dependencies=[Depends(enforce_content_length), Depends(limit_ingest)],
)
async def ingest_event(
    body: EventCreate,
    response: Response,
    user: AuthenticatedUser,
    session: SessionDep,
    queue: QueueDep,
    bus: BusDep,
    settings: SettingsDep,
    idempotency_key: IdempotencyKey = None,
) -> EventDetail:
    """Accept an event for asynchronous delivery. Returns 202 once it is durably stored."""
    user_id = user.id  # captured early: a lost idempotency race rolls back and expires `user`
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
    EVENTS_INGESTED.labels("replayed" if result.replayed else "accepted").inc()
    if result.replayed:
        response.headers["Idempotent-Replayed"] = "true"
    else:
        await publish_best_effort(bus, user_id, event_created(result.event, result.deliveries))
    return _detail(result.event, result.deliveries)


@router.get("", response_model=list[EventListItem], dependencies=[Depends(limit_api)])
async def list_events(
    user: AuthenticatedUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    type: Annotated[str | None, Query(max_length=64)] = None,
) -> list[EventListItem]:
    rows = await events.list_events(session, user.id, limit=limit, offset=offset, event_type=type)
    counts = await events.delivery_counts_for(session, [row.id for row in rows])
    return [
        EventListItem(
            **EventOut.model_validate(row).model_dump(),
            delivery_counts={
                s: counts.get(row.id, {}).get(s, 0) for s in ("pending", "succeeded", "dead")
            },
        )
        for row in rows
    ]


@router.get("/{event_id}", response_model=EventDetail, dependencies=[Depends(limit_api)])
async def get_event(
    event_id: uuid.UUID, user: AuthenticatedUser, session: SessionDep
) -> EventDetail:
    found = await events.get_event_with_deliveries(session, user.id, event_id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    return _detail(*found)


__all__ = ["router"]
