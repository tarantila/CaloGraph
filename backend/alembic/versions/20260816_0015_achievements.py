"""Add user achievements.

Revision ID: 20260816_0015
Revises: 20260812_0014
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260816_0015"
down_revision: str | None = "20260812_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_achievements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("achievement_key", sa.String(length=64), nullable=False),
        sa.Column("unlocked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "achievement_key", name="uq_user_achievement_key"),
    )
    op.create_index("ix_user_achievements_user_id", "user_achievements", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_achievements_user_id", table_name="user_achievements")
    op.drop_table("user_achievements")
