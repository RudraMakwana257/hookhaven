import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text, Uuid, func, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

JsonList = JSON().with_variant(JSONB(), "postgresql")


class Endpoint(Base):
    """A destination registered by a user to receive webhook events."""

    __tablename__ = "endpoints"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(String(2048))
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Fernet-encrypted: we need the plaintext to sign requests, so it cannot be hashed.
    secret_encrypted: Mapped[str] = mapped_column(Text)
    event_types: Mapped[list[str]] = mapped_column(JsonList, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
