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
from app.models import (
    AIConnectionRetirement,
    AIProviderConnection,
    AIProviderCredential,
    User,
)

POSTGRES_TESTS_ENABLED = (
    os.environ.get("CALOGRAPH_ALLOW_DESTRUCTIVE_POSTGRES_TESTS") == "1"
    and bool(os.environ.get("CALOGRAPH_POSTGRES_TEST_URL"))
)


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_ai_consents_upgrade_and_downgrade_preserve_connection_secret() -> None:
    assert application_engine.dialect.name == "postgresql"
    database_url = os.environ["CALOGRAPH_POSTGRES_TEST_URL"]
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    engine = create_engine(database_url, pool_pre_ping=True)

    owner_id, connection_id = uuid4(), uuid4()
    now = datetime.now(UTC)
    ciphertext = b"opaque-synthetic-ciphertext"
    try:
        # The opt-in runner supplies a disposable PostgreSQL database.
        command.stamp(config, "head")
        command.downgrade(config, "20261113_0045")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users "
                    "(id, username, password_hash, language, timezone, week_starts_on, "
                    "preferred_weight_unit, raw_payload_retention_days, is_active, is_admin, "
                    "created_at, updated_at) VALUES (:id, :username, 'synthetic-hash', 'de', "
                    "'Europe/Berlin', 1, 'kg', 0, true, false, :now, :now)"
                ),
                {"id": owner_id, "username": f"consent-migration-{uuid4()}", "now": now},
            )
            connection.execute(
                text(
                    "INSERT INTO ai_provider_connections "
                    "(user_id, id, label, provider_kind, model_id, enabled, "
                    "configuration_version, created_at, updated_at) "
                    "VALUES (:user_id, :id, 'Synthetic', 'openai_api', 'model-test', true, 1, :now, :now)"
                ),
                {"user_id": owner_id, "id": connection_id, "now": now},
            )
            connection.execute(
                text(
                    "INSERT INTO ai_provider_credentials (user_id, connection_id, encrypted_api_key) "
                    "VALUES (:user_id, :connection_id, :ciphertext)"
                ),
                {"user_id": owner_id, "connection_id": connection_id, "ciphertext": ciphertext},
            )

        command.upgrade(config, "head")
        consent_id = uuid4()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO ai_consents "
                    "(user_id, id, connection_id, configuration_version, provider_kind, model_id, "
                    "purpose, data_domains, include_meal_names, history_mode, history_start, "
                    "history_end, policy_version, created_at) VALUES "
                    "(:user_id, :id, :connection_id, 1, 'openai_api', 'model-test', "
                    "'nutrition_chat', '[\"nutrition\"]'::json, false, 'date_range', "
                    "DATE '2026-01-01', DATE '2026-01-31', 'expert-data-2026-11-14', :now)"
                ),
                {"user_id": owner_id, "id": consent_id, "connection_id": connection_id, "now": now},
            )
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM ai_consents WHERE id=:id"), {"id": consent_id}) == 1
            fks = sa.inspect(connection).get_foreign_keys("ai_consents")
            connection_fk = next(fk for fk in fks if fk["constrained_columns"] == ["user_id", "connection_id"])
            assert connection_fk["referred_columns"] == ["user_id", "id"]
            assert connection_fk["options"].get("ondelete") == "CASCADE"
            assert connection.scalar(
                text("SELECT encrypted_api_key FROM ai_provider_credentials WHERE user_id=:id"),
                {"id": owner_id},
            ) == ciphertext

        command.downgrade(config, "20261113_0045")
        with engine.connect() as connection:
            assert "ai_consents" not in sa.inspect(connection).get_table_names()
            assert connection.scalar(text("SELECT count(*) FROM ai_provider_connections WHERE id=:id"), {"id": connection_id}) == 1
            assert connection.scalar(
                text("SELECT encrypted_api_key FROM ai_provider_credentials WHERE user_id=:id"),
                {"id": owner_id},
            ) == ciphertext
    finally:
        engine.dispose()


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_retained_revocations_upgrade_backfill_guard_and_owner_cleanup() -> None:
    assert application_engine.dialect.name == "postgresql"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", os.environ["CALOGRAPH_POSTGRES_TEST_URL"])
    engine = create_engine(os.environ["CALOGRAPH_POSTGRES_TEST_URL"], pool_pre_ping=True)
    owner_id, connection_id, consent_id = uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    try:
        command.stamp(config, "head")
        command.downgrade(config, "20261114_0046")
        with engine.begin() as connection:
            connection.execute(sa.insert(User).values(
                id=owner_id, username=f"revocation-migration-{uuid4()}",
                password_hash="synthetic-only", language="de", timezone="Europe/Berlin",
                week_starts_on=1, preferred_weight_unit="kg", raw_payload_retention_days=0,
                is_active=True, is_admin=False, created_at=now, updated_at=now,
            ))
            connection.execute(sa.insert(AIProviderConnection).values(
                user_id=owner_id, id=connection_id, label="Synthetic", provider_kind="openai_api",
                model_id="synthetic-model", enabled=True, configuration_version=1,
                created_at=now, updated_at=now,
            ))
            connection.execute(sa.insert(AIProviderCredential).values(
                user_id=owner_id, connection_id=connection_id, encrypted_api_key=b"synthetic-ciphertext",
            ))
            # Reflect the historical schema so defaults from newer consent
            # columns cannot leak into this pre-attachment migration fixture.
            legacy_consents = sa.Table("ai_consents", sa.MetaData(), autoload_with=connection)
            connection.execute(sa.insert(legacy_consents).values(
                user_id=owner_id, id=consent_id, connection_id=connection_id,
                configuration_version=1, provider_kind="openai_api", model_id="synthetic-model",
                purpose="nutrition_chat", data_domains=["nutrition"], include_meal_names=False,
                history_mode="all_available", history_start=None, history_end=None,
                policy_version="expert-data-2026-11-14", created_at=now, revoked_at=now,
            ))
        command.upgrade(config, "20261123_0056")
        with engine.connect() as connection:
            assert connection.scalar(text(
                "SELECT revoked_at FROM ai_consent_revocations WHERE user_id=:owner AND consent_id=:id"
            ), {"owner": owner_id, "id": consent_id}) == now
            assert connection.scalar(text(
                "SELECT encrypted_api_key FROM ai_provider_credentials WHERE user_id=:owner AND connection_id=:id"
            ), {"owner": owner_id, "id": connection_id}) == b"synthetic-ciphertext"
        with engine.begin() as connection:
            connection.execute(sa.insert(AIConnectionRetirement).values(
                user_id=owner_id, connection_id=uuid4(), retired_at=now,
            ))
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM ai_consent_revocations WHERE user_id=:owner"),
                                     {"owner": owner_id}) == 1
            assert connection.scalar(text("SELECT count(*) FROM ai_connection_retirements WHERE user_id=:owner"),
                                     {"owner": owner_id}) == 1
            assert "expert_preferences" in sa.inspect(connection).get_table_names()
        with pytest.raises(RuntimeError, match="retained consent revocations"):
            command.downgrade(config, "20261114_0046")
        with engine.begin() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                ScriptDirectory.from_config(config).get_current_head()
            )
            connection.execute(text("DELETE FROM ai_provider_connections WHERE user_id=:owner AND id=:id"),
                               {"owner": owner_id, "id": connection_id})
            assert connection.scalar(text("SELECT count(*) FROM ai_consents WHERE user_id=:owner"),
                                     {"owner": owner_id}) == 0
            assert connection.scalar(text("SELECT count(*) FROM ai_consent_revocations WHERE user_id=:owner"),
                                     {"owner": owner_id}) == 1
            connection.execute(sa.insert(AIConnectionRetirement).values(
                user_id=owner_id, connection_id=connection_id, retired_at=now,
            ))
            connection.execute(text("DELETE FROM ai_consent_revocations WHERE user_id=:owner"),
                               {"owner": owner_id})
        with pytest.raises(RuntimeError, match="connection retirements"):
            command.downgrade(config, "20261114_0046")
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM users WHERE id=:id"), {"id": owner_id})
            assert connection.scalar(text("SELECT count(*) FROM ai_consent_revocations WHERE user_id=:owner"),
                                     {"owner": owner_id}) == 0
            assert connection.scalar(text("SELECT count(*) FROM ai_connection_retirements WHERE user_id=:owner"),
                                     {"owner": owner_id}) == 0
        command.downgrade(config, "20261114_0046")
        command.upgrade(config, "head")
        command.check(config)
    finally:
        engine.dispose()
