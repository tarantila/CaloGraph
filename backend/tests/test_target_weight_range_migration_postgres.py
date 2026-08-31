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
PREVIOUS_REVISION = "20260829_0019"
TARGET_REVISION = "20260830_0020"
CONSTRAINT_NAME = "ck_target_weight_range"


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_target_weight_range_migration_preserves_targets_and_downgrades() -> None:
    assert application_engine.dialect.name == "postgresql"
    database_url = os.environ["CALOGRAPH_POSTGRES_TEST_URL"]
    alembic_config = Config("alembic.ini")
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    engine = create_engine(database_url, pool_pre_ping=True)

    try:
        command.stamp(alembic_config, "head")
        command.downgrade(alembic_config, PREVIOUS_REVISION)
        with SessionLocal() as db:
            user = User(
                username=f"postgres-target-weight-{uuid4()}",
                password_hash="test-password-hash",
            )
            db.add(user)
            db.commit()
            user_id = user.id

        existing_target_id = uuid4()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO nutrition_targets "
                    "(id, user_id, valid_from, calories_kcal, protein_g, created_at) "
                    "VALUES (:id, :user_id, :valid_from, :calories_kcal, :protein_g, :created_at)"
                ),
                {
                    "id": existing_target_id,
                    "user_id": user_id,
                    "valid_from": date(2026, 8, 1),
                    "calories_kcal": Decimal("2000"),
                    "protein_g": Decimal("140"),
                    "created_at": datetime.now(UTC),
                },
            )

        command.upgrade(alembic_config, TARGET_REVISION)
        with engine.connect() as connection:
            inspector = sa.inspect(connection)
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == TARGET_REVISION
            columns = {column["name"]: column for column in inspector.get_columns("nutrition_targets")}
            assert columns["target_weight_min_kg"]["nullable"] is True
            assert columns["target_weight_max_kg"]["nullable"] is True
            assert columns["target_weight_min_kg"]["type"].precision == 7
            assert columns["target_weight_min_kg"]["type"].scale == 3
            checks = {
                constraint["name"]
                for constraint in inspector.get_check_constraints("nutrition_targets")
            }
            assert CONSTRAINT_NAME in checks
            assert connection.execute(
                text(
                    "SELECT target_weight_min_kg, target_weight_max_kg "
                    "FROM nutrition_targets WHERE id = :id"
                ),
                {"id": existing_target_id},
            ).one() == (None, None)

        valid_targets = (
            (date(2026, 8, 2), None, None),
            (date(2026, 8, 3), Decimal("72"), Decimal("72")),
            (date(2026, 8, 4), Decimal("60.125"), Decimal("1000")),
        )
        with engine.begin() as connection:
            for valid_from, minimum, maximum in valid_targets:
                connection.execute(
                    text(
                        "INSERT INTO nutrition_targets "
                        "(id, user_id, valid_from, calories_kcal, protein_g, "
                        "target_weight_min_kg, target_weight_max_kg, created_at) "
                        "VALUES (:id, :user_id, :valid_from, :calories_kcal, :protein_g, "
                        ":minimum, :maximum, :created_at)"
                    ),
                    {
                        "id": uuid4(),
                        "user_id": user_id,
                        "valid_from": valid_from,
                        "calories_kcal": Decimal("2000"),
                        "protein_g": Decimal("140"),
                        "minimum": minimum,
                        "maximum": maximum,
                        "created_at": datetime.now(UTC),
                    },
                )

        invalid_targets = (
            (date(2026, 8, 10), Decimal("70"), None),
            (date(2026, 8, 11), None, Decimal("80")),
            (date(2026, 8, 12), Decimal("0"), Decimal("80")),
            (date(2026, 8, 13), Decimal("80"), Decimal("70")),
            (date(2026, 8, 14), Decimal("80"), Decimal("1000.001")),
            (date(2026, 8, 15), Decimal("NaN"), Decimal("80")),
            (date(2026, 8, 16), Decimal("80"), Decimal("Infinity")),
        )
        for valid_from, minimum, maximum in invalid_targets:
            with pytest.raises(sa.exc.DBAPIError), engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO nutrition_targets "
                        "(id, user_id, valid_from, calories_kcal, protein_g, "
                        "target_weight_min_kg, target_weight_max_kg, created_at) "
                        "VALUES (:id, :user_id, :valid_from, :calories_kcal, :protein_g, "
                        ":minimum, :maximum, :created_at)"
                    ),
                    {
                        "id": uuid4(),
                        "user_id": user_id,
                        "valid_from": valid_from,
                        "calories_kcal": Decimal("2000"),
                        "protein_g": Decimal("140"),
                        "minimum": minimum,
                        "maximum": maximum,
                        "created_at": datetime.now(UTC),
                    },
                )

        command.downgrade(alembic_config, PREVIOUS_REVISION)
        with engine.connect() as connection:
            inspector = sa.inspect(connection)
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == PREVIOUS_REVISION
            columns = {column["name"] for column in inspector.get_columns("nutrition_targets")}
            assert "target_weight_min_kg" not in columns
            assert "target_weight_max_kg" not in columns
            checks = {
                constraint["name"]
                for constraint in inspector.get_check_constraints("nutrition_targets")
            }
            assert CONSTRAINT_NAME not in checks
            assert connection.scalar(
                text("SELECT count(*) FROM nutrition_targets WHERE user_id = :user_id"),
                {"user_id": user_id},
            ) == 4
    finally:
        command.upgrade(alembic_config, "head")
        engine.dispose()
