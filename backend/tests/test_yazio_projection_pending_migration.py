from __future__ import annotations

import importlib.util
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.database import Base

REVISION_FILE = "20261021_0040_yazio_projection_pending.py"
TABLE_NAME = "nutrition_ingestion_runs"
INDEX_NAME = "ix_nutrition_ingestion_runs_projection_pending"
INDEX_COLUMNS = ["user_id", "provider_key", "source_instance_id", "created_at", "id"]
COMPATIBILITY_TABLES = ("import_batches", "health_samples")
EVIDENCE_TABLES = ("nutrition_source_observations", "nutrition_provenance")


def _revision(filename=REVISION_FILE):
    path = Path(__file__).parents[1] / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _apply(engine, operation="upgrade"):
    with engine.begin() as connection, Operations.context(
        MigrationContext.configure(connection=connection)
    ):
        getattr(_revision(), operation)()


def _legacy_tables():
    # Frozen pre-0040 columns; never derive the legacy schema from the new model.
    metadata = sa.MetaData()
    sa.Table(
        TABLE_NAME,
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider_key", sa.String(64), nullable=False),
        # Permit malformed historical evidence so the backfill's guard is tested.
        sa.Column("source_instance_id", sa.Uuid()),
        sa.Column("connector_variant", sa.String(64)),
        sa.Column("requested_start_date", sa.Date()),
        sa.Column("requested_end_date", sa.Date()),
        sa.Column("covered_start_date", sa.Date()),
        sa.Column("covered_end_date", sa.Date()),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("coverage_state", sa.String(16), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Index("ix_nutrition_ingestion_runs_user_provider", "user_id", "provider_key"),
    )
    # Synthetic persisted evidence/compatibility sentinels, independent of ORM
    # defaults and containing provenance that must survive both DDL directions.
    for name in COMPATIBILITY_TABLES + EVIDENCE_TABLES:
        sa.Table(
            name,
            metadata,
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("user_id", sa.Uuid(), nullable=False),
            sa.Column("metadata", sa.JSON(), nullable=False),
        )
    return metadata.tables


def _seed(engine):
    tables = _legacy_tables()
    next(iter(tables.values())).metadata.create_all(engine)
    now = datetime(2026, 10, 20, 12, tzinfo=UTC)
    user_id, other_user_id = uuid4(), uuid4()
    source_id = uuid4()
    cases = [
        ({}, True),
        ({"requested_end_date": date(2026, 10, 1)}, True),  # single day
        ({"user_id": other_user_id, "source_instance_id": uuid4()}, True),
        ({"metadata": {"projection_status": "succeeded"}}, True),
        ({"finished_at": None, "coverage_state": "unknown"}, True),
        ({"provider_key": "apple_health"}, False),
        ({"provider_key": "google_health"}, False),
        ({"connector_variant": "legacy-v15"}, False),
        ({"connector_variant": "file-import"}, False),
        ({"connector_variant": "unknown"}, False),
        ({"connector_variant": "sdk-v23"}, False),
        ({"connector_variant": None}, False),
        ({"status": "pending"}, False),
        ({"status": "running"}, False),
        ({"status": "partial"}, False),
        ({"status": "failed"}, False),
        ({"source_instance_id": None}, False),
        ({"requested_start_date": None}, False),
        ({"requested_end_date": None}, False),
        ({"requested_start_date": date(2026, 10, 21)}, False),
    ]
    eligible_sdk_run_ids = set()
    with engine.begin() as connection:
        for changes, eligible in cases:
            run_id = uuid4()
            values = {
                "id": run_id,
                "user_id": user_id,
                "provider_key": "yazio",
                "source_instance_id": source_id,
                "connector_variant": "sdk-v22",
                "requested_start_date": date(2026, 10, 1),
                "requested_end_date": date(2026, 10, 20),
                "covered_start_date": date(2026, 10, 2),
                "covered_end_date": date(2026, 10, 19),
                "status": "completed",
                "coverage_state": "complete",
                "metadata": {"connector": "historical", "projection_status": "unknown"},
                "started_at": now,
                "finished_at": now,
                "created_at": now,
                **changes,
            }
            connection.execute(tables[TABLE_NAME].insert().values(**values))
            if eligible:
                eligible_sdk_run_ids.add(run_id)
        for name in COMPATIBILITY_TABLES + EVIDENCE_TABLES:
            for owner in (user_id, other_user_id):
                connection.execute(tables[name].insert().values(
                    id=uuid4(), user_id=owner,
                    metadata={"provider": "yazio", "connector_variant": "legacy", "file": "synthetic"},
                ))
    return eligible_sdk_run_ids


def _snapshot(engine, table_names):
    with engine.connect() as connection:
        return {
            name: [dict(row) for row in connection.execute(
                sa.select(table).order_by(table.c.id)
            ).mappings()]
            for name in table_names
            for table in [sa.Table(name, sa.MetaData(), autoload_with=connection)]
        }


def _old_columns(engine):
    rows = _snapshot(engine, (TABLE_NAME,))[TABLE_NAME]
    return [{key: value for key, value in row.items() if key != "projection_pending"} for row in rows]


def _assert_pending_schema(engine):
    inspector = sa.inspect(engine)
    columns = inspector.get_columns(TABLE_NAME)
    column_names = [column["name"] for column in columns]
    indexes = inspector.get_indexes(TABLE_NAME)
    index_names = [index["name"] for index in indexes]
    assert column_names.count("projection_pending") == 1
    assert index_names.count(INDEX_NAME) == 1
    pending_column = next(column for column in columns if column["name"] == "projection_pending")
    assert pending_column["nullable"] is False
    assert pending_column["default"] is not None
    index = next(index for index in indexes if index["name"] == INDEX_NAME)
    assert index["column_names"] == INDEX_COLUMNS
    predicate = str(index["dialect_options"][f"{engine.dialect.name}_where"])
    assert "projection_pending" in predicate
    assert "true" in predicate.lower()


@pytest.fixture
def migration_engine(tmp_path):
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'pending.sqlite'}")
    try:
        yield engine
    finally:
        engine.dispose()


def test_upgrade_backfills_only_completed_canonical_sdk_ranges(migration_engine):
    eligible_sdk_run_ids = _seed(migration_engine)
    old_columns_before = _old_columns(migration_engine)
    compatibility_rows_before = _snapshot(migration_engine, COMPATIBILITY_TABLES + EVIDENCE_TABLES)

    _apply(migration_engine)

    with migration_engine.connect() as connection:
        table = sa.Table(TABLE_NAME, sa.MetaData(), autoload_with=connection)
        pending_ids = {
            UUID(str(run_id))
            for run_id in connection.scalars(
                sa.select(table.c.id).where(table.c.projection_pending.is_(True))
            )
        }
    assert pending_ids == eligible_sdk_run_ids
    old_columns_after = _old_columns(migration_engine)
    compatibility_rows_after = _snapshot(migration_engine, COMPATIBILITY_TABLES + EVIDENCE_TABLES)
    assert old_columns_after == old_columns_before
    assert compatibility_rows_after == compatibility_rows_before
    _assert_pending_schema(migration_engine)


@pytest.mark.parametrize("existing_column", [False, True])
def test_existing_schema_upgrade_adds_column_and_index_once(migration_engine, existing_column):
    _seed(migration_engine)
    if existing_column:
        with migration_engine.begin() as connection:
            connection.exec_driver_sql(
                "ALTER TABLE nutrition_ingestion_runs ADD COLUMN projection_pending BOOLEAN NOT NULL DEFAULT false"
            )
    _apply(migration_engine)
    _apply(migration_engine)
    _assert_pending_schema(migration_engine)


def test_downgrade_preserves_evidence_and_compatibility_rows(migration_engine):
    _seed(migration_engine)
    old_columns_before = _old_columns(migration_engine)
    compatibility_rows_before = _snapshot(migration_engine, COMPATIBILITY_TABLES + EVIDENCE_TABLES)
    indexes_before = sa.inspect(migration_engine).get_indexes(TABLE_NAME)
    _apply(migration_engine)

    _apply(migration_engine, "downgrade")

    assert "projection_pending" not in {
        column["name"] for column in sa.inspect(migration_engine).get_columns(TABLE_NAME)
    }
    assert sa.inspect(migration_engine).get_indexes(TABLE_NAME) == indexes_before
    old_columns_after = _old_columns(migration_engine)
    compatibility_rows_after = _snapshot(migration_engine, COMPATIBILITY_TABLES + EVIDENCE_TABLES)
    assert old_columns_after == old_columns_before
    assert compatibility_rows_after == compatibility_rows_before


def test_fresh_nutrition_chain_has_one_pending_column_and_index(migration_engine):
    filenames = (
        "20260908_0025_nutrition_domain_a1.py",
        "20260908_0026_nutrition_domain_a2.py",
        "20260909_0027_nutrition_provenance_identity.py",
        "20260910_0028_nutrition_identity_source_scope.py",
        REVISION_FILE,
    )
    revisions = [_revision(filename) for filename in filenames]
    nutrition_tables = set(revisions[0]._A1_TABLES) | set(revisions[1]._A2_TABLES)
    prerequisites = [table for table in Base.metadata.sorted_tables if table.name not in nutrition_tables]
    Base.metadata.create_all(migration_engine, tables=prerequisites)

    for revision in revisions:
        with migration_engine.begin() as connection, Operations.context(
            MigrationContext.configure(connection=connection)
        ):
            revision.upgrade()
        if revision.revision == "20260908_0025":
            # Specifically exercise the live-metadata trap before 0040 runs.
            _assert_pending_schema(migration_engine)

    assert revisions[-1].revision == "20261021_0040"
    assert revisions[-1].down_revision == "20261006_0039"
    _assert_pending_schema(migration_engine)
