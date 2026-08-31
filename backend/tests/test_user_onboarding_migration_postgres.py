import os
from datetime import UTC, datetime
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
PREVIOUS_REVISION = "20260830_0020"
TARGET_REVISION = "20260831_0021"
EXPECTED_COLUMNS = {
    "user_id",
    "current_step",
    "completed_at",
    "created_at",
    "updated_at",
}
EXPECTED_STEPS = {"personal", "targets", "security", "completed"}


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_user_onboarding_migration_constraints_cascade_and_no_backfill() -> None:
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
            assert "user_onboarding" not in inspector.get_table_names()
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == PREVIOUS_REVISION

        with SessionLocal() as db:
            existing_user = User(
                username=f"postgres-onboarding-{uuid4()}",
                password_hash="test-password-hash",
            )
            cascade_user = User(
                username=f"postgres-onboarding-cascade-{uuid4()}",
                password_hash="test-password-hash",
            )
            db.add_all([existing_user, cascade_user])
            db.commit()
            existing_user_id = existing_user.id
            cascade_user_id = cascade_user.id

        command.upgrade(alembic_config, TARGET_REVISION)
        with engine.connect() as connection:
            inspector = sa.inspect(connection)
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == TARGET_REVISION
            assert "user_onboarding" in inspector.get_table_names()
            columns = {column["name"]: column for column in inspector.get_columns("user_onboarding")}
            assert set(columns) == EXPECTED_COLUMNS
            assert columns["user_id"]["nullable"] is False
            assert columns["current_step"]["nullable"] is False
            assert columns["created_at"]["nullable"] is False
            assert columns["updated_at"]["nullable"] is False
            assert columns["completed_at"]["nullable"] is True
            checks = {
                constraint["name"]
                for constraint in inspector.get_check_constraints("user_onboarding")
            }
            assert "ck_user_onboarding_current_step" in checks
            foreign_keys = inspector.get_foreign_keys("user_onboarding")
            assert len(foreign_keys) == 1
            assert foreign_keys[0]["referred_table"] == "users"
            assert foreign_keys[0]["options"].get("ondelete") == "CASCADE"
            assert connection.scalar(text("SELECT count(*) FROM user_onboarding")) == 0

        with engine.begin() as connection:
            now = datetime.now(UTC)
            connection.execute(
                text(
                    "INSERT INTO user_onboarding "
                    "(user_id, current_step, completed_at, created_at, updated_at) "
                    "VALUES (:user_id, 'personal', NULL, :now, :now)"
                ),
                {"user_id": existing_user_id, "now": now},
            )
            connection.execute(
                text(
                    "INSERT INTO user_onboarding "
                    "(user_id, current_step, completed_at, created_at, updated_at) "
                    "VALUES (:user_id, 'completed', :now, :now, :now)"
                ),
                {"user_id": cascade_user_id, "now": now},
            )

        for invalid_step in ("unknown", "", "PERSONAL"):
            with pytest.raises(sa.exc.DBAPIError), engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO user_onboarding "
                        "(user_id, current_step, created_at, updated_at) "
                        "VALUES (:user_id, :step, :now, :now)"
                    ),
                    {"user_id": uuid4(), "step": invalid_step, "now": datetime.now(UTC)},
                )

        with engine.begin() as connection:
            connection.execute(text("DELETE FROM users WHERE id = :user_id"), {"user_id": cascade_user_id})
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM user_onboarding WHERE user_id = :user_id"),
                    {"user_id": cascade_user_id},
                )
                == 0
            )

        command.downgrade(alembic_config, PREVIOUS_REVISION)
        with engine.connect() as connection:
            inspector = sa.inspect(connection)
            assert "user_onboarding" not in inspector.get_table_names()
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == PREVIOUS_REVISION
    finally:
        command.upgrade(alembic_config, "head")
        engine.dispose()
