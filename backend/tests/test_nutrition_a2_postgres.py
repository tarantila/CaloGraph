from __future__ import annotations

import os
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alembic import command
from app.models import (
    NutritionDailyProjection,
    NutritionProjectionHead,
    SourcePriorityPolicy,
)

POSTGRES_TESTS_ENABLED = (
    os.environ.get("CALOGRAPH_ALLOW_DESTRUCTIVE_POSTGRES_TESTS") == "1"
    and bool(os.environ.get("CALOGRAPH_POSTGRES_TEST_URL"))
)
PREVIOUS_REVISION = "20260908_0025"
TARGET_REVISION = "20260908_0026"


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL migration tests are not explicitly enabled",
)
def test_a2_postgres_catalog_and_cross_scope_constraints():
    database_url = os.environ["CALOGRAPH_POSTGRES_TEST_URL"]
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    engine = sa.create_engine(database_url)

    try:
        command.downgrade(config, "base")
        command.upgrade(config, PREVIOUS_REVISION)
        command.upgrade(config, TARGET_REVISION)
        inspector = sa.inspect(engine)

        expected_tables = {
            "source_priority_policies",
            "source_priority_rules",
            "nutrition_daily_projections",
            "nutrition_daily_projection_facts",
            "nutrition_daily_projection_lineage",
            "nutrition_projection_heads",
        }
        assert expected_tables <= set(inspector.get_table_names())

        rule_indexes = {item["name"] for item in inspector.get_indexes("source_priority_rules")}
        assert {
            "uq_source_priority_rules_wildcard_provider",
            "uq_source_priority_rules_wildcard_rank",
            "uq_source_priority_rules_metric_provider",
            "uq_source_priority_rules_metric_rank",
        } <= rule_indexes

        expected_fk_names = {
            "source_priority_rules": {"fk_source_priority_rules_policy_user"},
            "nutrition_daily_projections": {"fk_nutrition_projections_policy_user"},
            "nutrition_daily_projection_facts": {
                "fk_nutrition_projection_facts_projection_user"
            },
            "nutrition_daily_projection_lineage": {
                "fk_nutrition_projection_lineage_fact_user",
                "fk_nutrition_projection_lineage_source_user",
            },
            "nutrition_projection_heads": {"fk_nutrition_projection_heads_projection_scope"},
        }
        for table, names in expected_fk_names.items():
            actual = {item["name"] for item in inspector.get_foreign_keys(table)}
            assert names <= actual

        fact_columns = {
            item["name"]: item["type"]
            for item in inspector.get_columns("nutrition_daily_projection_facts")
        }
        lineage_columns = {
            item["name"]: item["type"]
            for item in inspector.get_columns("nutrition_daily_projection_lineage")
        }
        assert (fact_columns["value"].precision, fact_columns["value"].scale) == (24, 12)
        assert (lineage_columns["contribution_value"].precision, lineage_columns["contribution_value"].scale) == (
            24,
            12,
        )

        with Session(engine) as db:
            users = sa.Table("users", sa.MetaData(), autoload_with=engine)
            first_id, second_id = uuid4(), uuid4()
            now = datetime.now(UTC)
            db.execute(
                users.insert(),
                [
                    {
                        "id": first_id,
                        "username": "a2-postgres-first",
                        "password_hash": "hash",
                        "language": "de",
                        "timezone": "UTC",
                        "week_starts_on": 0,
                        "preferred_weight_unit": "kg",
                        "raw_payload_retention_days": 0,
                        "is_active": True,
                        "is_admin": False,
                        "created_at": now,
                        "updated_at": now,
                    },
                    {
                        "id": second_id,
                        "username": "a2-postgres-second",
                        "password_hash": "hash",
                        "language": "de",
                        "timezone": "UTC",
                        "week_starts_on": 0,
                        "preferred_weight_unit": "kg",
                        "raw_payload_retention_days": 0,
                        "is_active": True,
                        "is_admin": False,
                        "created_at": now,
                        "updated_at": now,
                    },
                ],
            )
            policy = SourcePriorityPolicy(
                user_id=first_id,
                version=1,
                effective_from=datetime(2026, 1, 1, tzinfo=UTC),
            )
            db.add(policy)
            db.commit()

            db.add(
                NutritionDailyProjection(
                    user_id=second_id,
                    local_date=date(2026, 9, 7),
                    projection_version=1,
                    projection_algorithm_version="cross-user",
                    priority_policy_id=policy.id,
                    input_watermark="postgres-test",
                    projection_status="ready",
                )
            )
            with pytest.raises(IntegrityError):
                db.commit()
            db.rollback()

            valid = NutritionDailyProjection(
                user_id=first_id,
                local_date=date(2026, 9, 7),
                projection_version=1,
                projection_algorithm_version="a2-postgres",
                priority_policy_id=policy.id,
                input_watermark="postgres-test",
                projection_status="ready",
            )
            db.add(valid)
            db.commit()
            db.refresh(valid)

            db.add(
                NutritionProjectionHead(
                    user_id=first_id,
                    local_date=date(2026, 9, 8),
                    current_projection_id=valid.id,
                )
            )
            with pytest.raises(IntegrityError):
                db.commit()
            db.rollback()

            db.add(
                NutritionProjectionHead(
                    user_id=second_id,
                    local_date=date(2026, 9, 7),
                    current_projection_id=valid.id,
                )
            )
            with pytest.raises(IntegrityError):
                db.commit()
            db.rollback()
    finally:
        command.downgrade(config, "base")
        command.upgrade(config, "head")
        engine.dispose()
