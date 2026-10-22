"""Add durable acknowledgement for pending canonical YAZIO projections.

Revision ID: 20261021_0040
Revises: 20261006_0039
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261021_0040"
down_revision: str | None = "20261006_0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "nutrition_ingestion_runs"
_INDEX = "ix_nutrition_ingestion_runs_projection_pending"


def upgrade() -> None:
    bind = op.get_bind()
    # 0025 creates tables from current model metadata on fresh installations.
    # Inspect independently: the column can exist without its partial index.
    columns = {column["name"] for column in sa.inspect(bind).get_columns(_TABLE)}
    if "projection_pending" not in columns:
        op.add_column(
            _TABLE,
            sa.Column("projection_pending", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes(_TABLE)}
    if _INDEX not in indexes:
        op.create_index(
            _INDEX,
            _TABLE,
            ["user_id", "provider_key", "source_instance_id", "created_at", "id"],
            postgresql_where=sa.text("projection_pending = true"),
            sqlite_where=sa.text("projection_pending = true"),
        )

    # Freeze this revision's data contract instead of importing evolving ORM
    # models. Even previously successful projections are conservatively replayed.
    runs = sa.table(
        _TABLE,
        sa.column("provider_key", sa.String(64)),
        sa.column("connector_variant", sa.String(64)),
        sa.column("status", sa.String(32)),
        sa.column("source_instance_id", sa.Uuid()),
        sa.column("requested_start_date", sa.Date()),
        sa.column("requested_end_date", sa.Date()),
        sa.column("projection_pending", sa.Boolean()),
    )
    bind.execute(
        runs.update()
        .where(
            runs.c.provider_key == "yazio",
            runs.c.connector_variant == "sdk-v22",
            runs.c.status == "completed",
            runs.c.source_instance_id.is_not(None),
            runs.c.requested_start_date.is_not(None),
            runs.c.requested_end_date.is_not(None),
            runs.c.requested_start_date <= runs.c.requested_end_date,
        )
        .values(projection_pending=True)
    )


def downgrade() -> None:
    bind = op.get_bind()
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes(_TABLE)}
    if _INDEX in indexes:
        op.drop_index(_INDEX, table_name=_TABLE)
    columns = {column["name"] for column in sa.inspect(bind).get_columns(_TABLE)}
    if "projection_pending" in columns:
        op.drop_column(_TABLE, "projection_pending")
