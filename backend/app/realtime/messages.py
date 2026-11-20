"""Shapes of the messages pushed to the dashboard. Kept small: they are hints, not a data API."""

from collections.abc import Sequence
from typing import Any

from app.models import Delivery, Event


def _iso(value: Any) -> str | None:
    return None if value is None else value.isoformat()


def event_created(event: Event, deliveries: Sequence[Delivery]) -> dict[str, Any]:
    return {
        "type": "event.created",
        "event_id": str(event.id),
        "event_type": event.type,
        "created_at": _iso(event.created_at),
        "delivery_ids": [str(d.id) for d in deliveries],
    }


def delivery_updated(
    *,
    delivery_id: object,
    event_id: object,
    endpoint_id: object,
    status: str,
    attempt_count: int,
    next_attempt_at: Any = None,
    last_status_code: int | None = None,
    last_error: str | None = None,
    attempt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "type": "delivery.updated",
        "delivery_id": str(delivery_id),
        "event_id": str(event_id),
        "endpoint_id": str(endpoint_id),
        "status": status,
        "attempt_count": attempt_count,
        "next_attempt_at": _iso(next_attempt_at),
        "last_status_code": last_status_code,
        "last_error": last_error,
        "attempt": attempt,
    }


def endpoint_disabled(endpoint_id: object, reason: str) -> dict[str, Any]:
    return {"type": "endpoint.disabled", "endpoint_id": str(endpoint_id), "reason": reason}


def deliveries_replayed(endpoint_id: object, count: int) -> dict[str, Any]:
    return {"type": "deliveries.replayed", "endpoint_id": str(endpoint_id), "count": count}
