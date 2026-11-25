import importlib.util
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.ai.connections import ConnectionProblem, ProviderConnectionService
from app.ai.preferences import AISettingsPut, AISettingsService
from app.ai.routing import AIRoutingService
from app.ai.routing_contracts import AIRoutingEntryPut, AIRoutingPut
from app.models import AIRoutingConfiguration, AIRoutingEntry, User
from tests.test_ai_connections import KEY, login, payload


def test_get_is_read_only_and_projects_legacy_preference_first(db: Session, user: User) -> None:
    connections = ProviderConnectionService(db, owner=user)
    ids = [uuid4(), uuid4()]
    for identifier in ids:
        connections.put(identifier, payload(api_key=KEY, enabled=True))
    AISettingsService(db, owner=user).put(AISettingsPut(active_connection_id=ids[1], configuration_version=1))
    result = AIRoutingService(db, owner=user).get()
    assert result.configuration_version == 0
    assert [item.connection_id for item in result.entries] == [ids[1], ids[0]]
    assert [item.enabled for item in result.entries] == [True, False]
    assert result.primary_connection_id == ids[1]
    assert db.get(AIRoutingConfiguration, user.id) is None


def test_order_activation_idempotency_new_draft_and_legacy_preferences(db: Session, user: User) -> None:
    connections = ProviderConnectionService(db, owner=user)
    ids = [uuid4(), uuid4()]
    for identifier in ids:
        connections.put(identifier, payload(api_key=KEY, enabled=True))
    service = AIRoutingService(db, owner=user)
    request = AIRoutingPut(expected_configuration_version=0, entries=(
        AIRoutingEntryPut(connection_id=ids[1], enabled=True),
        AIRoutingEntryPut(connection_id=ids[0], enabled=True),
    ))
    first = service.put(request)
    assert first.configuration_version == 1 and first.primary_connection_id == ids[1]
    assert service.put(request) == first

    draft_id = uuid4()
    connections.put(draft_id, payload(model_id=None))
    appended = service.get()
    assert appended.entries[-1].connection_id == draft_id and not appended.entries[-1].enabled

    selected = connections.get(ids[0])
    legacy = AISettingsService(db, owner=user).put(AISettingsPut(
        active_connection_id=ids[0], configuration_version=selected.configuration_version,
    ))
    assert legacy.active_connection_id == ids[0]
    updated = service.get()
    assert updated.configuration_version == 2
    assert updated.entries[0].connection_id == ids[0] and updated.entries[0].enabled
    assert sum(item.enabled for item in updated.entries) == 1
    cleared = AISettingsService(db, owner=user).put(AISettingsPut(active_connection_id=None, configuration_version=None))
    assert cleared.active_connection_id is None
    assert service.get().primary_connection_id is None


def test_projected_connections_persist_in_order_before_later_uuid_additions(db: Session, user: User) -> None:
    connections = ProviderConnectionService(db, owner=user)
    first_id, second_id, later_lower_id = (UUID(int=value) for value in (100, 300, 200))
    connections.put(first_id, payload(api_key=KEY, enabled=True))
    service = AIRoutingService(db, owner=user)
    first = service.put(AIRoutingPut(expected_configuration_version=0, entries=(
        AIRoutingEntryPut(connection_id=first_id, enabled=True),
    )))
    assert first.configuration_version == 1

    connections.put(second_id, payload(api_key=KEY, enabled=True))
    projected = service.get()
    assert [item.connection_id for item in projected.entries] == [first_id, second_id]
    persisted = service.put(AIRoutingPut(
        expected_configuration_version=projected.configuration_version,
        entries=tuple(AIRoutingEntryPut(connection_id=item.connection_id, enabled=item.enabled) for item in projected.entries),
    ))
    assert persisted.configuration_version == 2
    assert [item.connection_id for item in persisted.entries] == [first_id, second_id]

    connections.put(later_lower_id, payload(api_key=KEY, enabled=True))
    later = service.get()
    assert [item.connection_id for item in later.entries] == [first_id, second_id, later_lower_id]
    saved = service.put(AIRoutingPut(
        expected_configuration_version=later.configuration_version,
        entries=tuple(AIRoutingEntryPut(connection_id=item.connection_id, enabled=item.enabled) for item in later.entries),
    ))
    assert saved.configuration_version == 3
    assert [item.connection_id for item in saved.entries] == [first_id, second_id, later_lower_id]

    connections.delete(second_id)
    after_delete = service.get()
    assert after_delete.configuration_version == 4
    assert [item.connection_id for item in after_delete.entries] == [first_id, later_lower_id]


def test_unready_first_enabled_route_does_not_promote_ready_backup(db: Session, user: User) -> None:
    connections = ProviderConnectionService(db, owner=user)
    ids = [uuid4(), uuid4()]
    for identifier in ids:
        connections.put(identifier, payload(api_key=KEY, enabled=True))
    service = AIRoutingService(db, owner=user)
    service.put(AIRoutingPut(expected_configuration_version=0, entries=tuple(
        AIRoutingEntryPut(connection_id=identifier, enabled=True) for identifier in ids
    )))
    first = connections._record(ids[0])
    assert first is not None
    first.provider_kind = "chatgpt_plan"
    db.commit()
    result = service.get()
    assert result.entries[0].enabled and not result.entries[0].ready
    assert result.entries[1].enabled and result.entries[1].ready
    assert result.primary_connection_id is None


def test_validation_owner_scope_version_and_deletion(db: Session, user: User) -> None:
    other = User(username="other-routing-owner", password_hash="synthetic-only")
    db.add(other)
    db.commit()
    identifier = uuid4()
    ProviderConnectionService(db, owner=user).put(identifier, payload(api_key=KEY, enabled=True))
    with pytest.raises(ConnectionProblem) as missing:
        AIRoutingService(db, owner=other).put(AIRoutingPut(expected_configuration_version=0, entries=(
            AIRoutingEntryPut(connection_id=identifier, enabled=True),
        )))
    assert missing.value.status == 409
    with pytest.raises(ConnectionProblem) as duplicate:
        AIRoutingService(db, owner=user).put(AIRoutingPut(expected_configuration_version=0, entries=(
            AIRoutingEntryPut(connection_id=identifier, enabled=False),
            AIRoutingEntryPut(connection_id=identifier, enabled=False),
        )))
    assert duplicate.value.code == "ai-routing-duplicate-connection"

    service = AIRoutingService(db, owner=user)
    request = AIRoutingPut(expected_configuration_version=0, entries=(AIRoutingEntryPut(connection_id=identifier, enabled=True),))
    service.put(request)
    with pytest.raises(ConnectionProblem) as stale:
        service.put(AIRoutingPut(expected_configuration_version=0, entries=(AIRoutingEntryPut(connection_id=identifier, enabled=False),)))
    assert stale.value.status == 409
    ProviderConnectionService(db, owner=user).delete(identifier)
    root = db.get(AIRoutingConfiguration, user.id)
    assert root is not None and root.configuration_version == 2
    assert db.get(AIRoutingEntry, (user.id, identifier)) is None
    assert AIRoutingService(db, owner=user).get().entries == ()
    assert AIRoutingService(db, owner=other).get().entries == ()


def test_routing_api_auth_csrf_and_closed_input(client: TestClient, user: User) -> None:
    path = "/api/v1/settings/ai/routing"
    assert client.get(path).status_code == 401
    csrf = login(client)
    assert client.put(path, json={"expected_configuration_version": 0, "entries": []}).status_code == 403
    headers = {"X-CSRF-Token": csrf}
    assert client.get(path).json() == {"configuration_version": 0, "entries": [], "primary_connection_id": None}
    assert client.put(path, headers=headers, json={
        "expected_configuration_version": 0, "entries": [], "user_id": str(user.id),
    }).status_code == 422
    assert client.put(path, headers=headers, json={"expected_configuration_version": 0, "entries": []}).json() == {
        "configuration_version": 1, "entries": [], "primary_connection_id": None,
    }


def test_routing_migration_schema_and_downgrade_guard() -> None:
    path = Path(__file__).parents[1] / "alembic/versions/20261125_0063_ai_provider_routing.py"
    spec = importlib.util.spec_from_file_location("ai_routing_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE users (id CHAR(32) PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE ai_provider_connections (user_id CHAR(32), id CHAR(32), PRIMARY KEY (user_id, id))")
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()
            assert {"user_id", "configuration_version", "updated_at"} == {
                item["name"] for item in sa.inspect(connection).get_columns("ai_routing_configurations")
            }
            assert {"user_id", "connection_id", "position", "enabled"} == {
                item["name"] for item in sa.inspect(connection).get_columns("ai_routing_entries")
            }
            migration.downgrade()
            migration.upgrade()
            connection.exec_driver_sql("INSERT INTO users VALUES ('owner')")
            connection.exec_driver_sql(
                "INSERT INTO ai_routing_configurations (user_id, configuration_version, updated_at) "
                "VALUES ('owner', 1, '2026-11-18T00:00:00+00:00')"
            )
            with pytest.raises(RuntimeError, match="persisted provider routing"):
                migration.downgrade()
