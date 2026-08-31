"""Add optional target weight ranges to nutrition targets.

Revision ID: 20260830_0020
Revises: 20260829_0019
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260830_0020"
down_revision: str | None = "20260829_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "nutrition_targets",
        sa.Column("target_weight_min_kg", sa.Numeric(precision=7, scale=3), nullable=True),
    )
    op.add_column(
        "nutrition_targets",
        sa.Column("target_weight_max_kg", sa.Numeric(precision=7, scale=3), nullable=True),
    )
    op.create_check_constraint(
        "ck_target_weight_range",
        "nutrition_targets",
        "(target_weight_min_kg IS NULL AND target_weight_max_kg IS NULL) "
        "OR (target_weight_min_kg IS NOT NULL AND target_weight_max_kg IS NOT NULL "
        "AND target_weight_min_kg > 0 "
        "AND target_weight_min_kg <= target_weight_max_kg "
        "AND target_weight_max_kg <= 1000)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_target_weight_range",
        "nutrition_targets",
        type_="check",
    )
    op.drop_column("nutrition_targets", "target_weight_max_kg")
    op.drop_column("nutrition_targets", "target_weight_min_kg")
