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
PREVIOUS_REVISION = "20260918_0031"
TARGET_REVISION = "20260920_0032"
SOURCE_CONSTRAINT = "ck_target_activity_source"


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_google_activity_source_constraint_upgrades_and_downgrades() -> None:
    assert application_engine.dialect.name == "postgresql"
    database_url = os.environ["CALOGRAPH_POSTGRES_TEST_URL"]
    alembic_config = Config("alembic.ini")
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    engine = create_engine(database_url, pool_pre_ping=True)
    user_id = uuid4()
    now = datetime.now(UTC)

    try:
        command.stamp(alembic_config, "head")
        command.downgrade(alembic_config, PREVIOUS_REVISION)
        command.upgrade(alembic_config, TARGET_REVISION)
        with engine.begin() as connection:
            constraints = {
                constraint["name"]
                for constraint in sa.inspect(connection).get_check_constraints(
                    "nutrition_targets"
                )
            }
            assert SOURCE_CONSTRAINT in constraints
            connection.execute(
                text(
                    "INSERT INTO users "
                    "(id, username, password_hash, language, timezone, week_starts_on, "
                    "preferred_weight_unit, raw_payload_retention_days, is_active, "
                    "is_admin, created_at, updated_at) "
                    "VALUES (:id, :username, :password_hash, 'de', 'Europe/Berlin', "
                    "1, 'kg', 0, true, true, :created_at, :updated_at)"
                ),
                {
                    "id": user_id,
                    "username": f"postgres-google-target-{uuid4()}",
                    "password_hash": "test-password-hash",
                    "created_at": now,
                    "updated_at": now,
                },
            )
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO nutrition_targets "
                    "(id, user_id, valid_from, calories_kcal, protein_g, activity_mode, "
                    "activity_source_type, created_at) VALUES "
                    "(:id, :user_id, :valid_from, :calories_kcal, :protein_g, 'full', "
                    "'google_health_activity_v4', :created_at)"
                ),
                {
                    "id": uuid4(),
                    "user_id": user_id,
                    "valid_from": datetime(2026, 9, 20).date(),
                    "calories_kcal": 2000,
                    "protein_g": 140,
                    "created_at": now,
                },
            )
        with pytest.raises(
            RuntimeError,
            match="Cannot downgrade Google activity target constraint while Google targets exist",
        ):
            command.downgrade(alembic_config, PREVIOUS_REVISION)
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM nutrition_targets WHERE user_id = :user_id"),
                {"user_id": user_id},
            )
        command.downgrade(alembic_config, PREVIOUS_REVISION)
        with pytest.raises(sa.exc.DBAPIError), engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO nutrition_targets "
                    "(id, user_id, valid_from, calories_kcal, protein_g, activity_mode, "
                    "activity_source_type, created_at) VALUES "
                    "(:id, :user_id, :valid_from, :calories_kcal, :protein_g, 'full', "
                    "'google_health_activity_v4', :created_at)"
                ),
                {
                    "id": uuid4(),
                    "user_id": user_id,
                    "valid_from": datetime(2026, 9, 21).date(),
                    "calories_kcal": 2000,
                    "protein_g": 140,
                    "created_at": now,
                },
            )
    finally:
        command.upgrade(alembic_config, "head")
        engine.dispose()
