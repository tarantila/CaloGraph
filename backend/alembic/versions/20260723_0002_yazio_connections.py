"""Add encrypted per-user YAZIO connections.

Revision ID: 20260723_0002
Revises: 20260719_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260723_0002"
down_revision: str | None = "20260719_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "yazio_connections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("encrypted_email", sa.LargeBinary(), nullable=False),
        sa.Column("encrypted_password", sa.LargeBinary(), nullable=False),
        sa.Column("account_hash", sa.String(64), nullable=False),
        sa.Column("source_identifier", sa.String(255), nullable=False),
        sa.Column("sync_enabled", sa.Boolean(), nullable=False),
        sa.Column("sync_interval_minutes", sa.Integer(), nullable=False),
        sa.Column("sync_days", sa.Integer(), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("next_sync_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "sync_interval_minutes BETWEEN 60 AND 10080",
            name="ck_yazio_sync_interval",
        ),
        sa.CheckConstraint("sync_days BETWEEN 1 AND 366", name="ck_yazio_sync_days"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_yazio_connections_user_id", "yazio_connections", ["user_id"])
    op.create_index(
        "ix_yazio_connections_next_sync_at",
        "yazio_connections",
        ["next_sync_at"],
    )


def downgrade() -> None:
    op.drop_table("yazio_connections")
