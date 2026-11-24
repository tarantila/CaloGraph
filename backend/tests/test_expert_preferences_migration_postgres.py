from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command
from app.database import engine as application_engine

POSTGRES_TESTS_ENABLED = (
    os.environ.get("CALOGRAPH_ALLOW_DESTRUCTIVE_POSTGRES_TESTS") == "1"
    and bool(os.environ.get("CALOGRAPH_POSTGRES_TEST_URL"))
)


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_expert_preferences_migration_round_trip() -> None:
    assert application_engine.dialect.name == "postgresql"
    database_url = os.environ["CALOGRAPH_POSTGRES_TEST_URL"]
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    engine = create_engine(database_url, pool_pre_ping=True)
    owner_id = uuid4()
    now = datetime.now(UTC)
    try:
        command.stamp(config, "head")
        command.downgrade(config, "20261114_0046")
        command.upgrade(config, "20261114_0047")
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO users "
                "(id, username, password_hash, language, timezone, week_starts_on, "
                "preferred_weight_unit, raw_payload_retention_days, is_active, is_admin, "
                "created_at, updated_at) VALUES (:id, :username, 'synthetic-hash', 'de', "
                "'Europe/Berlin', 1, 'kg', 0, true, false, :now, :now)"
            ), {"id": owner_id, "username": f"expert-preferences-{uuid4()}", "now": now})
            connection.execute(text(
                "INSERT INTO expert_preferences "
                "(user_id, data_domains, include_meal_names, configuration_version, created_at, updated_at) "
                "VALUES (:user_id, CAST(:domains AS json), true, 1, :now, :now)"
            ), {
                "user_id": owner_id,
                "domains": '["nutrition", "targets", "weight", "activity", "profile", "health_context"]',
                "now": now,
            })
            assert connection.scalar(text(
                "SELECT json_array_length(data_domains) FROM expert_preferences WHERE user_id=:id"
            ), {"id": owner_id}) == 6
            foreign_keys = sa.inspect(connection).get_foreign_keys("expert_preferences")
            assert foreign_keys[0]["options"].get("ondelete") == "CASCADE"
        command.downgrade(config, "20261114_0046")
        with engine.connect() as connection:
            assert "expert_preferences" not in sa.inspect(connection).get_table_names()
    finally:
        engine.dispose()
