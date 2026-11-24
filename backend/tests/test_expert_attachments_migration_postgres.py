from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command
from app.database import Base
from app.database import engine as application_engine

POSTGRES_TESTS_ENABLED = (
    os.environ.get("CALOGRAPH_ALLOW_DESTRUCTIVE_POSTGRES_TESTS") == "1"
    and bool(os.environ.get("CALOGRAPH_POSTGRES_TEST_URL"))
)


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_attachment_migration_backfills_defaults_and_retains_owner_receipts() -> None:
    assert application_engine.dialect.name == "postgresql"
    database_url = os.environ["CALOGRAPH_POSTGRES_TEST_URL"]
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    engine = create_engine(database_url, pool_pre_ping=True)
    owner_id = uuid4()
    connection_id, consent_id = uuid4(), uuid4()
    conversation_id, run_id, attachment_id = uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    try:
        command.stamp(config, "head")
        command.downgrade(config, "20261114_0048")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users (id, username, password_hash, language, timezone, "
                    "week_starts_on, preferred_weight_unit, raw_payload_retention_days, "
                    "is_active, is_admin, created_at, updated_at) VALUES "
                    "(:id, :username, 'synthetic-hash', 'de', 'Europe/Berlin', 1, 'kg', "
                    "0, true, false, :now, :now)"
                ),
                {"id": owner_id, "username": f"attachment-migration-{uuid4()}", "now": now},
            )
            connection.execute(
                text(
                    "INSERT INTO ai_provider_connections (user_id, id, label, provider_kind, "
                    "model_id, enabled, timeout_seconds, configuration_version, created_at, updated_at) "
                    "VALUES (:owner, :id, 'Synthetic', 'openai_api', 'synthetic-model', false, 60, 1, :now, :now)"
                ),
                {"owner": owner_id, "id": connection_id, "now": now},
            )
            connection.execute(
                text(
                    "INSERT INTO ai_consents (user_id, id, connection_id, configuration_version, "
                    "provider_kind, model_id, purpose, data_domains, include_meal_names, history_mode, "
                    "policy_version, created_at) VALUES (:owner, :id, :connection, 1, 'openai_api', "
                    "'synthetic-model', 'nutrition_chat', '[\"nutrition\"]', false, 'all_available', "
                    "'synthetic-policy', :now)"
                ),
                {"owner": owner_id, "id": consent_id, "connection": connection_id, "now": now},
            )
            connection.execute(
                text(
                    "INSERT INTO expert_conversations (user_id, id, mode, created_at, updated_at) "
                    "VALUES (:owner, :id, 'chat', :now, :now)"
                ),
                {"owner": owner_id, "id": conversation_id, "now": now},
            )
            connection.execute(
                text(
                    "INSERT INTO expert_runs (user_id, id, conversation_id, request_hash, kind, message, "
                    "connection_id, connection_version, preferences_version, consent_id, provider_kind, "
                    "model_id, state, created_at) VALUES (:owner, :id, :conversation, :hash, 'chat', "
                    "'synthetic question', :connection, 1, 1, :consent, 'openai_api', 'synthetic-model', "
                    "'failed', :now)"
                ),
                {
                    "owner": owner_id, "id": run_id, "conversation": conversation_id,
                    "hash": "a" * 64, "connection": connection_id, "consent": consent_id, "now": now,
                },
            )

        command.upgrade(config, "20261114_0049")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO expert_attachments (user_id, id, conversation_id, file_name, media_type, "
                    "byte_size, sha256, data, created_at) VALUES (:owner, :id, :conversation, "
                    "'synthetic.pdf', 'application/pdf', 8, :hash, :data, :now)"
                ),
                {
                    "owner": owner_id, "id": attachment_id, "conversation": conversation_id,
                    "hash": "b" * 64, "data": b"%PDF-1.4", "now": now,
                },
            )

        command.upgrade(config, "head")
        with engine.connect() as connection:
            for table in (
                "ai_consents", "expert_conversations", "expert_runs",
                "expert_attachments", "expert_attachment_identities",
            ):
                assert {column["name"] for column in inspect(connection).get_columns(table)} == set(
                    Base.metadata.tables[table].columns.keys()
                )
            assert connection.execute(
                text("SELECT include_attachments FROM ai_consents WHERE user_id=:owner AND id=:id"),
                {"owner": owner_id, "id": consent_id},
            ).scalar_one() is False
            assert connection.execute(
                text("SELECT uses_attachments FROM expert_conversations WHERE user_id=:owner AND id=:id"),
                {"owner": owner_id, "id": conversation_id},
            ).scalar_one() is False
            assert connection.execute(
                text("SELECT attachment_ids FROM expert_runs WHERE user_id=:owner AND id=:id"),
                {"owner": owner_id, "id": run_id},
            ).scalar_one() == []
            assert connection.execute(
                text("SELECT count(*) FROM expert_attachment_identities WHERE user_id=:owner AND attachment_id=:id"),
                {"owner": owner_id, "id": attachment_id},
            ).scalar_one() == 1
            assert connection.execute(
                text("SELECT count(*) FROM expert_conversation_identities WHERE user_id=:owner AND conversation_id=:id"),
                {"owner": owner_id, "id": conversation_id},
            ).scalar_one() == 1
            assert connection.execute(
                text("SELECT count(*) FROM expert_run_identities WHERE user_id=:owner AND run_id=:id"),
                {"owner": owner_id, "id": run_id},
            ).scalar_one() == 1

        with pytest.raises(RuntimeError, match="Expert attachment identity receipts"):
            command.downgrade(config, "20261124_0060")

        with engine.begin() as connection:
            connection.execute(text("DELETE FROM users WHERE id=:owner"), {"owner": owner_id})
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT count(*) FROM expert_attachment_identities WHERE user_id=:owner"),
                {"owner": owner_id},
            ).scalar_one() == 0
            assert connection.execute(
                text("SELECT count(*) FROM expert_conversation_identities WHERE user_id=:owner"),
                {"owner": owner_id},
            ).scalar_one() == 0

        command.downgrade(config, "20261124_0060")
        command.downgrade(config, "20261114_0048")
        with engine.connect() as connection:
            tables = inspect(connection).get_table_names()
            assert "expert_attachments" not in tables
            assert "expert_attachment_identities" not in tables
    finally:
        engine.dispose()
