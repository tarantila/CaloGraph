import os
from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command
from app.database import SessionLocal
from app.database import engine as application_engine
from app.models import NutritionTarget, User

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

        with SessionLocal() as db:
            user = User(
                username="postgres-independent-maintenance",
                password_hash="test-password-hash",
            )
            db.add(user)
            db.flush()
            target = NutritionTarget(
                user_id=user.id,
                valid_from=date(2026, 8, 11),
                calories_kcal=Decimal("3000"),
                maintenance_kcal=Decimal("2500"),
                protein_g=Decimal("140"),
            )
            db.add(target)
            db.flush()
            target_id = target.id
            db.commit()

        for invalid_maintenance in (
            Decimal("0"),
            Decimal("-1"),
            Decimal("NaN"),
            Decimal("Infinity"),
            Decimal("-Infinity"),
        ):
            with SessionLocal() as db:
                target = db.get(NutritionTarget, target_id)
                assert target is not None
                target.maintenance_kcal = invalid_maintenance
                with pytest.raises(sa.exc.DBAPIError):
                    db.commit()

        with SessionLocal() as db:
            target = db.get(NutritionTarget, target_id)
            assert target is not None
            target.maintenance_kcal = None
            db.commit()
    finally:
        command.upgrade(alembic_config, "head")
        engine.dispose()
