"""Scope nutrition external identities by source instance.

Revision ID: 20260910_0028
Revises: 20260909_0027
"""

from collections import defaultdict
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260910_0028"
down_revision: str | None = "20260909_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "nutrition_external_identities"
_OLD_UNIQUE = "uq_nutrition_external_identity_value"
_NEW_UNIQUE = "uq_nutrition_external_identity_source_value"
_LOOKUP_INDEX = "ix_nutrition_external_identities_user_source_namespace"
_OLD_LOOKUP_INDEX = "ix_nutrition_external_identities_user_namespace"


def _source_scoped_schema_status(bind: sa.Connection) -> bool:
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns(_TABLE)}
    if "source_instance_id" not in columns:
        return False
    unique_names = {
        constraint["name"] for constraint in inspector.get_unique_constraints(_TABLE)
    }
    index_names = {index["name"] for index in inspector.get_indexes(_TABLE)}
    if _NEW_UNIQUE in unique_names and _LOOKUP_INDEX in index_names:
        return True
    raise RuntimeError(
        "nutrition identity source migration found a partial source-scoped schema"
    )


def _identity_source_evidence(
    bind: sa.Connection,
) -> tuple[list[tuple[object, object]], dict[tuple[object, object], set[object]]]:
    identities = bind.execute(
        sa.text("SELECT id, user_id FROM nutrition_external_identities")
    ).all()
    evidence = bind.execute(
        sa.text(
            """
            SELECT link.external_identity_id, link.user_id, event.source_instance_id
            FROM nutrition_external_identity_links AS link
            JOIN nutrition_consumption_events AS event
              ON event.id = link.consumption_event_id
             AND event.user_id = link.user_id
            WHERE link.consumption_event_id IS NOT NULL
            UNION
            SELECT link.external_identity_id, link.user_id, profile.source_instance_id
            FROM nutrition_external_identity_links AS link
            JOIN nutrition_food_profiles AS profile
              ON profile.id = link.food_profile_id
             AND profile.user_id = link.user_id
            WHERE link.food_profile_id IS NOT NULL
            UNION
            SELECT link.external_identity_id, link.user_id, observation.source_instance_id
            FROM nutrition_external_identity_links AS link
            JOIN nutrition_source_observations AS observation
              ON observation.id = link.source_observation_id
             AND observation.user_id = link.user_id
            WHERE link.source_observation_id IS NOT NULL
            """
        )
    ).all()
    by_identity: dict[tuple[object, object], set[object]] = defaultdict(set)
    for identity_id, user_id, source_instance_id in evidence:
        by_identity[(identity_id, user_id)].add(source_instance_id)
    return identities, by_identity


def _raise_for_invalid_backfill(
    identities: list[tuple[object, object]],
    evidence: dict[tuple[object, object], set[object]],
) -> None:
    orphan_count = sum((identity_id, user_id) not in evidence for identity_id, user_id in identities)
    ambiguous_count = sum(
        len(evidence.get((identity_id, user_id), ())) > 1
        for identity_id, user_id in identities
    )
    if orphan_count or ambiguous_count:
        raise RuntimeError(
            "nutrition identity source backfill aborted: "
            f"{orphan_count} orphan identities, {ambiguous_count} cross-source identities"
        )


def _backfill_source_instances(bind: sa.Connection) -> None:
    identities, evidence = _identity_source_evidence(bind)
    _raise_for_invalid_backfill(identities, evidence)
    op.add_column(_TABLE, sa.Column("source_instance_id", sa.Uuid(), nullable=True))
    identity_table = sa.table(
        _TABLE,
        sa.column("id"),
        sa.column("user_id"),
        sa.column("source_instance_id"),
    )
    for identity_id, user_id in identities:
        source_instance_id = next(iter(evidence[(identity_id, user_id)]))
        bind.execute(
            identity_table.update()
            .where(identity_table.c.id == identity_id)
            .where(identity_table.c.user_id == user_id)
            .values(source_instance_id=source_instance_id)
        )
    missing_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM nutrition_external_identities "
            "WHERE source_instance_id IS NULL"
        )
    ).scalar_one()
    if missing_count:
        raise RuntimeError(
            f"nutrition identity source backfill aborted: {missing_count} rows remain NULL"
        )


def _drop_lookup_index(bind: sa.Connection) -> None:
    index_names = {index["name"] for index in sa.inspect(bind).get_indexes(_TABLE)}
    if _OLD_LOOKUP_INDEX in index_names:
        op.drop_index(_OLD_LOOKUP_INDEX, table_name=_TABLE)
    if _LOOKUP_INDEX in index_names:
        op.drop_index(_LOOKUP_INDEX, table_name=_TABLE)


def upgrade() -> None:
    bind = op.get_bind()
    if _source_scoped_schema_status(bind):
        return
    _backfill_source_instances(bind)
    _drop_lookup_index(bind)
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(_TABLE, recreate="always") as batch:
            batch.drop_constraint(_OLD_UNIQUE, type_="unique")
            batch.alter_column(
                "source_instance_id",
                existing_type=sa.Uuid(),
                nullable=False,
            )
            batch.create_unique_constraint(
                _NEW_UNIQUE,
                ["user_id", "source_instance_id", "provider_key", "namespace", "identity_value"],
            )
    else:
        op.drop_constraint(_OLD_UNIQUE, table_name=_TABLE, type_="unique")
        op.alter_column(
            _TABLE,
            "source_instance_id",
            existing_type=sa.Uuid(),
            nullable=False,
        )
        op.create_unique_constraint(
            _NEW_UNIQUE,
            _TABLE,
            ["user_id", "source_instance_id", "provider_key", "namespace", "identity_value"],
        )
    op.create_index(
        _LOOKUP_INDEX,
        _TABLE,
        ["user_id", "source_instance_id", "provider_key", "namespace"],
    )


def _downgrade_conflict_count(bind: sa.Connection) -> int:
    return int(
        bind.execute(
            sa.text(
                """
                SELECT COUNT(*)
                FROM (
                    SELECT user_id, provider_key, namespace, identity_value
                    FROM nutrition_external_identities
                    GROUP BY user_id, provider_key, namespace, identity_value
                    HAVING COUNT(*) > 1
                ) AS conflicts
                """
            )
        ).scalar_one()
    )


def downgrade() -> None:
    bind = op.get_bind()
    conflict_count = _downgrade_conflict_count(bind)
    if conflict_count:
        raise RuntimeError(
            "nutrition identity source downgrade aborted: "
            f"{conflict_count} unscoped identity collisions"
        )
    _drop_lookup_index(bind)
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(_TABLE, recreate="always") as batch:
            batch.drop_constraint(_NEW_UNIQUE, type_="unique")
            batch.drop_column("source_instance_id")
            batch.create_unique_constraint(
                _OLD_UNIQUE,
                ["user_id", "provider_key", "namespace", "identity_value"],
            )
    else:
        op.drop_constraint(_NEW_UNIQUE, table_name=_TABLE, type_="unique")
        op.drop_column(_TABLE, "source_instance_id")
        op.create_unique_constraint(
            _OLD_UNIQUE,
            _TABLE,
            ["user_id", "provider_key", "namespace", "identity_value"],
        )
    op.create_index(
        _OLD_LOOKUP_INDEX,
        _TABLE,
        ["user_id", "provider_key", "namespace"],
    )
