"""Replace email-derived YAZIO identifiers with internal UUIDs.

Revision ID: 20260803_0009
Revises: 20260729_0008
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260803_0009"
down_revision: str | None = "20260729_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    connections = list(
        connection.execute(
            sa.text(
                """
                SELECT id, user_id, source_identifier
                FROM yazio_connections
                """
            )
        ).mappings()
    )
    current_sources = {
        row["user_id"]: row["source_identifier"] for row in connections
    }
    source_rows = list(
        connection.execute(
            sa.text(
                """
                SELECT DISTINCT user_id, source_identifier
                FROM health_samples
                WHERE source_type = 'yazio_export_v1'
                  AND source_identifier ~ '^yazio:[0-9a-f]{16}$'
                """
            )
        ).mappings()
    )
    for row in source_rows:
        current_identifier = current_sources.get(row["user_id"])
        new_identifier = (
            f"yazio:{row['user_id']}"
            if row["source_identifier"] == current_identifier
            else f"yazio:legacy:{uuid.uuid4()}"
        )
        connection.execute(
            sa.text(
                """
                UPDATE health_samples
                SET source_identifier = :new_identifier,
                    fingerprint =
                        md5(id::text || '-opaque-yazio-source-v1')
                        || md5('v1-' || id::text)
                WHERE user_id = :user_id
                  AND source_type = 'yazio_export_v1'
                  AND source_identifier = :old_identifier
                """
            ),
            {
                "user_id": row["user_id"],
                "old_identifier": row["source_identifier"],
                "new_identifier": new_identifier,
            },
        )

    for row in connections:
        new_identifier = f"yazio:{row['user_id']}"
        if row["source_identifier"] == new_identifier:
            continue
        connection.execute(
            sa.text(
                """
                UPDATE yazio_connections
                SET source_identifier = :new_identifier
                WHERE id = :connection_id
                """
            ),
            {
                "connection_id": row["id"],
                "new_identifier": new_identifier,
            },
        )
    op.drop_column("yazio_connections", "account_hash")


def downgrade() -> None:
    op.add_column(
        "yazio_connections",
        sa.Column("account_hash", sa.String(length=64), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE yazio_connections
            SET account_hash =
                md5(id::text || '-opaque-yazio-downgrade')
                || md5('down-' || id::text)
            """
        )
    )
    op.alter_column("yazio_connections", "account_hash", nullable=False)
