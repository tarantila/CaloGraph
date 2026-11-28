"""Persist encrypted subscription device authorization attempts and receipts.

Revision ID: 20261128_0066
Revises: 20261127_0065
"""

import sqlalchemy as sa

from alembic import op

revision = "20261128_0066"
down_revision = "20261127_0065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_subscription_device_authorizations",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("configuration_version", sa.Integer(), nullable=False),
        sa.Column("credential_generation", sa.Integer(), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("safe_error_code", sa.String(length=64), nullable=True),
        sa.Column("oauth_status", sa.Integer(), nullable=True),
        sa.Column("encrypted_device", sa.LargeBinary(), nullable=True),
        sa.Column("poll_interval_seconds", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("next_poll_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "id", name="pk_ai_subscription_device_authorizations"),
        sa.ForeignKeyConstraint(
            ["user_id", "connection_id"],
            ["ai_provider_connections.user_id", "ai_provider_connections.id"],
            ondelete="CASCADE", name="fk_ai_subscription_device_authorizations_connection",
        ),
        sa.CheckConstraint(
            "state IN ('starting', 'pending', 'polling', 'exchanging', 'completed', "
            "'denied', 'failed', 'cancelled', 'expired', 'interrupted')",
            name="ck_ai_subscription_device_authorizations_state",
        ),
        sa.CheckConstraint("length(request_hash) = 64", name="ck_ai_subscription_device_authorizations_hash"),
        sa.CheckConstraint(
            "configuration_version >= 1 AND credential_generation >= 0",
            name="ck_ai_subscription_device_authorizations_snapshot",
        ),
        sa.CheckConstraint(
            "poll_interval_seconds BETWEEN 1 AND 60",
            name="ck_ai_subscription_device_authorizations_interval",
        ),
    )
    op.create_index(
        "uq_ai_subscription_device_authorizations_active_connection",
        "ai_subscription_device_authorizations", ["user_id", "connection_id"], unique=True,
        postgresql_where=sa.text("state IN ('starting', 'pending', 'polling', 'exchanging')"),
        sqlite_where=sa.text("state IN ('starting', 'pending', 'polling', 'exchanging')"),
    )
    op.create_index(
        "ix_ai_subscription_device_authorizations_owner_created",
        "ai_subscription_device_authorizations", ["user_id", "created_at"],
    )
    op.create_table(
        "ai_subscription_device_authorization_receipts",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("authorization_id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "authorization_id", name="pk_ai_subscription_device_authorization_receipts"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "length(request_hash) = 64",
            name="ck_ai_subscription_device_authorization_receipts_hash",
        ),
    )
    op.create_index(
        "ix_ai_subscription_device_authorization_receipts_owner_created",
        "ai_subscription_device_authorization_receipts", ["user_id", "created_at"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    for table in (
        "ai_subscription_device_authorizations",
        "ai_subscription_device_authorization_receipts",
    ):
        if bind.scalar(sa.text(f"SELECT 1 FROM {table} LIMIT 1")) is not None:
            raise RuntimeError("Refusing to remove retained subscription device authorization state.")
    op.drop_index(
        "ix_ai_subscription_device_authorization_receipts_owner_created",
        table_name="ai_subscription_device_authorization_receipts",
    )
    op.drop_table("ai_subscription_device_authorization_receipts")
    op.drop_index(
        "ix_ai_subscription_device_authorizations_owner_created",
        table_name="ai_subscription_device_authorizations",
    )
    op.drop_index(
        "uq_ai_subscription_device_authorizations_active_connection",
        table_name="ai_subscription_device_authorizations",
    )
    op.drop_table("ai_subscription_device_authorizations")
