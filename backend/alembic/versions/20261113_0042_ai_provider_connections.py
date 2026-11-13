"""Add user-scoped AI provider connection configuration and credentials.

Revision ID: 20261113_0042
Revises: 20261026_0041
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261113_0042"
down_revision: str | None = "20261026_0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_provider_connections",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("provider_kind", sa.String(length=32), nullable=False),
        sa.Column("model_id", sa.String(length=200), nullable=False),
        sa.Column("base_url", sa.String(length=2048), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "timeout_seconds", sa.Integer(), server_default=sa.text("60"), nullable=False
        ),
        sa.Column(
            "configuration_version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(trim(label)) > 0", name="ck_ai_provider_connections_label"),
        sa.CheckConstraint(
            "provider_kind IN ("
            "'openai_api', 'chatgpt_plan', 'anthropic', 'gemini', 'local', 'gateway')",
            name="ck_ai_provider_connections_provider_kind",
        ),
        sa.CheckConstraint(
            "length(trim(model_id)) > 0",
            name="ck_ai_provider_connections_model_id",
        ),
        sa.CheckConstraint(
            "(provider_kind IN ('local', 'gateway') AND base_url IS NOT NULL "
            "AND length(trim(base_url)) > 0) OR "
            "(provider_kind NOT IN ('local', 'gateway') AND base_url IS NULL)",
            name="ck_ai_provider_connections_base_url",
        ),
        sa.CheckConstraint(
            "timeout_seconds >= 5 AND timeout_seconds <= 300",
            name="ck_ai_provider_connections_timeout",
        ),
        sa.CheckConstraint(
            "configuration_version >= 1",
            name="ck_ai_provider_connections_configuration_version",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "id"),
    )

    op.create_table(
        "ai_provider_credentials",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("encrypted_api_key", sa.LargeBinary(), nullable=False),
        sa.CheckConstraint(
            "length(encrypted_api_key) > 0",
            name="ck_ai_provider_credentials_encrypted_api_key",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "connection_id"],
            ["ai_provider_connections.user_id", "ai_provider_connections.id"],
            name="fk_ai_provider_credentials_connection",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "connection_id"),
    )


def downgrade() -> None:
    op.drop_table("ai_provider_credentials")
    op.drop_table("ai_provider_connections")
