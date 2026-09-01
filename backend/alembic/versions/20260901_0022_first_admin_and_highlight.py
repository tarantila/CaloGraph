"""Add first-run state and dashboard preference.

Revision ID: 20260901_0022
Revises: 20260831_0021
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260901_0022"
down_revision: str | None = "20260831_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("highlight_over_budget", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "instance_bootstrap",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("initialized", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("initialized_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(sa.text("INSERT INTO instance_bootstrap (id, initialized) SELECT 1, true WHERE EXISTS (SELECT 1 FROM users)"))


def downgrade() -> None:
    op.drop_table("instance_bootstrap")
    op.drop_column("users", "highlight_over_budget")
