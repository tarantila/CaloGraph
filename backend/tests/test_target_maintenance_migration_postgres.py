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

POSTGRES_TESTS_ENABLED = (
    os.environ.get("CALOGRAPH_ALLOW_DESTRUCTIVE_POSTGRES_TESTS") == "1"
    and bool(os.environ.get("CALOGRAPH_POSTGRES_TEST_URL"))
)
PREVIOUS_REVISION = "20260810_0011"
TARGET_REVISION = "20260811_0012"
OLD_CONSTRAINT_NAME = "ck_target_maintenance_at_least_budget"
NEW_CONSTRAINT_NAME = "ck_target_maintenance_positive_finite"


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_maintenance_migration_removes_budget_ordering_constraint() -> None:
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
            constraints = {
                constraint["name"]
                for constraint in sa.inspect(connection).get_check_constraints(
                    "nutrition_targets"
                )
            }
            assert OLD_CONSTRAINT_NAME in constraints
            assert NEW_CONSTRAINT_NAME not in constraints

        command.upgrade(alembic_config, TARGET_REVISION)
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == TARGET_REVISION
            constraints = {
                constraint["name"]
                for constraint in sa.inspect(connection).get_check_constraints(
                    "nutrition_targets"
                )
            }
            assert OLD_CONSTRAINT_NAME not in constraints
            assert NEW_CONSTRAINT_NAME in constraints

        user_id = uuid4()
        target_id = uuid4()
        now = datetime.now(UTC)
        with engine.begin() as connection:
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
                    "username": "postgres-independent-maintenance",
                    "password_hash": "test-password-hash",
                    "created_at": now,
                    "updated_at": now,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO nutrition_targets "
                    "(id, user_id, valid_from, calories_kcal, maintenance_kcal, protein_g, created_at) "
                    "VALUES (:id, :user_id, :valid_from, :calories_kcal, :maintenance_kcal, "
                    ":protein_g, :created_at)"
                ),
                {
                    "id": target_id,
                    "user_id": user_id,
                    "valid_from": date(2026, 8, 11),
                    "calories_kcal": Decimal("3000"),
                    "maintenance_kcal": Decimal("2500"),
                    "protein_g": Decimal("140"),
                    "created_at": now,
                },
            )

        for invalid_maintenance in (
            Decimal("0"),
            Decimal("-1"),
            Decimal("NaN"),
            Decimal("Infinity"),
            Decimal("-Infinity"),
        ):
            with SessionLocal() as db:
                with pytest.raises(sa.exc.DBAPIError):
                    db.execute(
                        text(
                            "UPDATE nutrition_targets SET maintenance_kcal = :maintenance_kcal "
                            "WHERE id = :id"
                        ),
                        {"maintenance_kcal": invalid_maintenance, "id": target_id},
                    )
                    db.commit()
                db.rollback()

        with SessionLocal() as db:
            db.execute(
                text(
                    "UPDATE nutrition_targets SET maintenance_kcal = NULL WHERE id = :id"
                ),
                {"id": target_id},
            )
            db.commit()
    finally:
        command.upgrade(alembic_config, "head")
        engine.dispose()
