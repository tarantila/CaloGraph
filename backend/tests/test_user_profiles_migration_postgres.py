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
from app.models import User

POSTGRES_TESTS_ENABLED = (
    os.environ.get("CALOGRAPH_ALLOW_DESTRUCTIVE_POSTGRES_TESTS") == "1"
    and bool(os.environ.get("CALOGRAPH_POSTGRES_TEST_URL"))
)
PREVIOUS_REVISION = "20260826_0018"
TARGET_REVISION = "20260829_0019"
EXPECTED_CHECKS = {
    "ck_user_profiles_gender",
    "ck_user_profiles_height_cm",
    "ck_user_profiles_diet_type",
}


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_user_profiles_migration_constraints_and_user_cascade() -> None:
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
            assert "user_profiles" not in inspector.get_table_names()
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == PREVIOUS_REVISION

        with SessionLocal() as db:
            existing_user = User(
                username="postgres-existing-profile-user",
                password_hash="test-password-hash",
            )
            db.add(existing_user)
            db.commit()
            existing_user_id = existing_user.id

        command.upgrade(alembic_config, TARGET_REVISION)
        with engine.connect() as connection:
            inspector = sa.inspect(connection)
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == TARGET_REVISION
            assert "user_profiles" in inspector.get_table_names()
            columns = {column["name"]: column for column in inspector.get_columns("user_profiles")}
            assert set(columns) == {
                "user_id",
                "display_name",
                "gender",
                "birth_date",
                "height_cm",
                "diet_type",
                "health_notes",
                "intolerances",
                "created_at",
                "updated_at",
            }
            assert columns["user_id"]["nullable"] is False
            assert columns["created_at"]["nullable"] is False
            assert columns["updated_at"]["nullable"] is False
            assert all(
                columns[name]["nullable"] is True
                for name in {
                    "display_name",
                    "gender",
                    "birth_date",
                    "height_cm",
                    "diet_type",
                    "health_notes",
                    "intolerances",
                }
            )
            checks = {
                constraint["name"]
                for constraint in inspector.get_check_constraints("user_profiles")
            }
            assert checks >= EXPECTED_CHECKS
            foreign_keys = inspector.get_foreign_keys("user_profiles")
            assert len(foreign_keys) == 1
            assert foreign_keys[0]["referred_table"] == "users"
            assert foreign_keys[0]["options"].get("ondelete") == "CASCADE"
            assert connection.scalar(text("SELECT count(*) FROM user_profiles")) == 0

        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO user_profiles (
                        user_id, display_name, gender, birth_date, height_cm,
                        diet_type, health_notes, intolerances, created_at, updated_at
                    ) VALUES (
                        :user_id, :display_name, :gender, :birth_date, :height_cm,
                        :diet_type, :health_notes, :intolerances, now(), now()
                    )
                    """
                ),
                {
                    "user_id": existing_user_id,
                    "display_name": "Existing User",
                    "gender": "prefer_not_to_say",
                    "birth_date": date(1990, 1, 1),
                    "height_cm": Decimal("180.25"),
                    "diet_type": "no_special_diet",
                    "health_notes": None,
                    "intolerances": None,
                },
            )

        invalid_values = (
            ("gender", "invalid-gender"),
            ("diet_type", "invalid-diet"),
            ("height_cm", Decimal("0")),
            ("height_cm", Decimal("300.01")),
        )
        for column, value in invalid_values:
            with pytest.raises(sa.exc.IntegrityError), engine.begin() as connection:
                connection.execute(
                    text(
                        f"""
                        UPDATE user_profiles
                        SET {column} = :value, updated_at = now()
                        WHERE user_id = :user_id
                        """
                    ),
                    {"value": value, "user_id": existing_user_id},
                )

        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM users WHERE id = :user_id"),
                {"user_id": existing_user_id},
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM user_profiles WHERE user_id = :user_id"),
                    {"user_id": existing_user_id},
                )
                == 0
            )
    finally:
        command.upgrade(alembic_config, "head")
        engine.dispose()
