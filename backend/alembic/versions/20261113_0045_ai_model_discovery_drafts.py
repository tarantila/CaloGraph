"""Allow unselected models while discovering provider models.

Revision ID: 20261113_0045
Revises: 20261113_0044
"""

import sqlalchemy as sa

from alembic import op

revision = "20261113_0045"
down_revision = "20261113_0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("ai_provider_connections", "model_id", existing_type=sa.String(length=200), nullable=True)
    op.create_check_constraint(
        "ck_ai_provider_connections_enabled_model",
        "ai_provider_connections",
        "NOT enabled OR model_id IS NOT NULL",
    )


def downgrade() -> None:
    bind = op.get_bind()
    drafts = bind.scalar(sa.text("SELECT count(*) FROM ai_provider_connections WHERE model_id IS NULL"))
    if drafts:
        raise RuntimeError(
            "Cannot downgrade AI model discovery while model-less provider drafts exist; "
            "choose a model or remove those drafts first. No schema changes were made."
        )
    op.drop_constraint("ck_ai_provider_connections_enabled_model", "ai_provider_connections", type_="check")
    op.alter_column("ai_provider_connections", "model_id", existing_type=sa.String(length=200), nullable=False)
