"""Add versioned maintenance calories to nutrition targets.

Revision ID: 20260727_0006
Revises: 20260727_0005
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260727_0006"
down_revision: str | None = "20260727_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "nutrition_targets",
        sa.Column("maintenance_kcal", sa.Numeric(12, 3), nullable=True),
    )
    op.create_check_constraint(
        "ck_target_maintenance_at_least_budget",
        "nutrition_targets",
        "maintenance_kcal IS NULL OR maintenance_kcal >= calories_kcal",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_target_maintenance_at_least_budget",
        "nutrition_targets",
        type_="check",
    )
    op.drop_column("nutrition_targets", "maintenance_kcal")
