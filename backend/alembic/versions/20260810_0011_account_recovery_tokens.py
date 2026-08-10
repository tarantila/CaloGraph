"""Add one-time account recovery tokens.

Revision ID: 20260810_0011
Revises: 20260810_0010
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260810_0011"
down_revision: str | None = "20260810_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "account_recovery_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        "ix_account_recovery_tokens_user_id",
        "account_recovery_tokens",
        ["user_id"],
    )
    op.create_index(
        "ix_account_recovery_tokens_expires_at",
        "account_recovery_tokens",
        ["expires_at"],
    )
    op.create_index(
        "ix_account_recovery_tokens_user_open",
        "account_recovery_tokens",
        ["user_id", "used_at", "revoked_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_account_recovery_tokens_user_open",
        table_name="account_recovery_tokens",
    )
    op.drop_index(
        "ix_account_recovery_tokens_expires_at",
        table_name="account_recovery_tokens",
    )
    op.drop_index(
        "ix_account_recovery_tokens_user_id",
        table_name="account_recovery_tokens",
    )
    op.drop_table("account_recovery_tokens")
