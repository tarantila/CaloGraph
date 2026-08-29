"""Add optional personal user profiles.

Revision ID: 20260829_0019
Revises: 20260826_0018
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260829_0019"
down_revision: str | None = "20260826_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_profiles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=True),
        sa.Column("gender", sa.String(length=32), nullable=True),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("height_cm", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("diet_type", sa.String(length=32), nullable=True),
        sa.Column("health_notes", sa.String(length=4000), nullable=True),
        sa.Column("intolerances", sa.String(length=2000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "gender IS NULL OR gender IN "
            "('female', 'male', 'non_binary', 'other', 'prefer_not_to_say')",
            name="ck_user_profiles_gender",
        ),
        sa.CheckConstraint(
            "height_cm IS NULL OR (height_cm > 0 AND height_cm <= 300)",
            name="ck_user_profiles_height_cm",
        ),
        sa.CheckConstraint(
            "diet_type IS NULL OR diet_type IN "
            "('no_special_diet', 'vegetarian', 'vegan', 'pescetarian', 'other', "
            "'prefer_not_to_say')",
            name="ck_user_profiles_diet_type",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("user_profiles")
