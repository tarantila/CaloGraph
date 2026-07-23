"""Add administrators and one-time user invitations.

Revision ID: 20260723_0003
Revises: 20260723_0002
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260723_0003"
down_revision: str | None = "20260723_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(
        sa.text(
            "UPDATE users SET is_admin = true "
            "WHERE id = (SELECT id FROM users ORDER BY created_at, id LIMIT 1)"
        )
    )
    op.create_table(
        "user_invitations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("invited_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["invited_by_user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        "ix_user_invitations_token_hash",
        "user_invitations",
        ["token_hash"],
    )
    op.create_index(
        "ix_user_invitations_invited_by_user_id",
        "user_invitations",
        ["invited_by_user_id"],
    )
    op.create_index(
        "ix_user_invitations_expires_at",
        "user_invitations",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_table("user_invitations")
    op.drop_column("users", "is_admin")
