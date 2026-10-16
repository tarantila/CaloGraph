from __future__ import annotations

import os
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from alembic import command
from app.database import engine as application_engine
from app.models import NutritionTarget

ENABLED = (
    os.environ.get("CALOGRAPH_ALLOW_DESTRUCTIVE_POSTGRES_TESTS") == "1"
    and bool(os.environ.get("CALOGRAPH_POSTGRES_TEST_URL"))
)
DEFAULT_ORDER = ["weight", "nutrition", "micronutrients", "activity"]
PREVIOUS = "20260927_0037"
TARGET = "20261005_0038"


@pytest.mark.skipif(not ENABLED, reason="requires explicitly isolated PostgreSQL test database")
def test_summary_order_migration_preserves_existing_data_and_defaults() -> None:
    assert application_engine.dialect.name == "postgresql"
    url = os.environ["CALOGRAPH_POSTGRES_TEST_URL"]
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    engine = create_engine(url, pool_pre_ping=True)
    owner_id, other_id, new_id = uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    stamped_head = False

    def insert_user(connection: sa.Connection, user_id: object) -> None:
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, username, password_hash, language, timezone, week_starts_on, "
                "preferred_weight_unit, raw_payload_retention_days, is_active, is_admin, "
                "created_at, updated_at) VALUES "
                "(:id, :username, 'synthetic-password-hash', 'de', 'Europe/Berlin', "
                "1, 'kg', 0, true, false, :now, :now)"
            ),
            {"id": user_id, "username": f"summary-test-{user_id}", "now": now},
        )

    try:
        # conftest creates the current model schema, so stamp its matching Alembic head.
        command.stamp(config, "head")
        stamped_head = True
        command.downgrade(config, PREVIOUS)
        with engine.begin() as connection:
            before_tables = set(sa.inspect(connection).get_table_names())
            user_columns = {c["name"] for c in sa.inspect(connection).get_columns("users")}
            assert "summary_order" not in user_columns
            assert "active_card_ids" not in user_columns
            insert_user(connection, owner_id)
            insert_user(connection, other_id)
            connection.execute(
                text(
                    "INSERT INTO user_analysis_preferences "
                    "(user_id, analysis_key, position, created_at, updated_at) "
                    "VALUES (:id, 'vitamin_a_ug', 0, :now, :now)"
                ),
                {"id": owner_id, "now": now},
            )
            before_users = connection.execute(
                text("SELECT id, username FROM users ORDER BY username")
            ).all()
        with Session(engine) as db:
            db.add(
                NutritionTarget(
                    user_id=owner_id,
                    valid_from=date(2024, 1, 1),
                    calories_kcal=Decimal("2000"),
                    protein_g=Decimal("120"),
                )
            )
            db.commit()
        with engine.connect() as connection:
            before_targets = connection.execute(
                text(
                    "SELECT id, user_id, valid_from, calories_kcal, protein_g "
                    "FROM nutrition_targets ORDER BY id"
                )
            ).all()
        command.upgrade(config, TARGET)
        command.upgrade(config, TARGET)
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == TARGET
            assert "active_card_ids" not in {
                c["name"] for c in sa.inspect(connection).get_columns("users")
            }
            assert set(sa.inspect(connection).get_table_names()) == before_tables
            column = {
                c["name"]: c for c in sa.inspect(connection).get_columns("users")
            }["summary_order"]
            assert isinstance(column["type"], sa.JSON)
            assert column["nullable"] is False
            assert column["default"] is not None
            assert connection.execute(
                text("SELECT id, username FROM users ORDER BY username")
            ).all() == before_users
            assert connection.execute(
                text(
                    "SELECT id, user_id, valid_from, calories_kcal, protein_g "
                    "FROM nutrition_targets ORDER BY id"
                )
            ).all() == before_targets
            assert connection.execute(
                text(
                    "SELECT analysis_key, position FROM user_analysis_preferences "
                    "WHERE user_id=:id ORDER BY position"
                ),
                {"id": owner_id},
            ).all() == [("vitamin_a_ug", 0)]
            for user_id in (owner_id, other_id):
                assert connection.scalar(
                    text("SELECT summary_order FROM users WHERE id=:id"),
                    {"id": user_id},
                ) == DEFAULT_ORDER
        with engine.begin() as connection:
            insert_user(connection, new_id)
            connection.execute(
                text("UPDATE users SET summary_order=CAST(:order AS JSON) WHERE id=:id"),
                {"id": owner_id, "order": '["activity","weight","nutrition","micronutrients"]'},
            )
        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT summary_order FROM users WHERE id=:id"),
                {"id": owner_id},
            ) == ["activity", "weight", "nutrition", "micronutrients"]
            for user_id in (other_id, new_id):
                assert connection.scalar(
                    text("SELECT summary_order FROM users WHERE id=:id"),
                    {"id": user_id},
                ) == DEFAULT_ORDER
        with pytest.raises(sa.exc.DBAPIError), engine.begin() as connection:
            connection.execute(
                text("UPDATE users SET summary_order=NULL WHERE id=:id"),
                {"id": owner_id},
            )
    finally:
        if stamped_head:
            command.upgrade(config, "head")
        engine.dispose()
