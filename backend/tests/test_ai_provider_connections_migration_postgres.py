from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from alembic import command
from app.database import engine as application_engine

POSTGRES_TESTS_ENABLED = (
    os.environ.get("CALOGRAPH_ALLOW_DESTRUCTIVE_POSTGRES_TESTS") == "1"
    and bool(os.environ.get("CALOGRAPH_POSTGRES_TEST_URL"))
)
PREVIOUS_REVISION = "20261026_0041"


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_ai_provider_connections_migration_constraints_and_cascade() -> None:
    assert application_engine.dialect.name == "postgresql"
    database_url = os.environ["CALOGRAPH_POSTGRES_TEST_URL"]
    alembic_config = Config("alembic.ini")
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    engine = create_engine(database_url, pool_pre_ping=True)

    try:
        # This opt-in suite uses a disposable database and exercises the entire
        # migration chain, including the metadata-created-table path in 0025.
        command.stamp(alembic_config, "head")
        command.downgrade(alembic_config, "base")
        command.upgrade(alembic_config, "head")
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == ScriptDirectory.from_config(alembic_config).get_current_head()
            )
            assert {"ai_provider_connections", "ai_provider_credentials"} <= set(
                sa.inspect(connection).get_table_names()
            )

        owner_id, other_id = uuid4(), uuid4()
        shared_connection_id = uuid4()
        other_only_connection_id = uuid4()
        now = datetime.now(UTC)
        with engine.begin() as connection:
            for user_id in (owner_id, other_id):
                connection.execute(
                    text(
                        "INSERT INTO users "
                        "(id, username, password_hash, language, timezone, week_starts_on, "
                        "preferred_weight_unit, raw_payload_retention_days, is_active, "
                        "is_admin, created_at, updated_at) "
                        "VALUES (:id, :username, 'test-password-hash', 'de', 'Europe/Berlin', "
                        "1, 'kg', 0, true, false, :now, :now)"
                    ),
                    {"id": user_id, "username": f"ai-provider-{uuid4()}", "now": now},
                )
                connection.execute(
                    text(
                        "INSERT INTO ai_provider_connections "
                        "(user_id, id, label, provider_kind, model_id, created_at, updated_at) "
                        "VALUES (:user_id, :id, 'Test', 'openai_api', 'model-test', :now, :now)"
                    ),
                    {"user_id": user_id, "id": shared_connection_id, "now": now},
                )
                connection.execute(
                    text(
                        "INSERT INTO ai_provider_credentials "
                        "(user_id, connection_id, encrypted_api_key) "
                        "VALUES (:user_id, :connection_id, :ciphertext)"
                    ),
                    {
                        "user_id": user_id,
                        "connection_id": shared_connection_id,
                        "ciphertext": b"opaque-test-ciphertext",
                    },
                )
                connection.execute(text(
                    "INSERT INTO ai_provider_checks "
                    "(user_id, id, connection_id, configuration_version, privacy_version, started_at) "
                    "VALUES (:user_id, :id, :connection_id, 1, 'synthetic-privacy-version', :now)"
                ), {"user_id": user_id, "id": uuid4(), "connection_id": shared_connection_id, "now": now})
            connection.execute(
                text(
                    "INSERT INTO ai_provider_connections "
                    "(user_id, id, label, provider_kind, model_id, created_at, updated_at) "
                    "VALUES (:user_id, :id, 'Other only', 'openai_api', 'model-test', :now, :now)"
                ),
                {"user_id": other_id, "id": other_only_connection_id, "now": now},
            )

        with engine.connect() as connection:
            connection_row = connection.execute(
                text(
                    "SELECT enabled, timeout_seconds, configuration_version, base_url "
                    "FROM ai_provider_connections WHERE user_id = :user_id AND id = :id"
                ),
                {"user_id": owner_id, "id": shared_connection_id},
            ).one()
            assert tuple(connection_row) == (False, 60, 1, None)
            assert connection.scalar(
                text("SELECT count(*) FROM ai_provider_connections WHERE id = :id"),
                {"id": shared_connection_id},
            ) == 2
            foreign_keys = sa.inspect(connection).get_foreign_keys("ai_provider_credentials")
            assert len(foreign_keys) == 1
            assert foreign_keys[0]["constrained_columns"] == ["user_id", "connection_id"]
            assert foreign_keys[0]["referred_columns"] == ["user_id", "id"]
            assert foreign_keys[0]["options"].get("ondelete") == "CASCADE"

        with pytest.raises(sa.exc.DBAPIError), engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO ai_provider_credentials "
                    "(user_id, connection_id, encrypted_api_key) "
                    "VALUES (:user_id, :connection_id, :ciphertext)"
                ),
                {
                    "user_id": owner_id,
                    "connection_id": other_only_connection_id,
                    "ciphertext": b"opaque-test-ciphertext",
                },
            )

        invalid_connection_values = (
            {
                "label": "",
                "provider_kind": "openai_api",
                "model_id": "model-test",
                "base_url": None,
            },
            {
                "label": "Test",
                "provider_kind": "unknown",
                "model_id": "model-test",
                "base_url": None,
            },
            {
                "label": "Test",
                "provider_kind": "local",
                "model_id": "model-test",
                "base_url": None,
            },
            {
                "label": "Test",
                "provider_kind": "openai_api",
                "model_id": "model-test",
                "base_url": "https://example.invalid",
            },
        )
        for values in invalid_connection_values:
            with pytest.raises(sa.exc.DBAPIError), engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO ai_provider_connections "
                        "(user_id, id, label, provider_kind, model_id, base_url, "
                        "created_at, updated_at) "
                        "VALUES (:user_id, :id, :label, :provider_kind, :model_id, "
                        ":base_url, :now, :now)"
                    ),
                    {
                        **values,
                        "user_id": owner_id,
                        "id": uuid4(),
                        "now": now,
                    },
                )

        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM users WHERE id = :user_id"), {"user_id": owner_id}
            )
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM ai_provider_checks WHERE user_id=:id"), {"id": owner_id}) == 0
            assert connection.scalar(text("SELECT count(*) FROM ai_provider_checks WHERE user_id=:id"), {"id": other_id}) == 1
            assert connection.scalar(
                text("SELECT count(*) FROM ai_provider_connections WHERE user_id = :user_id"),
                {"user_id": owner_id},
            ) == 0
            assert connection.scalar(
                text("SELECT count(*) FROM ai_provider_credentials WHERE user_id = :user_id"),
                {"user_id": owner_id},
            ) == 0
            assert connection.scalar(
                text("SELECT count(*) FROM ai_provider_connections WHERE user_id = :user_id"),
                {"user_id": other_id},
            ) == 2
            assert connection.scalar(
                text("SELECT count(*) FROM ai_provider_credentials WHERE user_id = :user_id"),
                {"user_id": other_id},
            ) == 1

        command.downgrade(alembic_config, PREVIOUS_REVISION)
        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT count(*) FROM users WHERE id = :other"), {"other": other_id},
            ) == 1
            tables = set(sa.inspect(connection).get_table_names())
            assert "ai_provider_connections" not in tables
            assert "ai_provider_credentials" not in tables
            assert "ai_provider_checks" not in tables
        command.upgrade(alembic_config, "head")
        command.check(alembic_config)
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM ai_provider_connections")) == 0
    finally:
        engine.dispose()
