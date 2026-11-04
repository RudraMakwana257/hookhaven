import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DeliveryStatus(StrEnum):
    PENDING = "pending"  # waiting for its first attempt, or for a retry (see next_attempt_at)
    SUCCEEDED = "succeeded"
    DEAD = "dead"  # retries exhausted: sits in the DLQ until manually replayed


class Delivery(Base):
    """One event going to one endpoint. The unit of retry, success and replay."""

    __tablename__ = "deliveries"
    __table_args__ = (
        UniqueConstraint("event_id", "endpoint_id", name="uq_deliveries_event_id_endpoint_id"),
        # Lets the reconciler find due work: WHERE status = 'pending' AND next_attempt_at <= now
        Index("ix_deliveries_status_next_attempt_at", "status", "next_attempt_at"),
        CheckConstraint("status IN ('pending', 'succeeded', 'dead')", name="status_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"))
    endpoint_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("endpoints.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(16), default=DeliveryStatus.PENDING.value)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # attempt_count when the current retry cycle began (0, or the count at the last replay).
    # The retry budget applies to attempts *within* a cycle, so a replay gets a fresh budget.
    cycle_start_attempt: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
