"""Retain permission invalidation independently of provider connections.

Revision ID: 20261123_0056
Revises: 20261114_0046
"""

import sqlalchemy as sa

from alembic import op

revision = "20261123_0056"
down_revision = "20261114_0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_connection_retirements",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "connection_id"),
    )
    op.create_table(
        "ai_consent_revocations",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("consent_id", sa.Uuid(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "consent_id"),
    )
    op.execute(sa.text(
        "INSERT INTO ai_consent_revocations (user_id, consent_id, revoked_at) "
        "SELECT user_id, id, revoked_at FROM ai_consents WHERE revoked_at IS NOT NULL"
    ))


def downgrade() -> None:
    count = op.get_bind().scalar(sa.text("SELECT count(*) FROM ai_consent_revocations"))
    retired = op.get_bind().scalar(sa.text("SELECT count(*) FROM ai_connection_retirements"))
    if count or retired:
        raise RuntimeError(
            "Cannot downgrade while retained consent revocations or connection retirements exist; "
            "removing them could restore invalidated permissions. No schema changes were made."
        )
    op.drop_table("ai_consent_revocations")
    op.drop_table("ai_connection_retirements")
