from __future__ import annotations

import importlib.util
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect

from app.database import Base

_A1_REVISION_PATH = Path(__file__).parents[1] / "alembic" / "versions" / "20260908_0025_nutrition_domain_a1.py"
_A2_REVISION_PATH = Path(__file__).parents[1] / "alembic" / "versions" / "20260908_0026_nutrition_domain_a2.py"
_A3_REVISION_PATH = Path(__file__).parents[1] / "alembic" / "versions" / "20260909_0027_nutrition_provenance_identity.py"
_A1_TABLES = {
    "nutrition_ingestion_runs",
    "nutrition_source_observations",
    "nutrition_external_identities",
    "nutrition_external_identity_links",
    "nutrition_consumption_events",
    "nutrition_food_profiles",
    "nutrition_food_snapshots",
    "nutrition_serving_observations",
    "nutrition_field_observations",
    "nutrition_source_tombstones",
    "nutrition_provenance",
}
_A2_TABLES = {
    "source_priority_policies",
    "source_priority_rules",
    "nutrition_daily_projections",
    "nutrition_daily_projection_facts",
    "nutrition_daily_projection_lineage",
    "nutrition_projection_heads",
}

_REVISION_PATH = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "20260910_0028_nutrition_identity_source_scope.py"
)


def _revision_module():
    spec = importlib.util.spec_from_file_location("nutrition_identity_source_scope_revision", _REVISION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _legacy_tables():
    metadata = sa.MetaData()
    uuid_type = sa.Uuid(as_uuid=True)
    sa.Table(
        "nutrition_external_identities",
        metadata,
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("user_id", uuid_type, nullable=False),
        sa.Column("provider_key", sa.String, nullable=False),
        sa.Column("namespace", sa.String, nullable=False),
        sa.Column("identity_value", sa.String, nullable=False),
        sa.Column("identity_kind", sa.String, nullable=False),
        sa.UniqueConstraint(
            "user_id",
            "provider_key",
            "namespace",
            "identity_value",
            name="uq_nutrition_external_identity_value",
        ),
        sa.Index(
            "ix_nutrition_external_identities_user_namespace",
            "user_id",
            "provider_key",
            "namespace",
        ),
    )
    sa.Table(
        "nutrition_external_identity_links",
        metadata,
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("user_id", uuid_type, nullable=False),
        sa.Column("external_identity_id", uuid_type, nullable=False),
        sa.Column("consumption_event_id", uuid_type),
        sa.Column("food_profile_id", uuid_type),
        sa.Column("source_observation_id", uuid_type),
    )
    for table_name in (
        "nutrition_consumption_events",
        "nutrition_food_profiles",
        "nutrition_source_observations",
    ):
        sa.Table(
            table_name,
            metadata,
            sa.Column("id", uuid_type, primary_key=True),
            sa.Column("user_id", uuid_type, nullable=False),
            sa.Column("source_instance_id", uuid_type, nullable=False),
        )
    return metadata.tables


def _revision(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_full_migration_chain_reaches_source_scoped_identity_schema(tmp_path):
    engine = _sqlite_engine(tmp_path)
    existing_tables = [
        table
        for table in Base.metadata.sorted_tables
        if table.name not in _A1_TABLES | _A2_TABLES
    ]
    Base.metadata.create_all(engine, tables=existing_tables)
    revisions = (
        _revision(_A1_REVISION_PATH, "nutrition_a1_for_identity_scope"),
        _revision(_A2_REVISION_PATH, "nutrition_a2_for_identity_scope"),
        _revision(_A3_REVISION_PATH, "nutrition_a3_for_identity_scope"),
        _revision_module(),
    )

    for revision in revisions:
        _apply(engine, revision, "upgrade")

    columns = {
        column["name"] for column in inspect(engine).get_columns("nutrition_external_identities")
    }
    assert "source_instance_id" in columns


def _sqlite_engine(tmp_path):
    return create_engine(f"sqlite+pysqlite:///{tmp_path / 'nutrition-identity-scope.sqlite'}")


def _apply(engine, revision, operation):
    with engine.begin() as connection, Operations.context(
        MigrationContext.configure(connection=connection)
    ):
        getattr(revision, operation)()


def _seed_identity(
    engine,
    *,
    source_instances: tuple[UUID, ...] = (UUID("00000000-0000-0000-0000-000000000001"),),
    orphan: bool = False,
    identity_id: UUID | None = None,
):
    tables = _legacy_tables()
    tables_metadata = next(iter(tables.values())).metadata
    tables_metadata.create_all(engine)
    user_id = UUID("00000000-0000-0000-0000-000000000010")
    identity_id = identity_id or uuid4()
    source_id = UUID("00000000-0000-0000-0000-000000000020")
    identity = tables["nutrition_external_identities"]
    link = tables["nutrition_external_identity_links"]
    event = tables["nutrition_consumption_events"]
    with engine.begin() as connection:
        connection.execute(
            identity.insert().values(
                id=identity_id,
                user_id=user_id,
                provider_key="yazio",
                namespace="product",
                identity_value=str(identity_id),
                identity_kind="product",
            )
        )
        if not orphan:
            for source_instance_id in source_instances:
                target_id = uuid4()
                connection.execute(
                    event.insert().values(
                        id=target_id,
                        user_id=user_id,
                        source_instance_id=source_instance_id,
                    )
                )
                connection.execute(
                    link.insert().values(
                        id=uuid4(),
                        user_id=user_id,
                        external_identity_id=identity_id,
                        consumption_event_id=target_id,
                    )
                )
        else:
            assert source_id
    return identity_id, user_id


def test_identity_source_scope_revision_exists():
    revision = _revision_module()
    assert revision.revision == "20260910_0028"
    assert revision.down_revision == "20260909_0027"


def test_upgrade_backfills_single_link_source_and_preserves_ids(tmp_path):
    engine = _sqlite_engine(tmp_path)
    identity_id, user_id = _seed_identity(engine)
    revision = _revision_module()

    _apply(engine, revision, "upgrade")

    with engine.connect() as connection:
        row = connection.execute(
            sa.text(
                "SELECT id, user_id, source_instance_id FROM nutrition_external_identities"
            )
        ).one()
        assert row.id == str(identity_id).replace("-", "")
        assert row.user_id == str(user_id).replace("-", "")
        assert row.source_instance_id == "00000000000000000000000000000001"
    inspector = inspect(engine)
    unique_names = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("nutrition_external_identities")
    }
    assert "uq_nutrition_external_identity_source_value" in unique_names
    assert "uq_nutrition_external_identity_value" not in unique_names
    assert {
        index["name"] for index in inspector.get_indexes("nutrition_external_identities")
    } == {"ix_nutrition_external_identities_user_source_namespace"}


def test_upgrade_backfills_multiple_same_source_links(tmp_path):
    engine = _sqlite_engine(tmp_path)
    _seed_identity(
        engine,
        source_instances=(
            UUID("00000000-0000-0000-0000-000000000001"),
            UUID("00000000-0000-0000-0000-000000000001"),
        ),
    )
    revision = _revision_module()

    _apply(engine, revision, "upgrade")

    with engine.connect() as connection:
        assert connection.execute(
            sa.text("SELECT COUNT(*) FROM nutrition_external_identities")
        ).scalar_one() == 1


@pytest.mark.parametrize("orphan", [True])
def test_upgrade_fails_closed_for_orphan_identity(tmp_path, orphan):
    engine = _sqlite_engine(tmp_path)
    _seed_identity(engine, orphan=orphan)
    revision = _revision_module()

    with pytest.raises(RuntimeError, match="orphan identities"):
        _apply(engine, revision, "upgrade")

    assert "source_instance_id" not in {
        column["name"] for column in inspect(engine).get_columns("nutrition_external_identities")
    }


def test_upgrade_fails_closed_for_cross_source_identity(tmp_path):
    engine = _sqlite_engine(tmp_path)
    _seed_identity(
        engine,
        source_instances=(
            UUID("00000000-0000-0000-0000-000000000001"),
            UUID("00000000-0000-0000-0000-000000000002"),
        ),
    )
    revision = _revision_module()

    with pytest.raises(RuntimeError, match="cross-source identities"):
        _apply(engine, revision, "upgrade")

    assert "source_instance_id" not in {
        column["name"] for column in inspect(engine).get_columns("nutrition_external_identities")
    }


def test_upgrade_backfills_distinct_identities_without_merging(tmp_path):
    engine = _sqlite_engine(tmp_path)
    first_id, user_id = _seed_identity(engine)
    second_id = uuid4()
    tables = _legacy_tables()
    event = tables["nutrition_consumption_events"]
    identity = tables["nutrition_external_identities"]
    link = tables["nutrition_external_identity_links"]
    source_instance_id = UUID("00000000-0000-0000-0000-000000000002")
    with engine.begin() as connection:
        connection.execute(
            identity.insert().values(
                id=second_id,
                user_id=user_id,
                provider_key="yazio",
                namespace="product",
                identity_value=str(second_id),
                identity_kind="product",
            )
        )
        target_id = uuid4()
        connection.execute(
            event.insert().values(
                id=target_id,
                user_id=user_id,
                source_instance_id=source_instance_id,
            )
        )
        connection.execute(
            link.insert().values(
                id=uuid4(),
                user_id=user_id,
                external_identity_id=second_id,
                consumption_event_id=target_id,
            )
        )
    revision = _revision_module()

    _apply(engine, revision, "upgrade")

    with engine.connect() as connection:
        rows = connection.execute(
            sa.text(
                "SELECT id, source_instance_id FROM nutrition_external_identities ORDER BY id"
            )
        ).all()
        assert {UUID(row.id) for row in rows} == {first_id, second_id}
        assert len({row.source_instance_id for row in rows}) == 2


def test_upgrade_enforces_source_scoped_uniqueness(tmp_path):
    engine = _sqlite_engine(tmp_path)
    first_id, user_id = _seed_identity(engine)
    revision = _revision_module()
    _apply(engine, revision, "upgrade")
    identity = sa.table(
        "nutrition_external_identities",
        sa.column("id", sa.Uuid()),
        sa.column("user_id", sa.Uuid()),
        sa.column("source_instance_id", sa.Uuid()),
        sa.column("provider_key", sa.String),
        sa.column("namespace", sa.String),
        sa.column("identity_value", sa.String),
        sa.column("identity_kind", sa.String),
    )
    source_instance_id = UUID("00000000-0000-0000-0000-000000000002")
    with engine.begin() as connection:
        connection.execute(
            identity.insert().values(
                id=uuid4(),
                user_id=user_id,
                source_instance_id=source_instance_id,
                provider_key="yazio",
                namespace="product",
                identity_value=str(first_id),
                identity_kind="product",
            )
        )
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                identity.insert().values(
                    id=uuid4(),
                    user_id=user_id,
                    source_instance_id=source_instance_id,
                    provider_key="yazio",
                    namespace="product",
                    identity_value=str(first_id),
                    identity_kind="product",
                )
            )
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                identity.insert().values(
                    id=uuid4(),
                    user_id=user_id,
                    source_instance_id=None,
                    provider_key="yazio",
                    namespace="product",
                    identity_value=str(first_id),
                    identity_kind="product",
                )
            )


def test_downgrade_restores_unscoped_schema_without_collision(tmp_path):
    engine = _sqlite_engine(tmp_path)
    _seed_identity(engine)
    revision = _revision_module()
    _apply(engine, revision, "upgrade")

    _apply(engine, revision, "downgrade")

    columns = {column["name"] for column in inspect(engine).get_columns("nutrition_external_identities")}
    assert "source_instance_id" not in columns
    unique_names = {
        constraint["name"]
        for constraint in inspect(engine).get_unique_constraints("nutrition_external_identities")
    }
    assert "uq_nutrition_external_identity_value" in unique_names
    assert "uq_nutrition_external_identity_source_value" not in unique_names
    assert {
        index["name"] for index in inspect(engine).get_indexes("nutrition_external_identities")
    } == {"ix_nutrition_external_identities_user_namespace"}


def test_downgrade_fails_closed_for_unscoped_collision(tmp_path):
    engine = _sqlite_engine(tmp_path)
    _seed_identity(engine)
    revision = _revision_module()
    _apply(engine, revision, "upgrade")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """
                INSERT INTO nutrition_external_identities
                    (id, user_id, source_instance_id, provider_key, namespace, identity_value, identity_kind)
                SELECT lower(hex(randomblob(16))), user_id,
                       '00000000000000000000000000000002',
                       provider_key, namespace, identity_value, identity_kind
                FROM nutrition_external_identities
                """
            )
        )
    with pytest.raises(RuntimeError, match="unscoped identity collisions"):
        _apply(engine, revision, "downgrade")
    assert "source_instance_id" in {
        column["name"] for column in inspect(engine).get_columns("nutrition_external_identities")
    }
