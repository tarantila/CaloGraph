from __future__ import annotations

import os
from uuid import uuid4

import pytest
import sqlalchemy as sa
import test_yazio_simple_product_presence_migration as simple_product_presence_migration
from alembic.config import Config
from sqlalchemy.engine import make_url
from test_yazio_projection_pending_migration import (
    COMPATIBILITY_TABLES,
    EVIDENCE_TABLES,
    INDEX_NAME,
    TABLE_NAME,
    _assert_pending_schema,
    _old_columns,
    _seed,
    _snapshot,
)

from alembic import command
from app.config import settings
from app.database import engine as application_engine

POSTGRES_TESTS_ENABLED = (
    os.environ.get("CALOGRAPH_ALLOW_DESTRUCTIVE_POSTGRES_TESTS") == "1"
    and bool(os.environ.get("CALOGRAPH_POSTGRES_TEST_URL"))
)
pytestmark = pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL migration tests are not explicitly enabled",
)


@pytest.fixture
def isolated_postgres(monkeypatch):
    # conftest additionally requires the allowlisted local host, _test database
    # name, and equal application/integration URLs before opening its engine.
    database_url = os.environ["CALOGRAPH_POSTGRES_TEST_URL"]
    assert settings.environment == "test"
    assert application_engine.dialect.name == "postgresql"
    assert application_engine.url == make_url(database_url)
    schema = f"yazio_pending_{uuid4().hex}"
    admin_engine = sa.create_engine(database_url)
    schema_url = make_url(database_url).update_query_dict({"options": f"-csearch_path={schema}"})
    # Alembic env.py passes settings.database_url through ConfigParser again.
    isolated_url = schema_url.render_as_string(hide_password=False).replace("%", "%%")
    engine = sa.create_engine(schema_url)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", isolated_url)
    # env.py reads this object rather than trusting Config.sqlalchemy.url.
    monkeypatch.setattr(settings, "database_url", isolated_url)
    monkeypatch.setenv("DATABASE_URL", isolated_url)
    try:
        with admin_engine.begin() as connection:
            connection.execute(sa.schema.CreateSchema(schema))
        yield engine, config
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(sa.schema.DropSchema(schema, cascade=True, if_exists=True))
        admin_engine.dispose()


def test_simple_product_presence_migration_is_narrow_and_idempotent_on_postgres(isolated_postgres):
    engine, _ = isolated_postgres
    source_ids, event_ids = simple_product_presence_migration._seed(engine)
    before = simple_product_presence_migration._snapshot(engine)
    expected = simple_product_presence_migration._expected_after(before, source_ids, event_ids)

    simple_product_presence_migration._apply(engine)

    assert simple_product_presence_migration._snapshot(engine) == expected
    simple_product_presence_migration._apply(engine)
    assert simple_product_presence_migration._snapshot(engine) == expected


def test_full_postgres_chain_has_one_pending_column_and_index(isolated_postgres):
    engine, config = isolated_postgres
    with engine.connect() as connection:
        assert sa.inspect(connection).get_table_names() == []

    command.upgrade(config, "head")

    _assert_pending_schema(engine)
    with engine.connect() as connection:
        assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "20261026_0041"


def test_existing_postgres_schema_upgrade_backfills_only_completed_canonical_sdk_ranges(isolated_postgres):
    engine, config = isolated_postgres
    eligible_sdk_run_ids = _seed(engine)
    old_columns_before = _old_columns(engine)
    compatibility_rows_before = _snapshot(engine, COMPATIBILITY_TABLES + EVIDENCE_TABLES)
    assert "projection_pending" not in {
        column["name"] for column in sa.inspect(engine).get_columns(TABLE_NAME)
    }
    # A genuine frozen pre-column schema, not create_all(new-model) followed
    # by a stamped upgrade that would silently skip the column-add branch.
    command.stamp(config, "20261006_0039")

    command.upgrade(config, "20261021_0040")

    _assert_pending_schema(engine)
    with engine.connect() as connection:
        table = sa.Table(TABLE_NAME, sa.MetaData(), autoload_with=connection)
        pending_ids = set(connection.scalars(sa.select(table.c.id).where(table.c.projection_pending.is_(True))))
    assert pending_ids == eligible_sdk_run_ids
    old_columns_after = _old_columns(engine)
    compatibility_rows_after = _snapshot(engine, COMPATIBILITY_TABLES + EVIDENCE_TABLES)
    assert old_columns_after == old_columns_before
    assert compatibility_rows_after == compatibility_rows_before

    # The database default must work independently of the Python model.
    with engine.begin() as connection:
        table = sa.Table(TABLE_NAME, sa.MetaData(), autoload_with=connection)
        values = {**old_columns_before[0], "id": uuid4()}
        connection.execute(table.insert().values(**values))
        assert connection.scalar(sa.select(table.c.projection_pending).where(table.c.id == values["id"])) is False
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as connection:
        connection.execute(table.insert().values(**{**values, "id": uuid4(), "projection_pending": None}))

    old_columns_before = _old_columns(engine)
    command.downgrade(config, "20261006_0039")
    assert "projection_pending" not in {
        column["name"] for column in sa.inspect(engine).get_columns(TABLE_NAME)
    }
    assert INDEX_NAME not in {index["name"] for index in sa.inspect(engine).get_indexes(TABLE_NAME)}
    assert _old_columns(engine) == old_columns_before
    assert _snapshot(engine, COMPATIBILITY_TABLES + EVIDENCE_TABLES) == compatibility_rows_before
