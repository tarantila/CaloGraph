"""Correct YAZIO micronutrient units stored by earlier importer versions.

Revision ID: 20260727_0005
Revises: 20260723_0004
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260727_0005"
down_revision: str | None = "20260723_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


MICRONUTRIENT_METRICS = (
    "vitamin_a_ug",
    "thiamin_mg",
    "riboflavin_mg",
    "niacin_mg",
    "pantothenic_acid_mg",
    "vitamin_b6_mg",
    "biotin_ug",
    "folate_ug",
    "vitamin_b12_ug",
    "vitamin_c_mg",
    "vitamin_d_ug",
    "vitamin_e_mg",
    "vitamin_k_ug",
    "calcium_mg",
    "iron_mg",
    "potassium_mg",
    "magnesium_mg",
    "phosphorus_mg",
    "zinc_mg",
    "copper_mg",
    "manganese_mg",
    "selenium_ug",
    "iodine_ug",
    "fluoride_mg",
    "chloride_mg",
    "choline_mg",
)


def _quoted_metrics() -> str:
    return ", ".join(f"'{metric}'" for metric in MICRONUTRIENT_METRICS)


def upgrade() -> None:
    op.alter_column(
        "health_samples",
        "original_value",
        existing_type=sa.Numeric(20, 6),
        type_=sa.Numeric(24, 12),
        existing_nullable=False,
    )
    op.execute(
        sa.text(
            f"""
            UPDATE health_samples
            SET value = CASE
                    WHEN unit = 'mg' THEN value * 1000
                    WHEN unit = 'ug' THEN value * 1000000
                    ELSE value
                END,
                original_unit = 'g',
                fingerprint =
                    md5(id::text || '-yazio-micronutrient-unit-fix-v1')
                    || md5('v1-' || id::text)
            WHERE source_type = 'yazio_export_v1'
              AND metric_type IN ({_quoted_metrics()})
              AND unit IN ('mg', 'ug')
              AND original_unit = unit
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            f"""
            UPDATE health_samples
            SET value = CASE
                    WHEN unit = 'mg' THEN value / 1000
                    WHEN unit = 'ug' THEN value / 1000000
                    ELSE value
                END,
                original_unit = unit,
                fingerprint =
                    md5(id::text || '-yazio-micronutrient-unit-fix-down')
                    || md5('down-' || id::text)
            WHERE source_type = 'yazio_export_v1'
              AND metric_type IN ({_quoted_metrics()})
              AND unit IN ('mg', 'ug')
              AND original_unit = 'g'
            """
        )
    )
    op.alter_column(
        "health_samples",
        "original_value",
        existing_type=sa.Numeric(24, 12),
        type_=sa.Numeric(20, 6),
        existing_nullable=False,
    )
