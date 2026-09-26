"""Add per-user Withings credentials and bind OAuth flows to connections.

Revision ID: 20260923_0035
Revises: 20260922_0034
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260923_0035"
down_revision: str | None = "20260922_0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "withings_connections",
        sa.Column("client_id", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "withings_connections",
        sa.Column("encrypted_client_secret", sa.LargeBinary(), nullable=True),
    )
    op.add_column(
        "withings_oauth_flows",
        sa.Column("connection_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_withings_oauth_flows_connection_id_withings_connections",
        "withings_oauth_flows",
        "withings_connections",
        ["connection_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_withings_oauth_flows_connection_id",
        "withings_oauth_flows",
        ["connection_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_withings_oauth_flows_connection_id",
        table_name="withings_oauth_flows",
    )
    op.drop_constraint(
        "fk_withings_oauth_flows_connection_id_withings_connections",
        "withings_oauth_flows",
        type_="foreignkey",
    )
    op.drop_column("withings_oauth_flows", "connection_id")
    op.drop_column("withings_connections", "encrypted_client_secret")
    op.drop_column("withings_connections", "client_id")
