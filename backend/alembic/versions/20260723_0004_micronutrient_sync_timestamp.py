"""Track the less frequent YAZIO micronutrient synchronization.

Revision ID: 20260723_0004
Revises: 20260723_0003
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260723_0004"
down_revision: str | None = "20260723_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "yazio_connections",
        sa.Column(
            "last_micronutrient_sync_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("yazio_connections", "last_micronutrient_sync_at")
