"""Migrate provider preferences and snapshot activity target sources.

Revision ID: 20260918_0031
Revises: 20260911_0030
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op
from app.activity import ACTIVITY_PROVIDER_SOURCE_TYPES

revision: str = "20260918_0031"
down_revision: str | None = "20260911_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_USER_PRIORITY_TABLE = "user_provider_priorities"
_ACTIVITY_TABLE = "nutrition_target_activity_sources"
_TARGET_SCOPE_KEY = "uq_nutrition_targets_id_user"
_ACTIVITY_PROVIDER_INDEX = "uq_nutrition_target_activity_sources_target_provider"
_ACTIVITY_USER_INDEX = "ix_nutrition_target_activity_sources_user_id"
_ACTIVITY_PRIORITY_UNIQUE = "uq_nutrition_target_activity_sources_target_priority"
_ACTIVITY_PRIMARY_KEY = "pk_nutrition_target_activity_sources"


def _target_scope_key_exists(bind: sa.Connection) -> bool:
    inspector = inspect(bind)
    target_columns = ("id", "user_id")
    return any(
        tuple(item.get("column_names", ())) == target_columns
        for item in inspector.get_unique_constraints("nutrition_targets")
    ) or any(
        tuple(item.get("column_names", ())) == target_columns and item.get("unique")
        for item in inspector.get_indexes("nutrition_targets")
    )


def _ensure_target_scope_key(bind: sa.Connection) -> None:
    if not _target_scope_key_exists(bind):
        op.create_index(
            _TARGET_SCOPE_KEY,
            "nutrition_targets",
            ["id", "user_id"],
            unique=True,
        )


def _create_user_priority_table(bind: sa.Connection) -> None:
    if _USER_PRIORITY_TABLE in inspect(bind).get_table_names():
        return
    op.create_table(
        _USER_PRIORITY_TABLE,
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("data_area", sa.String(length=64), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("provider_key", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(data_area) > 0",
            name="ck_user_provider_priorities_data_area",
        ),
        sa.CheckConstraint(
            "length(provider_key) > 0",
            name="ck_user_provider_priorities_provider_key",
        ),
        sa.CheckConstraint(
            "priority >= 1",
            name="ck_user_provider_priorities_priority",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "data_area", "priority"),
        sa.UniqueConstraint(
            "user_id",
            "data_area",
            "provider_key",
            name="uq_user_provider_priorities_provider",
        ),
    )
    op.create_index(
        "ix_user_provider_priorities_user_area_priority",
        _USER_PRIORITY_TABLE,
        ["user_id", "data_area", "priority"],
    )
def _create_activity_table(bind: sa.Connection) -> None:
    _ensure_target_scope_key(bind)
    op.create_table(
        _ACTIVITY_TABLE,
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("provider_key", sa.String(length=64), nullable=True),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["target_id", "user_id"],
            ["nutrition_targets.id", "nutrition_targets.user_id"],
            name="fk_nutrition_target_activity_sources_target_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("target_id", "priority", name=_ACTIVITY_PRIMARY_KEY),
        sa.UniqueConstraint(
            "target_id",
            "priority",
            name=_ACTIVITY_PRIORITY_UNIQUE,
        ),
        sa.CheckConstraint(
            "priority >= 1",
            name="ck_nutrition_target_activity_sources_priority",
        ),
        sa.CheckConstraint(
            "provider_key IS NULL OR length(provider_key) > 0",
            name="ck_nutrition_target_activity_sources_provider_key",
        ),
        sa.CheckConstraint(
            "length(source_type) > 0",
            name="ck_nutrition_target_activity_sources_source_type",
        ),
    )
    op.create_index(
        _ACTIVITY_PROVIDER_INDEX,
        _ACTIVITY_TABLE,
        ["target_id", "provider_key"],
        unique=True,
        postgresql_where=sa.text("provider_key IS NOT NULL"),
        sqlite_where=sa.text("provider_key IS NOT NULL"),
    )
    op.create_index(_ACTIVITY_USER_INDEX, _ACTIVITY_TABLE, ["user_id"])


def _activity_source_type_provider_keys() -> dict[str, str]:
    providers_by_source_type: defaultdict[str, list[str]] = defaultdict(list)
    for provider_key, source_type in ACTIVITY_PROVIDER_SOURCE_TYPES.items():
        providers_by_source_type[source_type].append(provider_key)
    return {
        source_type: provider_keys[0]
        for source_type, provider_keys in providers_by_source_type.items()
        if len(provider_keys) == 1
    }


def _backfill_activity_sources(bind: sa.Connection) -> None:
    targets = sa.table(
        "nutrition_targets",
        sa.column("id", sa.Uuid()),
        sa.column("user_id", sa.Uuid()),
        sa.column("activity_source_type", sa.String()),
    )
    activity_sources = sa.table(
        _ACTIVITY_TABLE,
        sa.column("target_id", sa.Uuid()),
        sa.column("user_id", sa.Uuid()),
        sa.column("priority", sa.Integer()),
        sa.column("provider_key", sa.String()),
        sa.column("source_type", sa.String()),
    )
    provider_by_source_type = _activity_source_type_provider_keys()
    provider_key = sa.case(
        provider_by_source_type,
        value=targets.c.activity_source_type,
    )
    bind.execute(
        activity_sources.insert().from_select(
            ["target_id", "user_id", "priority", "provider_key", "source_type"],
            sa.select(
                targets.c.id,
                targets.c.user_id,
                sa.literal(1),
                provider_key,
                targets.c.activity_source_type,
            ).where(targets.c.activity_source_type.is_not(None)),
        )
    )








def _migrate_preferences(bind: sa.Connection) -> None:
    preferences = sa.table(
        "user_provider_preferences",
        sa.column("user_id", sa.Uuid()),
        sa.column("data_area", sa.String()),
        sa.column("provider_key", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    priorities = sa.table(
        _USER_PRIORITY_TABLE,
        sa.column("user_id", sa.Uuid()),
        sa.column("data_area", sa.String()),
        sa.column("priority", sa.Integer()),
        sa.column("provider_key", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    rows = list(
        bind.execute(
            sa.select(
                preferences.c.user_id,
                preferences.c.data_area,
                preferences.c.provider_key,
                preferences.c.created_at,
                preferences.c.updated_at,
            ).order_by(preferences.c.user_id, preferences.c.data_area)
        ).mappings()
    )
    if rows:
        bind.execute(
            priorities.insert(),
            [
                {
                    "user_id": row["user_id"],
                    "data_area": row["data_area"],
                    "priority": 1,
                    "provider_key": row["provider_key"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
                for row in rows
            ],
        )


def _drop_preferences(bind: sa.Connection) -> None:
    op.drop_table("user_provider_preferences")


def _recreate_preferences(bind: sa.Connection, rows: list[dict[str, object]]) -> None:
    preferences = sa.table(
        "user_provider_preferences",
        sa.column("user_id", sa.Uuid()),
        sa.column("data_area", sa.String()),
        sa.column("provider_key", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    if "user_provider_preferences" not in inspect(bind).get_table_names():
        op.create_table(
            "user_provider_preferences",
            sa.Column("user_id", sa.Uuid(), nullable=False),
            sa.Column("data_area", sa.String(length=64), nullable=False),
            sa.Column("provider_key", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint(
                "length(data_area) > 0",
                name="ck_provider_preferences_data_area",
            ),
            sa.CheckConstraint(
                "length(provider_key) > 0",
                name="ck_provider_preferences_provider_key",
            ),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("user_id", "data_area"),
        )
    else:
        bind.execute(preferences.delete())
    if rows:
        bind.execute(preferences.insert(), rows)


def _legacy_preference_rows(bind: sa.Connection) -> list[dict[str, object]]:
    priorities = sa.table(
        _USER_PRIORITY_TABLE,
        sa.column("user_id", sa.Uuid()),
        sa.column("data_area", sa.String()),
        sa.column("priority", sa.Integer()),
        sa.column("provider_key", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    rows = bind.execute(
        sa.select(
            priorities.c.user_id,
            priorities.c.data_area,
            priorities.c.priority,
            priorities.c.provider_key,
            priorities.c.created_at,
            priorities.c.updated_at,
        ).order_by(
            priorities.c.user_id,
            priorities.c.data_area,
            priorities.c.priority,
        )
    ).mappings()
    seen: set[tuple[object, object]] = set()
    preferences: list[dict[str, object]] = []
    for row in rows:
        scope = (row["user_id"], row["data_area"])
        if scope in seen:
            raise RuntimeError(
                "provider priority downgrade aborted: multiple current priorities "
                f"for user,data_area {row['user_id']},{row['data_area']}"
            )
        seen.add(scope)
        preferences.append(
            {
                "user_id": row["user_id"],
                "data_area": row["data_area"],
                "provider_key": row["provider_key"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )
    return preferences




def _drop_activity_table(bind: sa.Connection) -> None:
    op.drop_index(_ACTIVITY_USER_INDEX, table_name=_ACTIVITY_TABLE)
    op.drop_index(_ACTIVITY_PROVIDER_INDEX, table_name=_ACTIVITY_TABLE)
    op.drop_table(_ACTIVITY_TABLE)


def _drop_owned_target_scope_key(bind: sa.Connection) -> None:
    inspector = inspect(bind)
    if _TARGET_SCOPE_KEY not in {item["name"] for item in inspector.get_indexes("nutrition_targets")}:
        return
    if _target_scope_key_exists(bind):
        unique_constraints = inspector.get_unique_constraints("nutrition_targets")
        if any(
            item.get("name") == _TARGET_SCOPE_KEY
            and tuple(item.get("column_names", ())) == ("id", "user_id")
            for item in unique_constraints
        ):
            return
    op.drop_index(_TARGET_SCOPE_KEY, table_name="nutrition_targets")


def upgrade() -> None:
    bind = op.get_bind()
    _create_user_priority_table(bind)
    _create_activity_table(bind)
    _backfill_activity_sources(bind)
    _migrate_preferences(bind)
    _drop_preferences(bind)


def downgrade() -> None:
    bind = op.get_bind()
    preferences = _legacy_preference_rows(bind)
    _recreate_preferences(bind, preferences)
    if _USER_PRIORITY_TABLE in inspect(bind).get_table_names():
        if "ix_user_provider_priorities_user_area_priority" in {
            item["name"] for item in inspect(bind).get_indexes(_USER_PRIORITY_TABLE)
        }:
            op.drop_index(
                "ix_user_provider_priorities_user_area_priority",
                table_name=_USER_PRIORITY_TABLE,
            )
        op.drop_table(_USER_PRIORITY_TABLE)
    _drop_activity_table(bind)
    _drop_owned_target_scope_key(bind)
