"""Create ordered user-scoped Analysis card preferences.

Revision ID: 20260927_0037
Revises: 20260924_0036
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260927_0037"
down_revision: str | None = "20260924_0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_analysis_preferences",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("analysis_key", sa.String(length=128), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(analysis_key) > 0",
            name="ck_user_analysis_preferences_key",
        ),
        sa.CheckConstraint(
            "position >= 0",
            name="ck_user_analysis_preferences_position",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "analysis_key"),
        sa.UniqueConstraint(
            "user_id",
            "position",
            name="uq_user_analysis_preferences_position",
        ),
    )


def downgrade() -> None:
    op.drop_table("user_analysis_preferences")
