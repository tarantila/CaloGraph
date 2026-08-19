"""Add historical activity-energy configuration to nutrition targets.

Revision ID: 20260817_0016
Revises: 20260816_0015
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_0016"
down_revision: str | None = "20260816_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "nutrition_targets",
        sa.Column(
            "activity_mode",
            sa.String(length=16),
            nullable=False,
            server_default="off",
        ),
    )
    op.add_column(
        "nutrition_targets",
        sa.Column("activity_source_type", sa.String(length=64), nullable=True),
    )
    op.create_check_constraint(
        "ck_target_activity_mode",
        "nutrition_targets",
        "activity_mode IN ('off', 'full')",
    )
    op.create_check_constraint(
        "ck_target_activity_source",
        "nutrition_targets",
        "(activity_mode = 'off' AND activity_source_type IS NULL) "
        "OR (activity_mode = 'full' AND activity_source_type IS NOT NULL "
        "AND activity_source_type IN "
        "('apple_health_xml', 'health_auto_export_v2', 'yazio_export_v1'))",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_target_activity_source",
        "nutrition_targets",
        type_="check",
    )
    op.drop_constraint(
        "ck_target_activity_mode",
        "nutrition_targets",
        type_="check",
    )
    op.drop_column("nutrition_targets", "activity_source_type")
    op.drop_column("nutrition_targets", "activity_mode")
