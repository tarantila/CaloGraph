"""Persist bounded idempotent provider connection checks.

Revision ID: 20261113_0043
Revises: 20261113_0042
"""

import sqlalchemy as sa

from alembic import op

revision = "20261113_0043"
down_revision = "20261113_0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_provider_checks",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("configuration_version", sa.Integer(), nullable=False),
        sa.Column("privacy_version", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("user_id", "id"),
        sa.ForeignKeyConstraint(
            ["user_id", "connection_id"], ["ai_provider_connections.user_id", "ai_provider_connections.id"],
            ondelete="CASCADE", name="fk_ai_provider_checks_connection",
        ),
        sa.CheckConstraint("configuration_version >= 1", name="ck_ai_provider_checks_version"),
    )
    op.create_index("ix_ai_provider_checks_owner_connection", "ai_provider_checks", ["user_id", "connection_id", "started_at"])


def downgrade() -> None:
    op.drop_table("ai_provider_checks")
