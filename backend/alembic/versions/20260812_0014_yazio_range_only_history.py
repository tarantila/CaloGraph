"""Restrict YAZIO history synchronization to explicit date ranges.

Revision ID: 20260812_0014
Revises: 20260812_0013
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260812_0014"
down_revision: str | None = "20260812_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # v0.3.2's initial and full-history jobs have no explicit user-selected
    # range. Neutralize only those legacy jobs rather than replaying them after
    # upgrade; previously queued explicit range jobs remain resumable.
    op.execute(
        """
        UPDATE yazio_connections
        SET historical_sync_state = 'idle',
            historical_sync_start_date = NULL,
            historical_sync_end_date = NULL,
            historical_sync_cursor_date = NULL,
            historical_sync_started_at = NULL,
            historical_sync_completed_at = NULL,
            historical_sync_last_error = NULL
        WHERE historical_sync_kind IN ('initial', 'full')
        """
    )
    op.drop_constraint(
        "ck_yazio_historical_sync_kind", "yazio_connections", type_="check"
    )
    op.drop_constraint(
        "ck_yazio_initial_sync_state", "yazio_connections", type_="check"
    )
    op.drop_column("yazio_connections", "historical_sync_kind")
    op.drop_column("yazio_connections", "initial_sync_state")


def downgrade() -> None:
    op.add_column(
        "yazio_connections",
        sa.Column(
            "initial_sync_state",
            sa.String(32),
            nullable=False,
            server_default="not_confirmed",
        ),
    )
    op.add_column("yazio_connections", sa.Column("historical_sync_kind", sa.String(16)))
    op.execute(
        """
        UPDATE yazio_connections
        SET historical_sync_kind = 'range'
        WHERE historical_sync_state IN ('pending', 'running', 'failed', 'completed')
        """
    )
    op.create_check_constraint(
        "ck_yazio_initial_sync_state",
        "yazio_connections",
        "initial_sync_state IN ('not_confirmed', 'pending', 'running', 'completed', 'failed')",
    )
    op.create_check_constraint(
        "ck_yazio_historical_sync_kind",
        "yazio_connections",
        "historical_sync_kind IS NULL OR historical_sync_kind IN ('initial', 'full', 'range')",
    )
    op.alter_column("yazio_connections", "initial_sync_state", server_default=None)
