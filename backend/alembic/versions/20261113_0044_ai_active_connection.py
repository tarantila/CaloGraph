"""Store the account's active AI provider connection.

Revision ID: 20261113_0044
Revises: 20261113_0043
"""

import sqlalchemy as sa

from alembic import op

revision = "20261113_0044"
down_revision = "20261113_0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_active_connections",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["user_id", "connection_id"], ["ai_provider_connections.user_id", "ai_provider_connections.id"],
            ondelete="CASCADE", name="fk_ai_active_connections_connection",
        ),
    )


def downgrade() -> None:
    op.drop_table("ai_active_connections")
