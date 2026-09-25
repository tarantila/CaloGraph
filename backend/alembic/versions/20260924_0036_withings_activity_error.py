"""Track the latest Withings activity-domain error independently.

Revision ID: 20260924_0036
Revises: 20260923_0035
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260924_0036"
down_revision: str | None = "20260923_0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "withings_connections",
        sa.Column("last_activity_error_category", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("withings_connections", "last_activity_error_category")
