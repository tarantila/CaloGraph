"""Add resumable YAZIO historical synchronization state.

Revision ID: 20260812_0013
Revises: 20260811_0012
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260812_0013"
down_revision: str | None = "20260811_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("yazio_connections", "sync_interval_minutes", nullable=True)
    op.alter_column("yazio_connections", "sync_days", nullable=True)
    op.add_column("yazio_connections", sa.Column("initial_sync_state", sa.String(32), nullable=False, server_default="not_confirmed"))
    op.add_column("yazio_connections", sa.Column("historical_sync_kind", sa.String(16)))
    op.add_column("yazio_connections", sa.Column("historical_sync_state", sa.String(16), nullable=False, server_default="idle"))
    op.add_column("yazio_connections", sa.Column("historical_sync_start_date", sa.Date()))
    op.add_column("yazio_connections", sa.Column("historical_sync_end_date", sa.Date()))
    op.add_column("yazio_connections", sa.Column("historical_sync_cursor_date", sa.Date()))
    op.add_column("yazio_connections", sa.Column("historical_sync_started_at", sa.DateTime(timezone=True)))
    op.add_column("yazio_connections", sa.Column("historical_sync_completed_at", sa.DateTime(timezone=True)))
    op.add_column("yazio_connections", sa.Column("historical_sync_last_error", sa.String(500)))
    # v0.3.1's built-in values become inherited deployment defaults; custom values remain overrides.
    op.execute("UPDATE yazio_connections SET sync_interval_minutes = NULL WHERE sync_interval_minutes = 360")
    op.execute("UPDATE yazio_connections SET sync_days = NULL WHERE sync_days = 7")
    op.create_check_constraint("ck_yazio_initial_sync_state", "yazio_connections", "initial_sync_state IN ('not_confirmed', 'pending', 'running', 'completed', 'failed')")
    op.create_check_constraint("ck_yazio_historical_sync_state", "yazio_connections", "historical_sync_state IN ('idle', 'pending', 'running', 'completed', 'failed')")
    op.create_check_constraint("ck_yazio_historical_sync_kind", "yazio_connections", "historical_sync_kind IS NULL OR historical_sync_kind IN ('initial', 'full', 'range')")
    op.alter_column("yazio_connections", "initial_sync_state", server_default=None)
    op.alter_column("yazio_connections", "historical_sync_state", server_default=None)


def downgrade() -> None:
    op.drop_constraint("ck_yazio_historical_sync_kind", "yazio_connections", type_="check")
    op.drop_constraint("ck_yazio_historical_sync_state", "yazio_connections", type_="check")
    op.drop_constraint("ck_yazio_initial_sync_state", "yazio_connections", type_="check")
    op.execute("UPDATE yazio_connections SET sync_interval_minutes = 360 WHERE sync_interval_minutes IS NULL")
    op.execute("UPDATE yazio_connections SET sync_days = 7 WHERE sync_days IS NULL")
    op.alter_column("yazio_connections", "sync_days", nullable=False)
    op.alter_column("yazio_connections", "sync_interval_minutes", nullable=False)
    for name in ("historical_sync_last_error", "historical_sync_completed_at", "historical_sync_started_at", "historical_sync_cursor_date", "historical_sync_end_date", "historical_sync_start_date", "historical_sync_state", "historical_sync_kind", "initial_sync_state"):
        op.drop_column("yazio_connections", name)
