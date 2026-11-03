import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import JsonType


class Event(Base):
    """An event accepted from a user's system, to be delivered to matching endpoints."""

    __tablename__ = "events"
    __table_args__ = (
        # NULL keys never collide, so the constraint only bites when a key is supplied.
        UniqueConstraint("user_id", "idempotency_key", name="uq_events_user_id_idempotency_key"),
        Index("ix_events_user_id_created_at", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType)
    # SHA-256 of the canonical (type, payload): detects an idempotency key reused for a
    # *different* request.
    payload_hash: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
