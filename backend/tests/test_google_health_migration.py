from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

from app.database import Base

_REVISION_ID = "20260910_0029"
_REVISION_PATH = (
    Path(__file__).parents[1] / "alembic" / "versions" / "20260910_0029_google_health_oauth.py"
)
_GOOGLE_TABLES = {"google_health_connections", "google_health_oauth_flows"}
_NUTRITION_TABLE_PREFIX = "nutrition_"


def _revision_module():
    spec = importlib.util.spec_from_file_location("google_health_revision", _REVISION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _apply(engine, revision, operation: str) -> None:
    with (
        engine.begin() as connection,
        Operations.context(MigrationContext.configure(connection=connection)),
    ):
        getattr(revision, operation)()


def _schema_signature(connection, table_names: set[str]) -> dict[str, object]:
    inspector = inspect(connection)
    return {
        table: {
            "columns": [
                (
                    column["name"],
                    str(column["type"]),
                    column["nullable"],
                )
                for column in inspector.get_columns(table)
            ],
            "foreign_keys": sorted(
                [
                    (
                        fk["name"],
                        tuple(fk["constrained_columns"]),
                        tuple(fk["referred_columns"]),
                        fk.get("options", {}).get("ondelete"),
                    )
                    for fk in inspector.get_foreign_keys(table)
                ],
                key=repr,
            ),
            "indexes": sorted(
                [
                    (index["name"], tuple(index["column_names"]), index["unique"])
                    for index in inspector.get_indexes(table)
                ],
                key=repr,
            ),
            "unique_constraints": sorted(
                [
                    (constraint["name"], tuple(constraint["column_names"]))
                    for constraint in inspector.get_unique_constraints(table)
                ],
                key=repr,
            ),
            "checks": sorted(
                [
                    (constraint["name"], constraint["sqltext"])
                    for constraint in inspector.get_check_constraints(table)
                ],
                key=repr,
            ),
        }
        for table in sorted(table_names)
    }


def _sqlite_baseline(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'google-health.sqlite'}")
    existing_tables = [
        table for table in Base.metadata.sorted_tables if table.name not in _GOOGLE_TABLES
    ]
    Base.metadata.create_all(engine, tables=existing_tables)
    return engine


def test_revision_is_linked_to_0028_and_is_current_head() -> None:
    revision = _revision_module()
    assert revision.revision == _REVISION_ID
    assert revision.down_revision == "20260910_0028"
    assert revision.branch_labels is None
    assert revision.depends_on is None

    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    assert ScriptDirectory.from_config(config).get_heads() == [_REVISION_ID]


def test_upgrade_and_downgrade_only_change_google_health_tables(tmp_path) -> None:
    engine = _sqlite_baseline(tmp_path)
    inspector = inspect(engine)
    before_tables = set(inspector.get_table_names())
    nutrition_tables = {
        table for table in before_tables if table.startswith(_NUTRITION_TABLE_PREFIX)
    }
    before_nutrition_schema = _schema_signature(engine, nutrition_tables)
    revision = _revision_module()

    _apply(engine, revision, "upgrade")
    inspector = inspect(engine)
    after_upgrade_tables = set(inspector.get_table_names())
    assert after_upgrade_tables == before_tables | _GOOGLE_TABLES
    assert _schema_signature(engine, nutrition_tables) == before_nutrition_schema

    _apply(engine, revision, "downgrade")
    inspector = inspect(engine)
    assert set(inspector.get_table_names()) == before_tables
    assert _schema_signature(engine, nutrition_tables) == before_nutrition_schema


def test_upgrade_has_expected_columns_constraints_and_indexes(tmp_path) -> None:
    engine = _sqlite_baseline(tmp_path)
    _apply(engine, _revision_module(), "upgrade")
    inspector = inspect(engine)

    connection_columns = {
        column["name"]: column for column in inspector.get_columns("google_health_connections")
    }
    assert connection_columns["encrypted_refresh_token"]["nullable"] is False
    assert isinstance(connection_columns["encrypted_refresh_token"]["type"], sa.LargeBinary)
    assert connection_columns["granted_scopes"]["nullable"] is False
    assert isinstance(connection_columns["granted_scopes"]["type"], sa.JSON)
    assert connection_columns["state"]["nullable"] is False
    assert connection_columns["refresh_token_expires_at"]["nullable"] is True
    assert connection_columns["last_attempt_at"]["nullable"] is True
    assert connection_columns["last_success_at"]["nullable"] is True
    assert connection_columns["last_error"]["nullable"] is True
    assert connection_columns["last_error"]["type"].length == 128
    assert {
        fk["options"].get("ondelete")
        for fk in inspector.get_foreign_keys("google_health_connections")
    } == {"CASCADE"}
    assert {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("google_health_connections")
    } == {"uq_google_health_connections_user_id"}
    assert {
        constraint["name"]
        for constraint in inspector.get_check_constraints("google_health_connections")
    } == {"ck_google_health_connections_state"}
    assert {index["name"] for index in inspector.get_indexes("google_health_connections")} == {
        "ix_google_health_connections_user_id"
    }

    flow_columns = {
        column["name"]: column for column in inspector.get_columns("google_health_oauth_flows")
    }
    assert flow_columns["encrypted_pkce_verifier"]["nullable"] is False
    assert isinstance(flow_columns["encrypted_pkce_verifier"]["type"], sa.LargeBinary)
    assert flow_columns["state_hash"]["nullable"] is False
    assert flow_columns["state_hash"]["type"].length == 64
    assert flow_columns["expires_at"]["nullable"] is False
    assert flow_columns["consumed_at"]["nullable"] is True
    assert {
        fk["options"].get("ondelete")
        for fk in inspector.get_foreign_keys("google_health_oauth_flows")
    } == {"CASCADE"}
    assert {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("google_health_oauth_flows")
    } == {"uq_google_health_oauth_flows_state_hash"}
    assert {index["name"] for index in inspector.get_indexes("google_health_oauth_flows")} == {
        "ix_google_health_oauth_flows_user_id",
        "ix_google_health_oauth_flows_state_hash",
        "ix_google_health_oauth_flows_expires_at",
    }
    assert not (
        {column["name"] for column in inspector.get_columns("google_health_connections")}
        | {column["name"] for column in inspector.get_columns("google_health_oauth_flows")}
    ) & {
        "raw_state",
        "authorization_code",
        "auth_code",
        "access_token",
        "client_secret",
        "raw_payload",
        "nutrition_data",
    }
