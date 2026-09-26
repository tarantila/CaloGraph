"""Create encrypted Withings connection and OAuth flow tables.

Revision ID: 20260922_0034
Revises: 20260920_0033
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260922_0034"
down_revision: str | None = "20260920_0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "withings_connections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("withings_user_id", sa.String(length=64), nullable=True),
        sa.Column("encrypted_access_token", sa.LargeBinary(), nullable=True),
        sa.Column("encrypted_refresh_token", sa.LargeBinary(), nullable=True),
        sa.Column("granted_scopes", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("access_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_state", sa.String(length=16), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_category", sa.String(length=32), nullable=True),
        sa.Column("last_error", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('active', 'reauth_required', 'not_connected')",
            name="ck_withings_connections_state",
        ),
        sa.CheckConstraint(
            "sync_state IN ('idle', 'running', 'completed', 'failed')",
            name="ck_withings_connections_sync_state",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_withings_connections_user_id"),
    )
    op.create_index(
        "ix_withings_connections_user_id",
        "withings_connections",
        ["user_id"],
    )

    op.create_table(
        "withings_oauth_flows",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("state_hash", name="uq_withings_oauth_flows_state_hash"),
    )
    op.create_index(
        "ix_withings_oauth_flows_user_id",
        "withings_oauth_flows",
        ["user_id"],
    )
    op.create_index(
        "ix_withings_oauth_flows_state_hash",
        "withings_oauth_flows",
        ["state_hash"],
    )
    op.create_index(
        "ix_withings_oauth_flows_expires_at",
        "withings_oauth_flows",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_withings_oauth_flows_expires_at", table_name="withings_oauth_flows")
    op.drop_index("ix_withings_oauth_flows_state_hash", table_name="withings_oauth_flows")
    op.drop_index("ix_withings_oauth_flows_user_id", table_name="withings_oauth_flows")
    op.drop_table("withings_oauth_flows")
    op.drop_index("ix_withings_connections_user_id", table_name="withings_connections")
    op.drop_table("withings_connections")
