"""Allow calorie budgets above maintenance estimates.

Revision ID: 20260811_0012
Revises: 20260810_0011
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260811_0012"
down_revision: str | None = "20260810_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_target_maintenance_at_least_budget",
        "nutrition_targets",
        type_="check",
    )
    op.create_check_constraint(
        "ck_target_maintenance_positive_finite",
        "nutrition_targets",
        "maintenance_kcal IS NULL OR "
        "(maintenance_kcal > 0 AND maintenance_kcal < 'Infinity')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_target_maintenance_positive_finite",
        "nutrition_targets",
        type_="check",
    )
    op.create_check_constraint(
        "ck_target_maintenance_at_least_budget",
        "nutrition_targets",
        "maintenance_kcal IS NULL OR maintenance_kcal >= calories_kcal",
    )
