"""Add immutable, connection-scoped Nutrition Expert consents.

Revision ID: 20261114_0046
Revises: 20261113_0045
"""

import sqlalchemy as sa

from alembic import op

revision = "20261114_0046"
down_revision = "20261113_0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_consents",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("configuration_version", sa.Integer(), nullable=False),
        sa.Column("provider_kind", sa.String(length=32), nullable=False),
        sa.Column("model_id", sa.String(length=200), nullable=False),
        sa.Column("base_url", sa.String(length=2048), nullable=True),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("data_domains", sa.JSON(), nullable=False),
        sa.Column("include_meal_names", sa.Boolean(), nullable=False),
        sa.Column("history_mode", sa.String(length=24), nullable=False),
        sa.Column("history_start", sa.Date(), nullable=True),
        sa.Column("history_end", sa.Date(), nullable=True),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("configuration_version >= 1", name="ck_ai_consents_configuration_version"),
        sa.CheckConstraint(
            "provider_kind IN ('openai_api', 'anthropic', 'gemini', 'local', 'gateway')",
            name="ck_ai_consents_provider_kind",
        ),
        sa.CheckConstraint("length(trim(model_id)) > 0", name="ck_ai_consents_model_id"),
        sa.CheckConstraint("length(policy_version) > 0", name="ck_ai_consents_policy_version"),
        sa.CheckConstraint(
            "purpose IN ('nutrition_chat', 'nutrition_check_in')", name="ck_ai_consents_purpose",
        ),
        sa.CheckConstraint(
            "(history_mode = 'all_available' AND history_start IS NULL AND history_end IS NULL) OR "
            "(history_mode = 'date_range' AND history_start IS NOT NULL AND history_end IS NOT NULL "
            "AND history_start <= history_end)",
            name="ck_ai_consents_history_scope",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["user_id", "connection_id"],
            ["ai_provider_connections.user_id", "ai_provider_connections.id"],
            ondelete="CASCADE", name="fk_ai_consents_connection",
        ),
        sa.PrimaryKeyConstraint("user_id", "id"),
    )
    op.create_index(
        "ix_ai_consents_owner_connection", "ai_consents", ["user_id", "connection_id", "id"],
    )


def downgrade() -> None:
    op.drop_table("ai_consents")
