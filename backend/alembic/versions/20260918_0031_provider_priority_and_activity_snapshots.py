"""Migrate provider preferences and snapshot activity target sources.

Revision ID: 20260918_0031
Revises: 20260911_0030
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

from app.activity import ACTIVITY_PROVIDER_SOURCE_TYPES

revision: str = "20260918_0031"
down_revision: str | None = "20260911_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MIGRATION_EFFECTIVE_FROM = datetime(2026, 9, 18, tzinfo=UTC)
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
    rows = bind.execute(
        sa.select(targets.c.id, targets.c.user_id, targets.c.activity_source_type).where(
            targets.c.activity_source_type.is_not(None)
        )
    ).mappings()
    for row in rows:
        source_type = row["activity_source_type"]
        bind.execute(
            activity_sources.insert().values(
                target_id=row["id"],
                user_id=row["user_id"],
                priority=1,
                provider_key=provider_by_source_type.get(source_type),
                source_type=source_type,
            )
        )


def _policy_tables() -> tuple[sa.TableClause, sa.TableClause]:
    policies = sa.table(
        "source_priority_policies",
        sa.column("id", sa.Uuid()),
        sa.column("user_id", sa.Uuid()),
        sa.column("version", sa.Integer()),
        sa.column("effective_from", sa.DateTime(timezone=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    rules = sa.table(
        "source_priority_rules",
        sa.column("id", sa.Uuid()),
        sa.column("user_id", sa.Uuid()),
        sa.column("policy_id", sa.Uuid()),
        sa.column("data_area", sa.String()),
        sa.column("metric_key", sa.String()),
        sa.column("provider_key", sa.String()),
        sa.column("priority_rank", sa.Integer()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    return policies, rules


def _existing_policy_versions(
    bind: sa.Connection,
    policies: sa.TableClause,
) -> dict[object, int]:
    rows = bind.execute(
        sa.select(policies.c.user_id, policies.c.version).order_by(
            policies.c.user_id,
            policies.c.version,
        )
    )
    versions: dict[object, int] = {}
    for row in rows:
        versions[row.user_id] = max(versions.get(row.user_id, 0), int(row.version))
    return versions


def _effective_timestamp_is_taken(
    bind: sa.Connection,
    policies: sa.TableClause,
    user_id: object,
    effective_from: datetime,
) -> bool:
    rows = bind.execute(
        sa.select(policies.c.effective_from).where(policies.c.user_id == user_id)
    )
    for row in rows:
        value = row.effective_from
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        if value.astimezone(UTC) == effective_from:
            return True
    return False


def _migrate_preferences(bind: sa.Connection) -> None:
    preferences = sa.table(
        "user_provider_preferences",
        sa.column("user_id", sa.Uuid()),
        sa.column("data_area", sa.String()),
        sa.column("provider_key", sa.String()),
    )
    policies, rules = _policy_tables()
    preference_rows = bind.execute(
        sa.select(
            preferences.c.user_id,
            preferences.c.data_area,
            preferences.c.provider_key,
        ).order_by(preferences.c.user_id, preferences.c.data_area)
    ).mappings()
    grouped: dict[object, list[dict[str, object]]] = defaultdict(list)
    for row in preference_rows:
        grouped[row["user_id"]].append(row)

    versions = _existing_policy_versions(bind, policies)
    for user_id in sorted(grouped, key=str):
        effective_from = MIGRATION_EFFECTIVE_FROM
        while _effective_timestamp_is_taken(bind, policies, user_id, effective_from):
            effective_from += timedelta(microseconds=1)
        version = versions.get(user_id, 0) + 1
        policy_id = uuid4()
        bind.execute(
            policies.insert().values(
                id=policy_id,
                user_id=user_id,
                version=version,
                effective_from=effective_from,
                created_at=effective_from,
            )
        )
        bind.execute(
            rules.insert(),
            [
                {
                    "id": uuid4(),
                    "user_id": user_id,
                    "policy_id": policy_id,
                    "data_area": row["data_area"],
                    "metric_key": None,
                    "provider_key": row["provider_key"],
                    "priority_rank": 1,
                    "created_at": effective_from,
                }
                for row in grouped[user_id]
            ],
        )


def _drop_preferences(bind: sa.Connection) -> None:
    op.drop_table("user_provider_preferences")


def _recreate_preferences(bind: sa.Connection, rows: list[dict[str, object]]) -> None:
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
    if rows:
        preferences = sa.table(
            "user_provider_preferences",
            sa.column("user_id", sa.Uuid()),
            sa.column("data_area", sa.String()),
            sa.column("provider_key", sa.String()),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        )
        bind.execute(preferences.insert(), rows)


def _current_policy_rules(bind: sa.Connection) -> list[dict[str, object]]:
    policies, rules = _policy_tables()
    policy_rows = bind.execute(
        sa.select(
            policies.c.id,
            policies.c.user_id,
            policies.c.version,
        ).order_by(policies.c.user_id, policies.c.version.desc(), policies.c.id)
    ).mappings()
    current_policy_by_user: dict[object, object] = {}
    for row in policy_rows:
        current_policy_by_user.setdefault(row["user_id"], row["id"])

    current_rules: list[dict[str, object]] = []
    for user_id, policy_id in current_policy_by_user.items():
        rows = bind.execute(
            sa.select(
                rules.c.data_area,
                rules.c.metric_key,
                rules.c.provider_key,
                rules.c.priority_rank,
            )
            .where(rules.c.user_id == user_id)
            .where(rules.c.policy_id == policy_id)
            .order_by(rules.c.data_area, rules.c.priority_rank, rules.c.id)
        ).mappings()
        for row in rows:
            current_rules.append(
                {
                    "user_id": user_id,
                    "data_area": row["data_area"],
                    "metric_key": row["metric_key"],
                    "provider_key": row["provider_key"],
                    "priority_rank": row["priority_rank"],
                }
            )
    return current_rules


def _legacy_preference_rows(bind: sa.Connection) -> list[dict[str, object]]:
    rows = _current_policy_rules(bind)
    seen: set[tuple[object, object]] = set()
    preferences: list[dict[str, object]] = []
    for row in rows:
        scope = (row["user_id"], row["data_area"])
        if row["metric_key"] is not None:
            raise RuntimeError(
                "provider priority downgrade aborted: current metric-specific rules "
                f"cannot be represented for user {row['user_id']} data_area {row['data_area']}"
            )
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
                "created_at": MIGRATION_EFFECTIVE_FROM,
                "updated_at": MIGRATION_EFFECTIVE_FROM,
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
    _create_activity_table(bind)
    _backfill_activity_sources(bind)
    _migrate_preferences(bind)
    _drop_preferences(bind)


def downgrade() -> None:
    bind = op.get_bind()
    preferences = _legacy_preference_rows(bind)
    _recreate_preferences(bind, preferences)
    _drop_activity_table(bind)
    _drop_owned_target_scope_key(bind)
