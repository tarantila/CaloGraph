"""Create the append-only Nutrition Domain A1 schema.

Revision ID: 20260908_0025
Revises: 20260906_0024
"""

from collections.abc import Sequence

from alembic import op

from app.nutrition import models as nutrition_models

revision: str = "20260908_0025"
down_revision: str | None = "20260906_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_A1_TABLES = (
    "nutrition_ingestion_runs",
    "nutrition_source_observations",
    "nutrition_external_identities",
    "nutrition_food_profiles",
    "nutrition_food_snapshots",
    "nutrition_consumption_events",
    "nutrition_serving_observations",
    "nutrition_field_observations",
    "nutrition_source_tombstones",
    "nutrition_provenance",
    "nutrition_external_identity_links",
)


def upgrade() -> None:
    bind = op.get_bind()
    tables = [nutrition_models.Base.metadata.tables[name] for name in _A1_TABLES]
    nutrition_models.Base.metadata.create_all(bind=bind, tables=tables, checkfirst=False)


def downgrade() -> None:
    bind = op.get_bind()
    tables = [nutrition_models.Base.metadata.tables[name] for name in reversed(_A1_TABLES)]
    nutrition_models.Base.metadata.drop_all(bind=bind, tables=tables, checkfirst=False)
