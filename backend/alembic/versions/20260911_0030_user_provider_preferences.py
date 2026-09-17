"""Create user-scoped provider preferences.

Revision ID: 20260911_0030
Revises: 20260910_0029
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260911_0030"
down_revision: str | None = "20260910_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_provider_preferences",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("data_area", sa.String(length=64), nullable=False),
        sa.Column("provider_key", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(data_area) > 0",
            name="ck_provider_preferences_data_area",
        ),
        sa.CheckConstraint(
            "length(provider_key) > 0",
            name="ck_provider_preferences_provider_key",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "data_area"),
    )


def downgrade() -> None:
    op.drop_table("user_provider_preferences")
