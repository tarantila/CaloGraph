"""Add account-local Nutrition Expert data-category preferences.

Revision ID: 20261114_0047
Revises: 20261114_0046
"""

import sqlalchemy as sa

from alembic import op

revision = "20261114_0047"
down_revision = "20261114_0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "expert_preferences",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("data_domains", sa.JSON(), nullable=False),
        sa.Column("include_meal_names", sa.Boolean(), nullable=False),
        sa.Column("configuration_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("configuration_version >= 1", name="ck_expert_preferences_version"),
        sa.CheckConstraint(
            "json_array_length(data_domains) BETWEEN 1 AND 6",
            name="ck_expert_preferences_domains_count",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("expert_preferences")
