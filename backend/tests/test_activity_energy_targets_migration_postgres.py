import os
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command
from app.database import SessionLocal
from app.database import engine as application_engine
from app.models import User

POSTGRES_TESTS_ENABLED = (
    os.environ.get("CALOGRAPH_ALLOW_DESTRUCTIVE_POSTGRES_TESTS") == "1"
    and bool(os.environ.get("CALOGRAPH_POSTGRES_TEST_URL"))
)
PREVIOUS_REVISION = "20260816_0015"
TARGET_REVISION = "20260817_0016"
MODE_CONSTRAINT = "ck_target_activity_mode"
SOURCE_CONSTRAINT = "ck_target_activity_source"


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_activity_energy_target_migration_preserves_existing_targets() -> None:
    assert application_engine.dialect.name == "postgresql"
    database_url = os.environ["CALOGRAPH_POSTGRES_TEST_URL"]
    alembic_config = Config("alembic.ini")
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    engine = create_engine(database_url, pool_pre_ping=True)

    try:
        command.stamp(alembic_config, "head")
        command.downgrade(alembic_config, PREVIOUS_REVISION)
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == PREVIOUS_REVISION
            columns = {
                column["name"]
                for column in sa.inspect(connection).get_columns("nutrition_targets")
            }
            assert "activity_mode" not in columns
            assert "activity_source_type" not in columns

        command.upgrade(alembic_config, TARGET_REVISION)
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == TARGET_REVISION
            columns = {
                column["name"]
                for column in sa.inspect(connection).get_columns("nutrition_targets")
            }
            constraints = {
                constraint["name"]
                for constraint in sa.inspect(connection).get_check_constraints(
                    "nutrition_targets"
                )
            }
            assert {"activity_mode", "activity_source_type"} <= columns
            assert {MODE_CONSTRAINT, SOURCE_CONSTRAINT} <= constraints
        with SessionLocal() as db:
            user = User(
                username=f"postgres-activity-target-{uuid4()}",
                password_hash="test-password-hash",
            )
            db.add(user)
            db.commit()
            user_id = user.id

        target_id = uuid4()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO nutrition_targets "
                    "(id, user_id, valid_from, calories_kcal, protein_g, activity_mode, "
                    "activity_source_type, created_at) "
                    "VALUES (:id, :user_id, :valid_from, :calories_kcal, :protein_g, "
                    ":activity_mode, :activity_source_type, :created_at)"
                ),
                {
                    "id": target_id,
                    "user_id": user_id,
                    "valid_from": date(2026, 8, 17),
                    "calories_kcal": Decimal("2000"),
                    "protein_g": Decimal("140"),
                    "activity_mode": "full",
                    "activity_source_type": "apple_health_xml",
                    "created_at": datetime.now(UTC),
                },
            )
        for mode, source in (("off", "apple_health_xml"), ("full", None)):
            with pytest.raises(sa.exc.DBAPIError), engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE nutrition_targets SET activity_mode = :mode, "
                        "activity_source_type = :source WHERE id = :id"
                    ),
                    {"id": target_id, "mode": mode, "source": source},
                )
    finally:
        command.upgrade(alembic_config, "head")
        engine.dispose()
