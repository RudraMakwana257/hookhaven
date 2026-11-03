"""events and deliveries

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("payload", JSON_TYPE, nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_events_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_events"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_events_user_id_idempotency_key"),
    )
    op.create_index("ix_events_user_id_created_at", "events", ["user_id", "created_at"])

    op.create_table(
        "deliveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("endpoint_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status_code", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'succeeded', 'dead')", name=op.f("ck_deliveries_status_valid")
        ),
        sa.ForeignKeyConstraint(
            ["event_id"], ["events.id"], name="fk_deliveries_event_id_events", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["endpoint_id"],
            ["endpoints.id"],
            name="fk_deliveries_endpoint_id_endpoints",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_deliveries"),
        sa.UniqueConstraint("event_id", "endpoint_id", name="uq_deliveries_event_id_endpoint_id"),
    )
    op.create_index("ix_deliveries_endpoint_id", "deliveries", ["endpoint_id"])
    op.create_index(
        "ix_deliveries_status_next_attempt_at", "deliveries", ["status", "next_attempt_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_deliveries_status_next_attempt_at", table_name="deliveries")
    op.drop_index("ix_deliveries_endpoint_id", table_name="deliveries")
    op.drop_table("deliveries")
    op.drop_index("ix_events_user_id_created_at", table_name="events")
    op.drop_table("events")
