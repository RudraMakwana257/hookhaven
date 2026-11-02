import re
import uuid
from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_EVENT_TYPE_RE = re.compile(r"(\*|[a-z0-9]+([._-][a-z0-9]+)*)")


def normalize_event_types(values: list[str]) -> list[str]:
    for value in values:
        if len(value) > 64 or not _EVENT_TYPE_RE.fullmatch(value):
            raise ValueError(f"invalid event type {value!r}: use lowercase like 'order.created'")
    if "*" in values:
        return ["*"]  # wildcard subsumes everything else
    return list(dict.fromkeys(values))  # de-duplicate, keep order


class EndpointCreate(BaseModel):
    url: str = Field(max_length=2048)
    description: str | None = Field(default=None, max_length=200)
    event_types: list[str] = Field(default_factory=lambda: ["*"], min_length=1, max_length=20)

    @field_validator("event_types")
    @classmethod
    def _normalize(cls, value: list[str]) -> list[str]:
        return normalize_event_types(value)


class EndpointUpdate(BaseModel):
    """PATCH semantics: only fields present in the request body change."""

    url: str | None = Field(default=None, max_length=2048)
    description: str | None = Field(default=None, max_length=200)
    event_types: list[str] | None = Field(default=None, min_length=1, max_length=20)
    is_active: bool | None = None

    @field_validator("event_types")
    @classmethod
    def _normalize(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else normalize_event_types(value)

    @model_validator(mode="after")
    def _required_fields_not_null(self) -> Self:
        for name in ("url", "event_types", "is_active"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class EndpointOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    url: str
    description: str | None
    event_types: list[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime


class EndpointWithSecret(EndpointOut):
    """Returned once on creation and on rotation. The secret is not retrievable afterwards."""

    secret: str
