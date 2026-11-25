"""Persist account-local provider order and activation.

Revision ID: 20261125_0063
Revises: 20261125_0062
"""

import sqlalchemy as sa

from alembic import op

revision = "20261125_0063"
down_revision = "20261125_0062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_routing_configurations",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("configuration_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id", name="pk_ai_routing_configurations"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE", name="fk_ai_routing_configurations_user_id_users"),
        sa.CheckConstraint("configuration_version >= 1", name="ck_ai_routing_configurations_version"),
    )
    op.create_table(
        "ai_routing_entries",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "connection_id", name="pk_ai_routing_entries"),
        sa.ForeignKeyConstraint(["user_id"], ["ai_routing_configurations.user_id"], ondelete="CASCADE", name="fk_ai_routing_entries_configuration"),
        sa.ForeignKeyConstraint(["user_id", "connection_id"], ["ai_provider_connections.user_id", "ai_provider_connections.id"], ondelete="CASCADE", name="fk_ai_routing_entries_connection"),
        sa.CheckConstraint("position >= 0 AND position < 20", name="ck_ai_routing_entries_position"),
        sa.UniqueConstraint("user_id", "position", name="uq_ai_routing_entries_owner_position"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT 1 FROM ai_routing_configurations LIMIT 1")) is not None:
        raise RuntimeError("Refusing to drop persisted provider routing configuration.")
    op.drop_table("ai_routing_entries")
    op.drop_table("ai_routing_configurations")
