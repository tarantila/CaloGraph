from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import event, inspect
from sqlalchemy.exc import IntegrityError

from app.models import NutritionTarget, NutritionTargetActivitySource
from app.source_priority.application import create_policy_with_rules
from app.source_priority.contracts import PriorityRuleSpec
from app.source_priority.models import SourcePriorityRule

_REVISION_PATH = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "20260918_0031_provider_priority_and_activity_snapshots.py"
)
_REVISION_ID = "20260918_0031"
_MIGRATION_AT = datetime(2026, 9, 18, tzinfo=UTC)


def _revision_module():
    spec = importlib.util.spec_from_file_location("provider_priority_revision", _REVISION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sqlite_engine(tmp_path):
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'provider-priority.sqlite'}")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    return engine


def _legacy_metadata():
    metadata = sa.MetaData()
    uuid_type = sa.Uuid(as_uuid=True)
    sa.Table("users", metadata, sa.Column("id", uuid_type, primary_key=True))
    sa.Table(
        "nutrition_targets",
        metadata,
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("user_id", uuid_type, nullable=False),
        sa.Column("activity_source_type", sa.String(64)),
    )
    sa.Table(
        "source_priority_policies",
        metadata,
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("user_id", uuid_type, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Table(
        "source_priority_rules",
        metadata,
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("user_id", uuid_type, nullable=False),
        sa.Column("policy_id", uuid_type, nullable=False),
        sa.Column("data_area", sa.String(64), nullable=False),
        sa.Column("metric_key", sa.String(128)),
        sa.Column("provider_key", sa.String(64), nullable=False),
        sa.Column("priority_rank", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Table(
        "user_provider_preferences",
        metadata,
        sa.Column("user_id", uuid_type, nullable=False),
        sa.Column("data_area", sa.String(64), nullable=False),
        sa.Column("provider_key", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "data_area"),
    )
    return metadata


def _apply(engine, revision, operation: str) -> None:
    with engine.begin() as connection, Operations.context(
        MigrationContext.configure(connection=connection)
    ):
        getattr(revision, operation)()


def _seed_legacy(engine):
    metadata = _legacy_metadata()
    metadata.create_all(engine)
    users = metadata.tables["users"]
    targets = metadata.tables["nutrition_targets"]
    preferences = metadata.tables["user_provider_preferences"]
    user_one = uuid4()
    user_two = uuid4()
    target_one = uuid4()
    target_two = uuid4()
    target_three = uuid4()
    now = datetime(2026, 9, 17, tzinfo=UTC)
    with engine.begin() as connection:
        connection.execute(users.insert(), [{"id": user_one}, {"id": user_two}])
        connection.execute(
            targets.insert(),
            [
                {"id": target_one, "user_id": user_one, "activity_source_type": "apple_health_xml"},
                {"id": target_two, "user_id": user_one, "activity_source_type": "unknown_legacy_source"},
                {"id": target_three, "user_id": user_two, "activity_source_type": None},
            ],
        )
        connection.execute(
            preferences.insert(),
            [
                {"user_id": user_one, "data_area": "nutrition", "provider_key": "yazio", "created_at": now, "updated_at": now},
                {"user_id": user_one, "data_area": "weight", "provider_key": "apple_health", "created_at": now, "updated_at": now},
                {"user_id": user_two, "data_area": "activity_energy", "provider_key": "google_health", "created_at": now, "updated_at": now},
            ],
        )
    return user_one, user_two, target_one, target_two


def test_revision_is_linked_to_0030():
    revision = _revision_module()
    assert revision.revision == _REVISION_ID
    assert revision.down_revision == "20260911_0030"


def test_upgrade_migrates_preferences_and_activity_snapshots(tmp_path):
    engine = _sqlite_engine(tmp_path)
    user_one, user_two, _, _ = _seed_legacy(engine)

    _apply(engine, _revision_module(), "upgrade")

    inspector = inspect(engine)
    assert "nutrition_target_activity_sources" in inspector.get_table_names()
    assert "user_provider_preferences" not in inspector.get_table_names()
    assert "user_provider_priorities" in inspector.get_table_names()
    with engine.connect() as connection:
        activity_rows = connection.execute(
            sa.text(
                "SELECT target_id, user_id, priority, provider_key, source_type "
                "FROM nutrition_target_activity_sources ORDER BY target_id"
            )
        ).all()
        assert {(row[2], row[3], row[4]) for row in activity_rows} == {
            (1, "apple_health", "apple_health_xml"),
            (1, None, "unknown_legacy_source"),
        }

        priorities = connection.execute(
            sa.text(
                "SELECT user_id, data_area, priority, provider_key "
                "FROM user_provider_priorities ORDER BY user_id, data_area"
            )
        ).all()
        assert {(str(row[0]), row[1], row[2], row[3]) for row in priorities} == {
            (user_one.hex, "nutrition", 1, "yazio"),
            (user_one.hex, "weight", 1, "apple_health"),
            (user_two.hex, "activity_energy", 1, "google_health"),
        }
        assert connection.execute(
            sa.text("SELECT COUNT(*) FROM source_priority_policies")
        ).scalar_one() == 0
        assert connection.execute(
            sa.text("SELECT COUNT(*) FROM source_priority_rules")
        ).scalar_one() == 0


def test_upgrade_preserves_existing_source_priority_policy_without_mixing_user_preferences(
    tmp_path,
):
    engine = _sqlite_engine(tmp_path)
    user_one, _, _, _ = _seed_legacy(engine)
    legacy_metadata = _legacy_metadata()
    policies = legacy_metadata.tables["source_priority_policies"]
    rules = legacy_metadata.tables["source_priority_rules"]
    existing_policy = uuid4()
    with engine.begin() as connection:
        connection.execute(
            policies.insert().values(
                id=existing_policy,
                user_id=user_one,
                version=4,
                effective_from=datetime(2026, 9, 1, tzinfo=UTC),
                created_at=datetime(2026, 9, 1, tzinfo=UTC),
            )
        )
        connection.execute(
            rules.insert().values(
                id=uuid4(),
                user_id=user_one,
                policy_id=existing_policy,
                data_area="nutrition",
                metric_key="protein_g",
                provider_key="yazio",
                priority_rank=1,
                created_at=datetime(2026, 9, 1, tzinfo=UTC),
            )
        )

    _apply(engine, _revision_module(), "upgrade")

    with engine.connect() as connection:
        assert connection.execute(
            sa.select(sa.func.count()).select_from(policies)
        ).scalar_one() == 1
        assert connection.execute(
            sa.select(policies.c.version).where(policies.c.id == existing_policy)
        ).scalar_one() == 4
        assert connection.execute(
            sa.select(sa.func.count()).select_from(rules)
        ).scalar_one() == 1
        assert connection.execute(
            sa.text(
                "SELECT provider_key FROM user_provider_priorities "
                "WHERE user_id = :user_id AND data_area = 'nutrition'"
            ),
            {"user_id": user_one.hex},
        ).scalar_one() == "yazio"


def test_activity_snapshot_constraints_and_target_user_ownership(tmp_path):
    engine = _sqlite_engine(tmp_path)
    _, _, target_one, _ = _seed_legacy(engine)
    _apply(engine, _revision_module(), "upgrade")
    reflected = sa.MetaData()
    reflected.reflect(engine, only=["nutrition_target_activity_sources"])
    sources = reflected.tables["nutrition_target_activity_sources"]
    other_user = uuid4()
    with engine.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(
                sources.insert().values(
                    target_id=target_one.hex,
                    user_id=other_user.hex,
                    priority=2,
                    provider_key="other",
                    source_type="other",
                )
            )
        with pytest.raises(IntegrityError):
            connection.execute(
                sources.insert().values(
                    target_id=target_one.hex,
                    user_id=uuid4().hex,
                    priority=0,
                    provider_key=None,
                    source_type="",
                )
            )


def test_downgrade_refuses_lossy_multiple_current_priorities(tmp_path):
    engine = _sqlite_engine(tmp_path)
    user_one, _, _, _ = _seed_legacy(engine)
    _apply(engine, _revision_module(), "upgrade")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO user_provider_priorities "
                "(user_id, data_area, priority, provider_key, created_at, updated_at) "
                "VALUES (:user_id, 'nutrition', 2, 'google_health', :created_at, :updated_at)"
            ),
            {
                "user_id": user_one.hex,
                "created_at": _MIGRATION_AT,
                "updated_at": _MIGRATION_AT,
            },
        )

    with pytest.raises(RuntimeError, match="multiple current priorities"):
        _apply(engine, _revision_module(), "downgrade")


def test_downgrade_recreates_preferences_for_one_wildcard_rule_per_area(tmp_path):
    engine = _sqlite_engine(tmp_path)
    user_one, _, _, _ = _seed_legacy(engine)
    _apply(engine, _revision_module(), "upgrade")
    _apply(engine, _revision_module(), "downgrade")

    inspector = inspect(engine)
    assert "nutrition_target_activity_sources" not in inspector.get_table_names()
    assert "user_provider_preferences" in inspector.get_table_names()
    with engine.connect() as connection:
        rows = connection.execute(
            sa.text(
                "SELECT data_area, provider_key FROM user_provider_preferences "
                "WHERE user_id = :user_id ORDER BY data_area"
            ),
            {"user_id": user_one.hex},
        ).all()
        assert rows == [("nutrition", "yazio"), ("weight", "apple_health")]


def test_wildcard_rules_are_supported_for_all_priority_areas_without_changing_metric_precedence(db, user):
    snapshot = create_policy_with_rules(
        db,
        user.id,
        1,
        _MIGRATION_AT,
        (
            PriorityRuleSpec("nutrition", "protein_g", "yazio", 1),
            PriorityRuleSpec("nutrition", None, "google_health", 1),
            PriorityRuleSpec("weight", None, "apple_health", 1),
            PriorityRuleSpec("activity_energy", None, "yazio", 1),
        ),
    )
    persisted = db.query(SourcePriorityRule).filter(SourcePriorityRule.policy_id == snapshot.policy_id).all()
    assert {(rule.data_area, rule.metric_key, rule.provider_key) for rule in persisted} == {
        ("nutrition", "protein_g", "yazio"),
        ("nutrition", None, "google_health"),
        ("weight", None, "apple_health"),
        ("activity_energy", None, "yazio"),
    }


def test_activity_snapshot_model_defines_relationship_and_composite_scope():
    assert NutritionTargetActivitySource.__tablename__ == "nutrition_target_activity_sources"
    foreign_keys = NutritionTargetActivitySource.__table__.foreign_key_constraints
    assert any(
        tuple(constraint.column_keys) == ("target_id", "user_id")
        and tuple(element.target_fullname for element in constraint.elements)
        == ("nutrition_targets.id", "nutrition_targets.user_id")
        for constraint in foreign_keys
    )
    assert {column.name for column in NutritionTargetActivitySource.__table__.primary_key.columns} == {
        "target_id",
        "priority",
    }
    assert NutritionTarget.activity_sources.property.back_populates == "target"
