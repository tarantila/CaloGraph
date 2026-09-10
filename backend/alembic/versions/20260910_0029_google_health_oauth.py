"""Create encrypted Google Health connection and OAuth flow tables.

Revision ID: 20260910_0029
Revises: 20260910_0028
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260910_0029"
down_revision: str | None = "20260910_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "google_health_connections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("encrypted_refresh_token", sa.LargeBinary(), nullable=False),
        sa.Column("granted_scopes", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("refresh_token_expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('active', 'reauth_required')",
            name="ck_google_health_connections_state",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_google_health_connections_user_id"),
    )
    op.create_index(
        "ix_google_health_connections_user_id",
        "google_health_connections",
        ["user_id"],
    )

    op.create_table(
        "google_health_oauth_flows",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("state_hash", sa.String(64), nullable=False),
        sa.Column("encrypted_pkce_verifier", sa.LargeBinary(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("state_hash", name="uq_google_health_oauth_flows_state_hash"),
    )
    op.create_index(
        "ix_google_health_oauth_flows_user_id",
        "google_health_oauth_flows",
        ["user_id"],
    )
    op.create_index(
        "ix_google_health_oauth_flows_state_hash",
        "google_health_oauth_flows",
        ["state_hash"],
    )
    op.create_index(
        "ix_google_health_oauth_flows_expires_at",
        "google_health_oauth_flows",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_google_health_oauth_flows_expires_at", table_name="google_health_oauth_flows")
    op.drop_index("ix_google_health_oauth_flows_state_hash", table_name="google_health_oauth_flows")
    op.drop_index("ix_google_health_oauth_flows_user_id", table_name="google_health_oauth_flows")
    op.drop_table("google_health_oauth_flows")
    op.drop_index("ix_google_health_connections_user_id", table_name="google_health_connections")
    op.drop_table("google_health_connections")
