"""Track the connector variant for import batches.

Revision ID: 20260904_0023
Revises: 20260901_0022
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260904_0023"
down_revision: str | None = "20260901_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "import_batches",
        sa.Column("connector_variant", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("import_batches", "connector_variant")
