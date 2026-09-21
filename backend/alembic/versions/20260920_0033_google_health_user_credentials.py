"""Store Google Health OAuth credentials per user.

Revision ID: 20260920_0033
Revises: 20260920_0032
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260920_0033"
down_revision: str | None = "20260920_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "google_health_connections"
_OLD_STATE_CONSTRAINT = "ck_google_health_connections_state"
_NEW_STATE_CONSTRAINT = "ck_google_health_connections_state"
_SYNC_STATE_CONSTRAINT = "ck_google_health_connections_sync_state"
_STATE_CHECK = "state IN ('active', 'reauth_required', 'not_connected')"
_OLD_STATE_CHECK = "state IN ('active', 'reauth_required')"
_SYNC_STATE_CHECK = "sync_state IN ('idle', 'running', 'completed', 'failed')"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(_TABLE, recreate="always") as batch:
            batch.add_column(sa.Column("client_id", sa.String(length=512), nullable=True))
            batch.add_column(sa.Column("encrypted_client_secret", sa.LargeBinary(), nullable=True))
            batch.add_column(
                sa.Column("sync_state", sa.String(length=16), nullable=False, server_default="idle")
            )
            batch.add_column(
                sa.Column("retry_attempt", sa.Integer(), nullable=False, server_default="0")
            )
            batch.add_column(
                sa.Column("retry_max_attempts", sa.Integer(), nullable=False, server_default="3")
            )
            batch.add_column(sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True))
            batch.add_column(sa.Column("last_error_category", sa.String(length=32), nullable=True))
            batch.alter_column(
                "encrypted_refresh_token",
                existing_type=sa.LargeBinary(),
                nullable=True,
            )
            batch.drop_constraint(_OLD_STATE_CONSTRAINT, type_="check")
            batch.create_check_constraint(_NEW_STATE_CONSTRAINT, _STATE_CHECK)
            batch.create_check_constraint(_SYNC_STATE_CONSTRAINT, _SYNC_STATE_CHECK)
    else:
        op.add_column(_TABLE, sa.Column("client_id", sa.String(length=512), nullable=True))
        op.add_column(
            _TABLE,
            sa.Column("encrypted_client_secret", sa.LargeBinary(), nullable=True),
        )
        op.add_column(
            _TABLE,
            sa.Column("sync_state", sa.String(length=16), nullable=False, server_default="idle"),
        )
        op.add_column(
            _TABLE,
            sa.Column("retry_attempt", sa.Integer(), nullable=False, server_default="0"),
        )
        op.add_column(
            _TABLE,
            sa.Column("retry_max_attempts", sa.Integer(), nullable=False, server_default="3"),
        )
        op.add_column(_TABLE, sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column(_TABLE, sa.Column("last_error_category", sa.String(length=32), nullable=True))
        op.alter_column(
            _TABLE,
            "encrypted_refresh_token",
            existing_type=sa.LargeBinary(),
            nullable=True,
        )
        op.drop_constraint(_OLD_STATE_CONSTRAINT, table_name=_TABLE, type_="check")
        op.create_check_constraint(_NEW_STATE_CONSTRAINT, _TABLE, _STATE_CHECK)
        op.create_check_constraint(_SYNC_STATE_CONSTRAINT, _TABLE, _SYNC_STATE_CHECK)

    # Existing encrypted token bytes are intentionally never read or rewritten.
    op.execute(
        sa.text(
            f"UPDATE {_TABLE} SET state = 'reauth_required' "
            "WHERE state IN ('active', 'reauth_required')"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    invalid_rows = bind.scalar(
        sa.text(
            f"SELECT COUNT(*) FROM {_TABLE} "
            "WHERE encrypted_refresh_token IS NULL OR state = 'not_connected'"
        )
    )
    if invalid_rows:
        raise RuntimeError(
            "Cannot downgrade Google Health credentials while rows have a null "
            "encrypted refresh token or the not_connected state"
        )

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(_TABLE, recreate="always") as batch:
            batch.drop_constraint(_SYNC_STATE_CONSTRAINT, type_="check")
            batch.drop_constraint(_NEW_STATE_CONSTRAINT, type_="check")
            batch.alter_column(
                "encrypted_refresh_token",
                existing_type=sa.LargeBinary(),
                nullable=False,
            )
            batch.drop_column("last_error_category")
            batch.drop_column("next_retry_at")
            batch.drop_column("retry_max_attempts")
            batch.drop_column("retry_attempt")
            batch.drop_column("sync_state")
            batch.drop_column("encrypted_client_secret")
            batch.drop_column("client_id")
            batch.create_check_constraint(_OLD_STATE_CONSTRAINT, _OLD_STATE_CHECK)
    else:
        op.drop_constraint(_SYNC_STATE_CONSTRAINT, table_name=_TABLE, type_="check")
        op.drop_constraint(_NEW_STATE_CONSTRAINT, table_name=_TABLE, type_="check")
        op.alter_column(
            _TABLE,
            "encrypted_refresh_token",
            existing_type=sa.LargeBinary(),
            nullable=False,
        )
        for column in (
            "last_error_category",
            "next_retry_at",
            "retry_max_attempts",
            "retry_attempt",
            "sync_state",
            "encrypted_client_secret",
            "client_id",
        ):
            op.drop_column(_TABLE, column)
        op.create_check_constraint(_OLD_STATE_CONSTRAINT, _TABLE, _OLD_STATE_CHECK)
