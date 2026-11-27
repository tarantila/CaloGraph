"""Persist encrypted, owner-bound ChatGPT subscription credentials.

Revision ID: 20261127_0065
Revises: 20261125_0064
"""

import sqlalchemy as sa

from alembic import op

revision = "20261127_0065"
down_revision = "20261125_0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_subscription_credentials",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("credential_version", sa.Integer(), nullable=False),
        sa.Column("encrypted_tokens", sa.LargeBinary(), nullable=True),
        sa.Column("encrypted_binding", sa.LargeBinary(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "connection_id", name="pk_ai_subscription_credentials"),
        sa.ForeignKeyConstraint(
            ["user_id", "connection_id"],
            ["ai_provider_connections.user_id", "ai_provider_connections.id"],
            ondelete="CASCADE", name="fk_ai_subscription_credentials_connection",
        ),
        sa.CheckConstraint(
            "state IN ('ready', 'refresh_claimed', 'refresh_unconfirmed', 'reauth_required', "
            "'revocation_pending', 'revocation_unconfirmed', 'disconnected')",
            name="ck_ai_subscription_credentials_state",
        ),
        sa.CheckConstraint("credential_version >= 1", name="ck_ai_subscription_credentials_version"),
        sa.CheckConstraint("length(encrypted_binding) > 0", name="ck_ai_subscription_credentials_binding"),
        sa.CheckConstraint(
            "(state = 'ready' AND encrypted_tokens IS NOT NULL AND expires_at IS NOT NULL) OR "
            "(state <> 'ready' AND encrypted_tokens IS NULL)",
            name="ck_ai_subscription_credentials_token_state",
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT 1 FROM ai_subscription_credentials LIMIT 1")) is not None:
        raise RuntimeError("Refusing to remove persisted subscription credential state.")
    op.drop_table("ai_subscription_credentials")
