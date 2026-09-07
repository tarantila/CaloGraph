"""Track the local date of the daily YAZIO sync trigger.

Revision ID: 20260906_0024
Revises: 20260904_0023
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260906_0024"
down_revision: str | None = "20260904_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "yazio_connections",
        sa.Column("last_daily_sync_trigger_date", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("yazio_connections", "last_daily_sync_trigger_date")
