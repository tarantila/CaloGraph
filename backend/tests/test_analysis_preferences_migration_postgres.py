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
PREVIOUS_REVISION = "20260924_0036"
TARGET_REVISION = "20260927_0037"
TABLE = "user_analysis_preferences"


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_analysis_preferences_migration_constraints_order_and_cascade() -> None:
    assert application_engine.dialect.name == "postgresql"
    database_url = os.environ["CALOGRAPH_POSTGRES_TEST_URL"]
    alembic_config = Config("alembic.ini")
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    engine = create_engine(database_url, pool_pre_ping=True)

    try:
        command.stamp(alembic_config, "head")
        command.downgrade(alembic_config, PREVIOUS_REVISION)
        with engine.connect() as connection:
            inspector = sa.inspect(connection)
            assert TABLE not in inspector.get_table_names()
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == PREVIOUS_REVISION

        owner_id = uuid4()
        other_id = uuid4()
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
                        "1, 'kg', 0, true, false, :created_at, :updated_at)"
                    ),
                    {
                        "id": user_id,
                        "username": f"postgres-analysis-{uuid4()}",
                        "created_at": now,
                        "updated_at": now,
                    },
                )

        command.upgrade(alembic_config, TARGET_REVISION)
        command.upgrade(alembic_config, TARGET_REVISION)
        with engine.connect() as connection:
            inspector = sa.inspect(connection)
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == TARGET_REVISION
            assert TABLE in inspector.get_table_names()
            columns = {column["name"] for column in inspector.get_columns(TABLE)}
            assert columns == {"user_id", "analysis_key", "position", "created_at", "updated_at"}
            checks = {constraint["name"] for constraint in inspector.get_check_constraints(TABLE)}
            assert checks == {
                "ck_user_analysis_preferences_key",
                "ck_user_analysis_preferences_position",
            }
            unique_constraints = {
                constraint["name"] for constraint in inspector.get_unique_constraints(TABLE)
            }
            assert "uq_user_analysis_preferences_position" in unique_constraints
            foreign_keys = inspector.get_foreign_keys(TABLE)
            assert len(foreign_keys) == 1
            assert foreign_keys[0]["referred_table"] == "users"
            assert foreign_keys[0]["options"].get("ondelete") == "CASCADE"
            assert connection.scalar(text(f"SELECT count(*) FROM {TABLE}")) == 0

        with engine.begin() as connection:
            for user_id, key, position in (
                (owner_id, "weight_kg", 0),
                (owner_id, "active_energy_kcal", 1),
                (other_id, "weight_kg", 0),
            ):
                connection.execute(
                    text(
                        f"INSERT INTO {TABLE} "
                        "(user_id, analysis_key, position, created_at, updated_at) "
                        "VALUES (:user_id, :analysis_key, :position, :now, :now)"
                    ),
                    {
                        "user_id": user_id,
                        "analysis_key": key,
                        "position": position,
                        "now": now,
                    },
                )

        with engine.connect() as connection:
            ordered = connection.execute(
                text(f"SELECT analysis_key FROM {TABLE} WHERE user_id = :user_id ORDER BY position"),
                {"user_id": owner_id},
            ).scalars().all()
            assert ordered == ["weight_kg", "active_energy_kcal"]

        invalid_rows = (
            (owner_id, "duplicate-position", 0),
            (owner_id, "weight_kg", 2),
            (owner_id, "negative-position", -1),
            (owner_id, "", 2),
            (uuid4(), "missing-user", 0),
        )
        for user_id, key, position in invalid_rows:
            with pytest.raises(sa.exc.DBAPIError), engine.begin() as connection:
                connection.execute(
                    text(
                        f"INSERT INTO {TABLE} "
                        "(user_id, analysis_key, position, created_at, updated_at) "
                        "VALUES (:user_id, :analysis_key, :position, :now, :now)"
                    ),
                    {
                        "user_id": user_id,
                        "analysis_key": key,
                        "position": position,
                        "now": now,
                    },
                )

        with engine.begin() as connection:
            connection.execute(text("DELETE FROM users WHERE id = :user_id"), {"user_id": owner_id})
        with engine.connect() as connection:
            remaining = connection.execute(
                text(f"SELECT analysis_key FROM {TABLE} WHERE user_id = :user_id"),
                {"user_id": owner_id},
            ).scalars().all()
            assert remaining == []

        command.downgrade(alembic_config, PREVIOUS_REVISION)
        with engine.connect() as connection:
            inspector = sa.inspect(connection)
            assert TABLE not in inspector.get_table_names()
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == PREVIOUS_REVISION
    finally:
        command.upgrade(alembic_config, "head")
        engine.dispose()
