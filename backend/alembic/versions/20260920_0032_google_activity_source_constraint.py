"""Allow Google Health activity sources in nutrition targets.

Revision ID: 20260920_0032
Revises: 20260918_0031
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260920_0032"
down_revision: str | None = "20260918_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SOURCE_CONSTRAINT = "ck_target_activity_source"
_TABLE = "nutrition_targets"


def _replace_source_constraint(allowed_sources: str) -> None:
    op.drop_constraint(_SOURCE_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(
        _SOURCE_CONSTRAINT,
        _TABLE,
        "(activity_mode = 'off' AND activity_source_type IS NULL) "
        "OR (activity_mode = 'full' AND activity_source_type IS NOT NULL "
        "AND activity_source_type IN "
        f"({allowed_sources}))",
    )


def upgrade() -> None:
    _replace_source_constraint(
        "'google_health_activity_v4', 'apple_health_xml', "
        "'health_auto_export_v2', 'yazio_export_v1'"
    )


def downgrade() -> None:
    _replace_source_constraint(
        "'apple_health_xml', 'health_auto_export_v2', 'yazio_export_v1'"
    )
