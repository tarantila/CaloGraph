"""Add explicit user deactivation state.

Revision ID: 20260810_0010
Revises: 20260803_0009
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260810_0010"
down_revision: str | None = "20260803_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT_NAME = "ck_users_active_deactivation_state"


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE users
            SET deactivated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)
            WHERE is_active = false
              AND deactivated_at IS NULL
            """
        )
    )
    op.create_check_constraint(
        CONSTRAINT_NAME,
        "users",
        "(is_active AND deactivated_at IS NULL) "
        "OR (NOT is_active AND deactivated_at IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT_NAME, "users", type_="check")
    op.drop_column("users", "deactivated_at")
