from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect

from app.database import Base

_REVISION_PATH = Path(__file__).parents[1] / "alembic" / "versions" / "20260908_0025_nutrition_domain_a1.py"


def _revision_module():
    spec = importlib.util.spec_from_file_location("nutrition_a1_revision", _REVISION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a1_migration_creates_and_drops_only_nutrition_tables(tmp_path):
    database = tmp_path / "nutrition-a1.sqlite"
    engine = create_engine(f"sqlite+pysqlite:///{database}")
    a1_tables = {
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
    existing_tables = [table for table in Base.metadata.sorted_tables if table.name not in a1_tables]
    Base.metadata.create_all(engine, tables=existing_tables)
    revision = _revision_module()

    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection=connection)):
            revision.upgrade()
        created = set(inspect(connection).get_table_names())
        assert a1_tables <= created
        assert not {"nutrition_source_priority_rules", "nutrition_projections"} & created

    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection=connection)):
            revision.downgrade()
        remaining = set(inspect(connection).get_table_names())
        assert not a1_tables & remaining
        assert "users" in remaining
