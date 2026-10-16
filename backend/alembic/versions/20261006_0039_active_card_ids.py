"""Persist canonical ordered Analysis Hub card IDs.

Revision ID: 20261006_0039
Revises: 20261005_0038
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261006_0039"
down_revision: str | None = "20261005_0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_CARDS = ["weight", "nutrition", "micronutrients", "activity"]
SUMMARY_IDS = set(DEFAULT_CARDS)
ALIASES = {"weight_kg": "weight", "active_energy_kcal": "activity"}
# Frozen migration contract: the card-capable analysis IDs at this revision.
METRIC_IDS = {
    "vitamin_a_ug", "thiamin_mg", "riboflavin_mg", "niacin_mg", "pantothenic_acid_mg",
    "vitamin_b6_mg", "biotin_ug", "folate_ug", "vitamin_b12_ug", "vitamin_c_mg",
    "vitamin_d_ug", "vitamin_e_mg", "vitamin_k_ug", "calcium_mg", "iron_mg",
    "potassium_mg", "magnesium_mg", "phosphorus_mg", "zinc_mg", "copper_mg",
    "manganese_mg", "selenium_ug", "iodine_ug", "fluoride_mg", "chloride_mg", "choline_mg",
    "dietary_energy_kcal", "protein_g", "carbohydrates_g", "fat_g", "fiber_g", "sugar_g",
    "saturated_fat_g",
}


def build_active_card_ids(user_id: object, summary_order: object, analysis_keys: object) -> list[str]:
    if (
        not isinstance(summary_order, list)
        or len(summary_order) != 4
        or not all(isinstance(value, str) for value in summary_order)
        or len(set(summary_order)) != 4
        or set(summary_order) != SUMMARY_IDS
    ):
        raise RuntimeError(f"Invalid summary_order for user {user_id}; refusing to discard preference data")
    if not isinstance(analysis_keys, list) or not all(isinstance(key, str) for key in analysis_keys):
        raise RuntimeError(f"Invalid analysis preferences for user {user_id}; refusing to discard preference data")
    result = list(summary_order)
    seen = set(result)
    for analysis_key in analysis_keys:
        if analysis_key not in METRIC_IDS and analysis_key not in ALIASES:
            raise RuntimeError(
                f"Invalid analysis preference {analysis_key!r} for user {user_id}; refusing to discard preference data"
            )
        card_id = ALIASES.get(analysis_key, analysis_key)
        if card_id not in seen:
            result.append(card_id)
            seen.add(card_id)
    return result


def upgrade() -> None:
    op.add_column("users", sa.Column("active_card_ids", sa.JSON(), nullable=True))
    connection = op.get_bind()
    users = sa.table(
        "users",
        sa.column("id"),
        sa.column("summary_order", sa.JSON()),
        sa.column("active_card_ids", sa.JSON()),
    )
    preferences = sa.table(
        "user_analysis_preferences",
        sa.column("user_id"),
        sa.column("analysis_key", sa.String()),
        sa.column("position", sa.Integer()),
    )
    for user_id, summary_order in connection.execute(sa.select(users.c.id, users.c.summary_order)):
        analysis_keys = list(
            connection.execute(
                sa.select(preferences.c.analysis_key)
                .where(preferences.c.user_id == user_id)
                .order_by(preferences.c.position, preferences.c.analysis_key)
            ).scalars()
        )
        active_ids = build_active_card_ids(user_id, summary_order, analysis_keys)
        connection.execute(
            users.update().where(users.c.id == user_id).values(active_card_ids=active_ids)
        )
    op.alter_column(
        "users",
        "active_card_ids",
        server_default=sa.text("'[\"weight\", \"nutrition\", \"micronutrients\", \"activity\"]'"),
    )


def downgrade() -> None:
    op.drop_column("users", "active_card_ids")
