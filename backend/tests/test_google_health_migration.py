from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

from app.database import Base

_REVISION_ID = "20260920_0033"
_REVISION_PATH = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "20260920_0033_google_health_user_credentials.py"
)
_BASE_REVISION_PATH = (
    Path(__file__).parents[1] / "alembic" / "versions" / "20260910_0029_google_health_oauth.py"
)
_GOOGLE_TABLES = {"google_health_connections", "google_health_oauth_flows"}
_NUTRITION_TABLE_PREFIX = "nutrition_"


def _revision_module(path: Path = _REVISION_PATH):
    spec = importlib.util.spec_from_file_location("google_health_revision", path)
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
                (column["name"], str(column["type"]), column["nullable"])
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
    _apply(engine, _revision_module(_BASE_REVISION_PATH), "upgrade")
    return engine


def _insert_legacy_connection(engine, *, token: bytes = b"legacy-encrypted-refresh-token") -> None:
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO google_health_connections "
                "(id, user_id, encrypted_refresh_token, granted_scopes, state, created_at, updated_at) "
                "VALUES (:id, :user_id, :token, :scopes, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "user_id": "00000000-0000-0000-0000-000000000002",
                "token": token,
                "scopes": "[]",
            },
        )


def test_revision_is_linked_to_0032_and_migration_chain_has_current_head() -> None:
    revision = _revision_module()
    assert revision.revision == _REVISION_ID
    assert revision.down_revision == "20260920_0032"
    assert revision.branch_labels is None
    assert revision.depends_on is None

    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    assert ScriptDirectory.from_config(config).get_heads() == [_REVISION_ID]


def test_upgrade_preserves_tokens_marks_rows_for_reauthentication_and_adds_contract(tmp_path) -> None:
    engine = _sqlite_baseline(tmp_path)
    token = b"opaque-encrypted-refresh-token"
    _insert_legacy_connection(engine, token=token)

    _apply(engine, _revision_module(), "upgrade")

    inspector = inspect(engine)
    columns = {column["name"]: column for column in inspector.get_columns("google_health_connections")}
    assert columns["client_id"]["nullable"] is True
    assert columns["encrypted_client_secret"]["nullable"] is True
    assert columns["encrypted_refresh_token"]["nullable"] is True
    assert str(columns["sync_state"]["default"]).strip("'") == "idle"
    assert str(columns["retry_attempt"]["default"]).strip("'") == "0"
    assert str(columns["retry_max_attempts"]["default"]).strip("'") == "3"
    checks = {
        constraint["name"]: constraint["sqltext"]
        for constraint in inspector.get_check_constraints("google_health_connections")
    }
    assert set(checks) == {
        "ck_google_health_connections_state",
        "ck_google_health_connections_sync_state",
    }
    assert "not_connected" in checks["ck_google_health_connections_state"]
    assert "completed" in checks["ck_google_health_connections_sync_state"]
    assert {
        index["name"] for index in inspector.get_indexes("google_health_connections")
    } == {"ix_google_health_connections_user_id"}
    assert {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("google_health_connections")
    } == {"uq_google_health_connections_user_id"}

    with engine.connect() as connection:
        row = connection.execute(
            sa.text(
                "SELECT encrypted_refresh_token, state FROM google_health_connections "
                "WHERE id = '00000000-0000-0000-0000-000000000001'"
            )
        ).one()
    assert row.encrypted_refresh_token == token
    assert row.state == "reauth_required"


def test_upgrade_allows_credential_only_not_connected_rows(tmp_path) -> None:
    engine = _sqlite_baseline(tmp_path)
    _apply(engine, _revision_module(), "upgrade")

    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO google_health_connections "
                "(id, user_id, client_id, encrypted_client_secret, encrypted_refresh_token, "
                "granted_scopes, state, created_at, updated_at) "
                "VALUES (:id, :user_id, :client_id, :secret, NULL, :scopes, 'not_connected', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {
                "id": "00000000-0000-0000-0000-000000000003",
                "user_id": "00000000-0000-0000-0000-000000000004",
                "client_id": "synthetic-client-id",
                "secret": b"synthetic-encrypted-secret",
                "scopes": "[]",
            },
        )

    with engine.connect() as connection:
        row = connection.execute(
            sa.text(
                "SELECT client_id, encrypted_client_secret, encrypted_refresh_token, state "
                "FROM google_health_connections WHERE id = '00000000-0000-0000-0000-000000000003'"
            )
        ).one()
    assert row.client_id == "synthetic-client-id"
    assert row.encrypted_client_secret == b"synthetic-encrypted-secret"
    assert row.encrypted_refresh_token is None
    assert row.state == "not_connected"


def test_downgrade_rejects_rows_that_would_lose_null_refresh_tokens(tmp_path) -> None:
    engine = _sqlite_baseline(tmp_path)
    _apply(engine, _revision_module(), "upgrade")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO google_health_connections "
                "(id, user_id, client_id, encrypted_client_secret, encrypted_refresh_token, "
                "granted_scopes, state, created_at, updated_at) VALUES "
                "('00000000-0000-0000-0000-000000000003', "
                "'00000000-0000-0000-0000-000000000004', 'synthetic-client-id', "
                ":secret, NULL, '[]', 'not_connected', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"secret": b"synthetic-encrypted-secret"},
        )

    with pytest.raises(RuntimeError, match=r"null.*refresh token"):
        _apply(engine, _revision_module(), "downgrade")

def test_downgrade_restores_legacy_schema_without_rewriting_tokens(tmp_path) -> None:
    engine = _sqlite_baseline(tmp_path)
    token = b"opaque-encrypted-refresh-token"
    _insert_legacy_connection(engine, token=token)
    _apply(engine, _revision_module(), "upgrade")
    _apply(engine, _revision_module(), "downgrade")

    inspector = inspect(engine)
    columns = {column["name"]: column for column in inspector.get_columns("google_health_connections")}
    assert "client_id" not in columns
    assert "encrypted_client_secret" not in columns
    assert "sync_state" not in columns
    assert "retry_attempt" not in columns
    assert "retry_max_attempts" not in columns
    assert "next_retry_at" not in columns
    assert "last_error_category" not in columns
    assert columns["encrypted_refresh_token"]["nullable"] is False
    checks = {
        constraint["name"]: constraint["sqltext"]
        for constraint in inspector.get_check_constraints("google_health_connections")
    }
    assert set(checks) == {"ck_google_health_connections_state"}
    assert "not_connected" not in checks["ck_google_health_connections_state"]

    with engine.connect() as connection:
        row = connection.execute(
            sa.text(
                "SELECT encrypted_refresh_token, state FROM google_health_connections "
                "WHERE id = '00000000-0000-0000-0000-000000000001'"
            )
        ).one()
    assert row.encrypted_refresh_token == token
    assert row.state == "reauth_required"


def test_upgrade_and_downgrade_leave_non_google_tables_unchanged(tmp_path) -> None:
    engine = _sqlite_baseline(tmp_path)
    inspector = inspect(engine)
    nutrition_tables = {
        table for table in inspector.get_table_names() if table.startswith(_NUTRITION_TABLE_PREFIX)
    }
    before = _schema_signature(engine, nutrition_tables)

    _apply(engine, _revision_module(), "upgrade")
    _apply(engine, _revision_module(), "downgrade")

    assert _schema_signature(engine, nutrition_tables) == before
