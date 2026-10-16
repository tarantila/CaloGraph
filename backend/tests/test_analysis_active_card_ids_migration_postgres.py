from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.database import engine as application_engine

POSTGRES_TESTS_ENABLED = (
    os.environ.get("CALOGRAPH_ALLOW_DESTRUCTIVE_POSTGRES_TESTS") == "1"
    and bool(os.environ.get("CALOGRAPH_POSTGRES_TEST_URL"))
)
PREVIOUS_REVISION = "20261005_0038"
TARGET_REVISION = "20261006_0039"


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_active_card_ids_migration_persists_user_scoped_ordered_backfill() -> None:
    assert application_engine.dialect.name == "postgresql"
    database_url = os.environ["CALOGRAPH_POSTGRES_TEST_URL"]
    alembic_config = Config("alembic.ini")
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    engine = create_engine(database_url, pool_pre_ping=True)

    first_user_id, second_user_id = uuid4(), uuid4()
    now = datetime.now(UTC)
    stamped_head = False

    def insert_user(
        connection: sa.Connection,
        user_id: object,
        summary_order: list[str],
    ) -> None:
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, username, password_hash, language, timezone, week_starts_on, "
                "preferred_weight_unit, raw_payload_retention_days, is_active, is_admin, "
                "summary_order, created_at, updated_at) VALUES "
                "(:id, :username, 'synthetic-password-hash', 'de', 'Europe/Berlin', "
                "1, 'kg', 0, true, false, CAST(:summary_order AS JSON), :now, :now)"
            ),
            {
                "id": user_id,
                "username": f"active-card-migration-{user_id}",
                "summary_order": json.dumps(summary_order),
                "now": now,
            },
        )

    try:
        # The test database is already at the current schema; move it back to
        # the revision immediately before the migration under test.
        command.stamp(alembic_config, "head")
        stamped_head = True
        command.downgrade(alembic_config, PREVIOUS_REVISION)
        with engine.begin() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == PREVIOUS_REVISION
            assert "active_card_ids" not in {
                column["name"] for column in sa.inspect(connection).get_columns("users")
            }

            insert_user(
                connection,
                first_user_id,
                ["activity", "nutrition", "weight", "micronutrients"],
            )
            insert_user(
                connection,
                second_user_id,
                ["micronutrients", "weight", "activity", "nutrition"],
            )

            # Rows are ordered, user-scoped legacy preferences. Aliases already
            # represented by each user's summary order must not be duplicated.
            connection.execute(
                text(
                    "INSERT INTO user_analysis_preferences "
                    "(user_id, analysis_key, position, created_at, updated_at) "
                    "VALUES (:user_id, :analysis_key, :position, :now, :now)"
                ),
                [
                    {"user_id": first_user_id, "analysis_key": "weight_kg", "position": 0, "now": now},
                    {"user_id": first_user_id, "analysis_key": "vitamin_a_ug", "position": 1, "now": now},
                    {"user_id": first_user_id, "analysis_key": "active_energy_kcal", "position": 2, "now": now},
                    {"user_id": first_user_id, "analysis_key": "protein_g", "position": 3, "now": now},
                    {"user_id": second_user_id, "analysis_key": "fat_g", "position": 0, "now": now},
                    {"user_id": second_user_id, "analysis_key": "weight_kg", "position": 1, "now": now},
                    {"user_id": second_user_id, "analysis_key": "calcium_mg", "position": 2, "now": now},
                ],
            )

        # Run Alembic's real revision upgrade against the persisted legacy rows.
        command.upgrade(alembic_config, TARGET_REVISION)
        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == TARGET_REVISION
            rows = connection.execute(
                text(
                    "SELECT id, active_card_ids FROM users "
                    "WHERE id IN (:first_user_id, :second_user_id)"
                ),
                {"first_user_id": first_user_id, "second_user_id": second_user_id},
            ).all()

        active_cards_by_user = {user_id: card_ids for user_id, card_ids in rows}
        assert active_cards_by_user == {
            first_user_id: [
                "activity",
                "nutrition",
                "weight",
                "micronutrients",
                "vitamin_a_ug",
                "protein_g",
            ],
            second_user_id: [
                "micronutrients",
                "weight",
                "activity",
                "nutrition",
                "fat_g",
                "calcium_mg",
            ],
        }
        assert "active_energy_kcal" not in active_cards_by_user[first_user_id]
        assert "weight_kg" not in active_cards_by_user[first_user_id]
        assert "weight_kg" not in active_cards_by_user[second_user_id]
        assert "vitamin_a_ug" not in active_cards_by_user[second_user_id]
        assert "fat_g" not in active_cards_by_user[first_user_id]
        assert "protein_g" not in active_cards_by_user[second_user_id]
    finally:
        if stamped_head:
            try:
                with engine.begin() as connection:
                    connection.execute(
                        text("DELETE FROM users WHERE id = :user_id"),
                        [{"user_id": first_user_id}, {"user_id": second_user_id}],
                    )
                command.upgrade(alembic_config, "head")
            finally:
                engine.dispose()
