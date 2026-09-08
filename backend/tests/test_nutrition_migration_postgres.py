from __future__ import annotations

import os

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command

POSTGRES_TESTS_ENABLED = (
    os.environ.get("CALOGRAPH_ALLOW_DESTRUCTIVE_POSTGRES_TESTS") == "1"
    and bool(os.environ.get("CALOGRAPH_POSTGRES_TEST_URL"))
)
PREVIOUS_REVISION = "20260906_0024"
TARGET_REVISION = "20260908_0025"


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL migration tests are not explicitly enabled",
)
def test_a1_migration_creates_scoped_append_only_schema():
    database_url = os.environ["CALOGRAPH_POSTGRES_TEST_URL"]
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    engine = sa.create_engine(database_url)
    expected_tables = {
        "nutrition_ingestion_runs",
        "nutrition_source_observations",
        "nutrition_external_identities",
        "nutrition_external_identity_links",
        "nutrition_consumption_events",
        "nutrition_food_profiles",
        "nutrition_food_snapshots",
        "nutrition_serving_observations",
        "nutrition_field_observations",
        "nutrition_source_tombstones",
        "nutrition_provenance",
    }

    try:
        command.downgrade(config, PREVIOUS_REVISION)
        command.upgrade(config, TARGET_REVISION)
        inspector = sa.inspect(engine)
        assert expected_tables <= set(inspector.get_table_names())
        assert not {"nutrition_source_priority_rules", "nutrition_projections"} & set(
            inspector.get_table_names()
        )
        link_constraints = {
            item["name"] for item in inspector.get_foreign_keys("nutrition_external_identity_links")
        }
        assert "fk_nutrition_identity_links_supersedes" in link_constraints
        assert "fk_nutrition_identity_links_identity_user" in link_constraints
    finally:
        command.downgrade(config, PREVIOUS_REVISION)
        engine.dispose()
