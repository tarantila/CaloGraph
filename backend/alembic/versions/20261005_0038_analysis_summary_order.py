"""Add persistent user-scoped Analysis Summary order.

Revision ID: 20261005_0038
Revises: 20260927_0037
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261005_0038"
down_revision: str | None = "20260927_0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "summary_order",
            sa.JSON(),
            nullable=False,
            server_default=sa.text(
                "'[\"weight\", \"nutrition\", \"micronutrients\", \"activity\"]'"
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "summary_order")
