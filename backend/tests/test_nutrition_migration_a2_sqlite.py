from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event, inspect

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


def _revision(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sqlite_engine(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'nutrition-a2.sqlite'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def _apply_revision(engine, revision):
    with engine.begin() as connection, Operations.context(
        MigrationContext.configure(connection=connection)
    ):
        revision.upgrade()


def test_a2_migration_is_additive_and_preserves_a1(tmp_path):
    engine = _sqlite_engine(tmp_path)
    existing_tables = [
        table
        for table in Base.metadata.sorted_tables
        if table.name not in _A1_TABLES | _A2_TABLES
    ]
    Base.metadata.create_all(engine, tables=existing_tables)

    a1 = _revision(_A1_REVISION_PATH, "nutrition_a1_for_a2")
    a2 = _revision(_A2_REVISION_PATH, "nutrition_a2")
    _apply_revision(engine, a1)
    _apply_revision(engine, a2)

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert tables >= _A1_TABLES
    assert tables >= _A2_TABLES

    indexes = {item["name"] for item in inspector.get_indexes("source_priority_rules")}
    assert {
        "uq_source_priority_rules_wildcard_provider",
        "uq_source_priority_rules_wildcard_rank",
        "uq_source_priority_rules_metric_provider",
        "uq_source_priority_rules_metric_rank",
    } <= indexes

    projection_fks = {
        item["name"] for item in inspector.get_foreign_keys("nutrition_projection_heads")
    }
    assert "fk_nutrition_projection_heads_projection_scope" in projection_fks

    with engine.begin() as connection, Operations.context(
        MigrationContext.configure(connection=connection)
    ):
        a2.downgrade()

    remaining = set(inspect(engine).get_table_names())
    assert remaining >= _A1_TABLES
    assert not _A2_TABLES & remaining
    engine.dispose()
def test_a3_migration_recreates_provenance_indexes_after_a2(tmp_path):
    engine = _sqlite_engine(tmp_path)
    existing_tables = [
        table
        for table in Base.metadata.sorted_tables
        if table.name not in _A1_TABLES | _A2_TABLES
    ]
    Base.metadata.create_all(engine, tables=existing_tables)
    a1 = _revision(_A1_REVISION_PATH, "nutrition_a1_for_a3")
    a2 = _revision(_A2_REVISION_PATH, "nutrition_a2_for_a3")
    a3 = _revision(_A3_REVISION_PATH, "nutrition_a3")
    _apply_revision(engine, a1)
    _apply_revision(engine, a2)

    identity_indexes = {
        "uq_nutrition_provenance_event_identity",
        "uq_nutrition_provenance_snapshot_identity",
        "uq_nutrition_provenance_serving_identity",
        "uq_nutrition_provenance_field_identity",
    }
    with engine.begin() as connection:
        for name in identity_indexes | {"ix_nutrition_provenance_user_serving"}:
            connection.exec_driver_sql(f'DROP INDEX "{name}"')

    _apply_revision(engine, a3)

    indexes = {
        item["name"]: item
        for item in inspect(engine).get_indexes("nutrition_provenance")
    }
    assert identity_indexes <= indexes.keys()
    assert all(indexes[name]["unique"] for name in identity_indexes)
    assert indexes["ix_nutrition_provenance_user_serving"]["unique"] == 0
    engine.dispose()



def test_a2_sqlite_foreign_keys_are_enforced(tmp_path):
    engine = _sqlite_engine(tmp_path)
    existing_tables = [
        table
        for table in Base.metadata.sorted_tables
        if table.name not in _A1_TABLES | _A2_TABLES
    ]
    Base.metadata.create_all(engine, tables=existing_tables)
    a1 = _revision(_A1_REVISION_PATH, "nutrition_a1_for_a2_fk")
    a2 = _revision(_A2_REVISION_PATH, "nutrition_a2_fk")
    _apply_revision(engine, a1)
    _apply_revision(engine, a2)

    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1

    engine.dispose()
