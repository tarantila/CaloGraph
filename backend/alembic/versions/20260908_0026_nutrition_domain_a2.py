"""Create the generic Source Priority and Nutrition Projection A2 schema.

Revision ID: 20260908_0026
Revises: 20260908_0025
"""

from collections.abc import Sequence

from alembic import op

from app.database import Base
from app.nutrition import models as nutrition_models  # noqa: F401
from app.source_priority import models as source_priority_models  # noqa: F401

revision: str = "20260908_0026"
down_revision: str | None = "20260908_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_A2_TABLES = (
    "source_priority_policies",
    "source_priority_rules",
    "nutrition_daily_projections",
    "nutrition_daily_projection_facts",
    "nutrition_daily_projection_lineage",
    "nutrition_projection_heads",
)


def upgrade() -> None:
    bind = op.get_bind()
    tables = [Base.metadata.tables[name] for name in _A2_TABLES]
    Base.metadata.create_all(bind=bind, tables=tables, checkfirst=False)


def downgrade() -> None:
    bind = op.get_bind()
    tables = [Base.metadata.tables[name] for name in reversed(_A2_TABLES)]
    Base.metadata.drop_all(bind=bind, tables=tables, checkfirst=False)
