"""replay cycles and endpoint auto-disable

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("endpoints") as batch:
        batch.add_column(
            sa.Column("consecutive_failures", sa.Integer(), server_default="0", nullable=False)
        )
        batch.add_column(sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("disabled_reason", sa.String(length=200), nullable=True))
    with op.batch_alter_table("deliveries") as batch:
        batch.add_column(
            sa.Column("cycle_start_attempt", sa.Integer(), server_default="0", nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("deliveries") as batch:
        batch.drop_column("cycle_start_attempt")
    with op.batch_alter_table("endpoints") as batch:
        batch.drop_column("disabled_reason")
        batch.drop_column("disabled_at")
        batch.drop_column("consecutive_failures")
