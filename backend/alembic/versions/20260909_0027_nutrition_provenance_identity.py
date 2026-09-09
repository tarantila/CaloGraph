"""Enforce append-only NutritionProvenance identity uniqueness.

Revision ID: 20260909_0027
Revises: 20260908_0026
"""

from collections.abc import Sequence

from sqlalchemy import inspect, text

from alembic import op

revision: str = "20260909_0027"
down_revision: str | None = "20260908_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PROVENANCE_TARGETS = (
    ("event", "consumption_event_id"),
    ("snapshot", "food_snapshot_id"),
    ("serving", "serving_observation_id"),
    ("field", "field_observation_id"),
)
_SERVING_LOOKUP_INDEX = "ix_nutrition_provenance_user_serving"


def _index_name(target_name: str) -> str:
    return f"uq_nutrition_provenance_{target_name}_identity"


def upgrade() -> None:
    bind = op.get_bind()
    existing_indexes = {index["name"] for index in inspect(bind).get_indexes("nutrition_provenance")}
    for target_name, target_column in _PROVENANCE_TARGETS:
        name = _index_name(target_name)
        if name in existing_indexes:
            continue
        op.create_index(
            name,
            "nutrition_provenance",
            ["user_id", "source_observation_id", "role", target_column],
            unique=True,
            postgresql_where=text(f"{target_column} IS NOT NULL"),
            sqlite_where=text(f"{target_column} IS NOT NULL"),
        )
    if _SERVING_LOOKUP_INDEX not in existing_indexes:
        op.create_index(
            _SERVING_LOOKUP_INDEX,
            "nutrition_provenance",
            ["user_id", "serving_observation_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    existing_indexes = {index["name"] for index in inspect(bind).get_indexes("nutrition_provenance")}
    if _SERVING_LOOKUP_INDEX in existing_indexes:
        op.drop_index(_SERVING_LOOKUP_INDEX, table_name="nutrition_provenance")
    for target_name, _ in reversed(_PROVENANCE_TARGETS):
        name = _index_name(target_name)
        if name in existing_indexes:
            op.drop_index(name, table_name="nutrition_provenance")
