"""Add authenticated usage days.

Revision ID: 20260826_0018
Revises: 20260824_0017
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260826_0018"
down_revision: str | None = "20260824_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_usage_days",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("activity_date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "activity_date", name="uq_user_usage_day"),
    )
    op.create_index("ix_user_usage_days_user_id", "user_usage_days", ["user_id"])
    op.create_index("ix_user_usage_days_activity_date", "user_usage_days", ["activity_date"])


def downgrade() -> None:
    op.drop_index("ix_user_usage_days_activity_date", table_name="user_usage_days")
    op.drop_index("ix_user_usage_days_user_id", table_name="user_usage_days")
    op.drop_table("user_usage_days")
