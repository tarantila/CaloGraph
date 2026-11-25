"""Opt-in PostgreSQL migration and routing-CAS regression tests."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy import delete, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from alembic import command
from app.ai.connections import ConnectionProblem
from app.ai.routing import AIRoutingService
from app.ai.routing_contracts import AIRoutingEntryPut, AIRoutingPut, AIRoutingView
from app.config import settings
from app.database import SessionLocal, engine
from app.models import AIProviderConnection, AIRoutingConfiguration, AIRoutingEntry, User
from app.services.user_operation_lock import UserOperationBusy

POSTGRES_TESTS_ENABLED = (
    os.environ.get("CALOGRAPH_ALLOW_DESTRUCTIVE_POSTGRES_TESTS") == "1"
    and bool(os.environ.get("CALOGRAPH_POSTGRES_TEST_URL"))
)
PREVIOUS_REVISION = "20261125_0062"
TARGET_REVISION = "20261125_0063"


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_populated_0062_upgrade_routing_constraints_cascade_and_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert engine.dialect.name == "postgresql"
    base_url = make_url(os.environ["CALOGRAPH_POSTGRES_TEST_URL"])
    original_database_url = settings.database_url
    schema = f"routing_migration_{uuid4().hex}"
    admin_engine = sa.create_engine(base_url, pool_pre_ping=True)
    schema_engine: sa.Engine | None = None
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    schema_url = base_url.update_query_dict({"options": f"-csearch_path={schema}"})
    try:
        # Alembic passes this value through ConfigParser interpolation.
        isolated_url = schema_url.render_as_string(hide_password=False).replace("%", "%%")
        monkeypatch.setattr(settings, "database_url", isolated_url)
        config = Config("alembic.ini")
        command.upgrade(config, PREVIOUS_REVISION)
        schema_engine = sa.create_engine(schema_url, pool_pre_ping=True)
        factory = sessionmaker(bind=schema_engine, expire_on_commit=False)
        owner_id, connection_id = uuid4(), uuid4()
        with factory() as db:
            db.add(User(
                id=owner_id, username=f"routing-migration-{owner_id.hex}",
                password_hash="synthetic-only",
            ))
            db.flush()
            db.add(AIProviderConnection(
                user_id=owner_id, id=connection_id, label="Synthetic provider",
                provider_kind="openai_api", model_id="synthetic-model", enabled=True,
            ))
            db.commit()

        command.upgrade(config, TARGET_REVISION)
        with schema_engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM users WHERE id=:id"), {"id": owner_id}) == 1
            assert connection.scalar(text(
                "SELECT count(*) FROM ai_provider_connections WHERE user_id=:owner AND id=:connection"
            ), {"owner": owner_id, "connection": connection_id}) == 1
            inspector = sa.inspect(connection)
            assert {"user_id", "configuration_version", "updated_at"} == {
                column["name"] for column in inspector.get_columns("ai_routing_configurations")
            }
            foreign_keys = inspector.get_foreign_keys("ai_routing_entries")
            assert any(
                item["constrained_columns"] == ["user_id", "connection_id"]
                and item["referred_table"] == "ai_provider_connections"
                and item["referred_columns"] == ["user_id", "id"]
                for item in foreign_keys
            )
            assert any(
                item["constrained_columns"] == ["user_id"]
                and item["referred_table"] == "ai_routing_configurations"
                for item in foreign_keys
            )

        with factory() as db:
            db.add(AIRoutingConfiguration(user_id=owner_id, configuration_version=1))
            db.add(AIRoutingEntry(
                user_id=owner_id, connection_id=connection_id, position=0, enabled=False,
            ))
            db.commit()
        with pytest.raises(RuntimeError, match="persisted provider routing"):
            command.downgrade(config, PREVIOUS_REVISION)

        with factory.begin() as db:
            db.execute(delete(User).where(User.id == owner_id))
        with schema_engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM ai_routing_configurations")) == 0
            assert connection.scalar(text("SELECT count(*) FROM ai_routing_entries")) == 0
        command.downgrade(config, PREVIOUS_REVISION)
        with schema_engine.connect() as connection:
            assert "ai_routing_configurations" not in sa.inspect(connection).get_table_names()
            assert "ai_routing_entries" not in sa.inspect(connection).get_table_names()
    finally:
        if schema_engine is not None:
            schema_engine.dispose()
        monkeypatch.setattr(settings, "database_url", original_database_url)
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()


def _create_owner_and_connections() -> tuple[UUID, tuple[UUID, UUID]]:
    owner_id = uuid4()
    identifiers = (UUID(int=owner_id.int % (2**64) + 10), UUID(int=owner_id.int % (2**64) + 20))
    with SessionLocal() as db:
        db.add(User(
            id=owner_id, username=f"routing-concurrency-{owner_id.hex}",
            password_hash="synthetic-only",
        ))
        db.flush()
        for identifier in identifiers:
            db.add(AIProviderConnection(
                user_id=owner_id, id=identifier, label="Synthetic provider",
                provider_kind="openai_api", model_id="synthetic-model", enabled=True,
            ))
        db.commit()
    return owner_id, identifiers


def _contend_for_routing_write(
    monkeypatch: pytest.MonkeyPatch,
    owner_id: UUID,
    first: AIRoutingPut,
    competing: AIRoutingPut,
) -> None:
    entered_validation = Event()
    release_validation = Event()
    original_validate = AIRoutingService._validate

    def paused_validate(service: AIRoutingService, entries: tuple[AIRoutingEntryPut, ...]):
        if service._owner.id == owner_id and not entered_validation.is_set():
            entered_validation.set()
            if not release_validation.wait(timeout=10):
                raise AssertionError("timed out waiting to release the concurrent route update")
        return original_validate(service, entries)

    monkeypatch.setattr(AIRoutingService, "_validate", paused_validate)

    def first_writer() -> AIRoutingView:
        with SessionLocal() as db:
            owner = db.get(User, owner_id)
            assert owner is not None
            return AIRoutingService(db, owner=owner).put(first)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(first_writer)
        try:
            assert entered_validation.wait(timeout=10)
            with SessionLocal() as db:
                owner = db.get(User, owner_id)
                assert owner is not None
                with pytest.raises(UserOperationBusy):
                    AIRoutingService(db, owner=owner).put(competing)
        finally:
            release_validation.set()
        saved = future.result(timeout=15)

    with SessionLocal() as db:
        owner = db.get(User, owner_id)
        assert owner is not None
        with pytest.raises(ConnectionProblem) as stale:
            AIRoutingService(db, owner=owner).put(competing)
        assert stale.value.code == "ai-routing-version-conflict"
        root = db.get(AIRoutingConfiguration, owner_id)
        assert root is not None and root.configuration_version == first.expected_configuration_version + 1
        persisted = list(db.scalars(
            select(AIRoutingEntry).where(AIRoutingEntry.user_id == owner_id)
            .order_by(AIRoutingEntry.position)
        ))
        assert [(row.connection_id, row.enabled) for row in persisted] == [
            (item.connection_id, item.enabled) for item in first.entries
        ]
        assert [item.connection_id for item in saved.entries] == [item.connection_id for item in first.entries]
    with SessionLocal.begin() as db:
        db.execute(delete(User).where(User.id == owner_id))


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_concurrent_initial_routing_puts_serialize_expected_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert engine.dialect.name == "postgresql"
    owner_id, (first_id, second_id) = _create_owner_and_connections()
    first = AIRoutingPut(expected_configuration_version=0, entries=(
        AIRoutingEntryPut(connection_id=first_id, enabled=True),
        AIRoutingEntryPut(connection_id=second_id, enabled=False),
    ))
    competing = AIRoutingPut(expected_configuration_version=0, entries=(
        AIRoutingEntryPut(connection_id=second_id, enabled=True),
        AIRoutingEntryPut(connection_id=first_id, enabled=False),
    ))
    _contend_for_routing_write(monkeypatch, owner_id, first, competing)


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_concurrent_existing_routing_cas_updates_serialize_and_do_not_partially_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert engine.dialect.name == "postgresql"
    owner_id, (first_id, second_id) = _create_owner_and_connections()
    with SessionLocal() as db:
        owner = db.get(User, owner_id)
        assert owner is not None
        AIRoutingService(db, owner=owner).put(AIRoutingPut(expected_configuration_version=0, entries=(
            AIRoutingEntryPut(connection_id=first_id, enabled=True),
            AIRoutingEntryPut(connection_id=second_id, enabled=False),
        )))
    first = AIRoutingPut(expected_configuration_version=1, entries=(
        AIRoutingEntryPut(connection_id=second_id, enabled=True),
        AIRoutingEntryPut(connection_id=first_id, enabled=False),
    ))
    competing = AIRoutingPut(expected_configuration_version=1, entries=(
        AIRoutingEntryPut(connection_id=first_id, enabled=True),
        AIRoutingEntryPut(connection_id=second_id, enabled=False),
    ))
    _contend_for_routing_write(monkeypatch, owner_id, first, competing)
