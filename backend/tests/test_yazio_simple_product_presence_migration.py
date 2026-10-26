from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

REVISION_FILE = "20261026_0041_yazio_simple_product_presence.py"
TABLE_NAMES = (
    "nutrition_ingestion_runs",
    "nutrition_source_observations",
    "nutrition_consumption_events",
    "nutrition_field_observations",
)


def _revision():
    path = Path(__file__).parents[1] / "alembic" / "versions" / REVISION_FILE
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


def _tables():
    metadata = sa.MetaData()
    sa.Table(
        "nutrition_ingestion_runs",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider_key", sa.String(64), nullable=False),
        sa.Column("source_instance_id", sa.Uuid(), nullable=False),
        sa.Column("connector_variant", sa.String(64)),
    )
    sa.Table(
        "nutrition_source_observations",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("ingestion_run_id", sa.Uuid(), nullable=False),
        sa.Column("source_instance_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider_key", sa.String(64), nullable=False),
        sa.Column("connector_variant", sa.String(64)),
        sa.Column("source_namespace", sa.String(128), nullable=False),
        sa.Column("observation_kind", sa.String(32), nullable=False),
        sa.Column("source_record_id", sa.String(255)),
        sa.Column("source_revision", sa.Integer(), nullable=False),
        sa.Column("observation_fingerprint", sa.String(64), nullable=False),
        sa.Column("presence_state", sa.String(16), nullable=False),
        sa.Column("coverage_state", sa.String(16), nullable=False),
        sa.Column("resolution_state", sa.String(32), nullable=False),
        sa.Column("lineage_state", sa.String(16), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
    )
    sa.Table(
        "nutrition_consumption_events",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("source_observation_id", sa.Uuid(), nullable=False),
        sa.Column("source_instance_id", sa.Uuid(), nullable=False),
        sa.Column("provider_key", sa.String(64), nullable=False),
        sa.Column("event_kind", sa.String(32), nullable=False),
        sa.Column("logical_event_key", sa.String(255)),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 3)),
        sa.Column("amount_unit", sa.String(32)),
        sa.Column("presence_state", sa.String(16), nullable=False),
        sa.Column("coverage_state", sa.String(16), nullable=False),
        sa.Column("resolution_state", sa.String(32), nullable=False),
        sa.Column("lineage_state", sa.String(16), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
    )
    sa.Table(
        "nutrition_field_observations",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("source_observation_id", sa.Uuid(), nullable=False),
        sa.Column("provider_field_path", sa.String(255), nullable=False),
        sa.Column("metric_key", sa.String(128)),
        sa.Column("canonical_value", sa.Numeric(18, 6)),
        sa.Column("observation_role", sa.String(16), nullable=False),
        sa.Column("presence_state", sa.String(16), nullable=False),
        sa.Column("coverage_state", sa.String(16), nullable=False),
        sa.Column("resolution_state", sa.String(32), nullable=False),
        sa.Column("lineage_state", sa.String(16), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
    )
    return metadata.tables


def _seed(engine):
    tables = _tables()
    next(iter(tables.values())).metadata.create_all(engine)
    source_rows: list[dict[str, object]] = []
    event_rows: list[dict[str, object]] = []
    run_rows: list[dict[str, object]] = []
    repair_source_ids: set[UUID] = set()
    repair_event_ids: set[UUID] = set()
    # The SDK variant belongs to the owning run; source variants are absent on
    # rows produced by the real SDK ingestion path.
    cases = (
        ("target-missing", "yazio", "yazio.simple_product", "simple_product", "sdk-v22", None, "missing", "simple_product", "missing", True),
        ("target-missing-second-user", "yazio", "yazio.simple_product", "simple_product", "sdk-v22", None, "missing", "simple_product", "missing", True),
        ("target-supplied-source", "yazio", "yazio.simple_product", "simple_product", "sdk-v22", None, "supplied", "simple_product", "missing", True),
        ("other-provider", "google_health", "google_health.simple_product", "simple_product", "sdk-v22", None, "missing", "simple_product", "missing", True),
        ("other-namespace", "yazio", "yazio.consumed_item", "consumption_event", "sdk-v22", None, "missing", "product", "missing", True),
        ("other-observation-kind", "yazio", "yazio.simple_product", "consumption_event", "sdk-v22", None, "missing", "simple_product", "missing", True),
        ("legacy-run", "yazio", "yazio.simple_product", "simple_product", "legacy-v15", None, "missing", "simple_product", "missing", True),
        ("unknown-run", "yazio", "yazio.simple_product", "simple_product", None, None, "missing", "simple_product", "missing", True),
        ("legacy-source-marker", "yazio", "yazio.simple_product", "simple_product", "sdk-v22", "legacy-v15", "missing", "simple_product", "missing", True),
        ("run-source-instance-mismatch", "yazio", "yazio.simple_product", "simple_product", "sdk-v22", None, "missing", "simple_product", "missing", False),
    )
    for (
        key,
        provider,
        namespace,
        observation_kind,
        run_connector,
        source_connector,
        source_presence,
        event_kind,
        event_presence,
        run_source_matches,
    ) in cases:
        user_id = uuid4()
        source_id = uuid4()
        event_id = uuid4()
        run_id = uuid4()
        source_instance_id = uuid4()
        run_source_instance_id = source_instance_id if run_source_matches else uuid4()
        run_rows.append({
            "id": run_id,
            "user_id": user_id,
            "provider_key": provider,
            "source_instance_id": run_source_instance_id,
            "connector_variant": run_connector,
        })
        source_rows.append({
            "id": source_id,
            "ingestion_run_id": run_id,
            "user_id": user_id,
            "provider_key": provider,
            "source_instance_id": source_instance_id,
            "connector_variant": source_connector,
            "source_namespace": namespace,
            "observation_kind": observation_kind,
            "source_record_id": key,
            "source_revision": 1,
            "observation_fingerprint": ("a" if len(key) % 2 else "b") * 64,
            "presence_state": source_presence,
            "coverage_state": "complete",
            "resolution_state": "resolved",
            "lineage_state": "confirmed",
            "metadata": {"name": "Chicken", "marker": key},
        })
        event_rows.append({
            "id": event_id,
            "user_id": user_id,
            "source_observation_id": source_id,
            "source_instance_id": source_instance_id,
            "provider_key": provider,
            "event_kind": event_kind,
            "logical_event_key": key,
            "revision": 1,
            "amount": None,
            "amount_unit": None,
            "presence_state": event_presence,
            "coverage_state": "complete",
            "resolution_state": "resolved",
            "lineage_state": "confirmed",
            "metadata": {"name": "Chicken", "marker": key},
        })
        if (
            provider == "yazio"
            and namespace == "yazio.simple_product"
            and observation_kind == "simple_product"
            and run_connector == "sdk-v22"
            and source_connector in {None, "sdk-v22"}
            and run_source_matches
        ):
            if source_presence == "missing":
                repair_source_ids.add(source_id)
            if event_presence == "missing":
                repair_event_ids.add(event_id)

    fields = []
    target_source_id = source_rows[0]["id"]
    target_user_id = source_rows[0]["user_id"]
    for path, value, presence in (
        ("amount", None, "missing"),
        ("nutrients.energy", Decimal("518"), "supplied"),
        ("nutrients.protein", Decimal("10"), "supplied"),
        ("nutrients.carb", Decimal("30"), "supplied"),
        ("nutrients.fat", Decimal("13"), "supplied"),
        ("nutrients.fiber", Decimal("0"), "explicit_zero"),
    ):
        fields.append({
            "id": uuid4(),
            "user_id": target_user_id,
            "source_observation_id": target_source_id,
            "provider_field_path": path,
            "metric_key": path.removeprefix("nutrients."),
            "canonical_value": value,
            "observation_role": "provider",
            "presence_state": presence,
            "coverage_state": "complete",
            "resolution_state": "resolved",
            "lineage_state": "confirmed",
            "metadata": {"preserve": True},
        })
    with engine.begin() as connection:
        connection.execute(tables["nutrition_ingestion_runs"].insert(), run_rows)
        connection.execute(tables["nutrition_source_observations"].insert(), source_rows)
        connection.execute(tables["nutrition_consumption_events"].insert(), event_rows)
        connection.execute(tables["nutrition_field_observations"].insert(), fields)
    return repair_source_ids, repair_event_ids


def _snapshot(engine):
    with engine.connect() as connection:
        return {
            name: [
                dict(row)
                for row in connection.execute(
                    sa.select(table).order_by(table.c.id)
                ).mappings()
            ]
            for name in TABLE_NAMES
            for table in [sa.Table(name, sa.MetaData(), autoload_with=connection)]
        }


def _expected_after(before, source_ids, event_ids):
    expected = {name: [row.copy() for row in rows] for name, rows in before.items()}
    for row in expected["nutrition_source_observations"]:
        if UUID(str(row["id"])) in source_ids:
            row["presence_state"] = "supplied"
    for row in expected["nutrition_consumption_events"]:
        if UUID(str(row["id"])) in event_ids:
            row["presence_state"] = "supplied"
    return expected


@pytest.fixture
def migration_engine(tmp_path):
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'yazio-presence.sqlite'}")
    try:
        yield engine
    finally:
        engine.dispose()


def test_upgrade_repairs_only_sdk_simple_product_record_presence(migration_engine):
    source_ids, event_ids = _seed(migration_engine)
    assert len(source_ids) == 2
    assert len(event_ids) == 3
    before = _snapshot(migration_engine)
    expected = _expected_after(before, source_ids, event_ids)

    _apply(migration_engine)
    after = _snapshot(migration_engine)

    assert after == expected
    assert len(after["nutrition_source_observations"]) == len(before["nutrition_source_observations"])
    assert len(after["nutrition_consumption_events"]) == len(before["nutrition_consumption_events"])
    assert after["nutrition_field_observations"] == before["nutrition_field_observations"]

    _apply(migration_engine)
    assert _snapshot(migration_engine) == expected


def test_downgrade_does_not_restore_unrecoverable_missing_presence(migration_engine):
    source_ids, event_ids = _seed(migration_engine)
    before = _snapshot(migration_engine)
    expected = _expected_after(before, source_ids, event_ids)

    _apply(migration_engine)
    _apply(migration_engine, "downgrade")

    assert _snapshot(migration_engine) == expected
