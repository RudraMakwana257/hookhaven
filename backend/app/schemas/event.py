import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

EVENT_TYPE_PATTERN = r"^[a-z0-9]+([._-][a-z0-9]+)*$"


class EventCreate(BaseModel):
    type: str = Field(min_length=1, max_length=64, pattern=EVENT_TYPE_PATTERN)
    payload: dict[str, Any]


class DeliveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    endpoint_id: uuid.UUID
    status: str
    attempt_count: int
    next_attempt_at: datetime | None
    last_status_code: int | None
    last_error: str | None
    delivered_at: datetime | None
    created_at: datetime
    updated_at: datetime


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: str
    idempotency_key: str | None
    created_at: datetime


class EventDetail(EventOut):
    payload: dict[str, Any]
    deliveries: list[DeliveryOut]
