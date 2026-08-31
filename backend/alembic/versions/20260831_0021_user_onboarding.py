"""Add per-user onboarding state without backfilling legacy users.

Revision ID: 20260831_0021
Revises: 20260830_0020
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260831_0021"
down_revision: str | None = "20260830_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_onboarding",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "current_step",
            sa.String(length=16),
            nullable=False,
            server_default="personal",
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
        sa.CheckConstraint(
            "current_step IN ('personal', 'targets', 'security', 'completed')",
            name="ck_user_onboarding_current_step",
        ),
    )


def downgrade() -> None:
    op.drop_table("user_onboarding")
