from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.ai.connections import ConnectionProblem, ProviderConnectionService
from app.ai.preferences import AISettingsPut, AISettingsService
from app.database import SessionLocal
from app.models import User
from tests.test_ai_connections import KEY, login, payload


def test_selection_is_account_local_idempotent_and_cleared_when_disabled_or_deleted(db: Session, user: User) -> None:
    other = User(username="other-selection-owner", password_hash="synthetic-only")
    db.add(other)
    db.commit()
    connections = ProviderConnectionService(db, owner=user)
    service = AISettingsService(db, owner=user)
    identifier = uuid4()
    connection, _ = connections.put(identifier, payload(api_key=KEY, enabled=True))
    assert service.get().active_connection_id is None
    selection = AISettingsPut(active_connection_id=identifier, configuration_version=connection.configuration_version)
    first = service.put(selection)
    assert service.put(selection) == first
    assert AISettingsService(db, owner=other).get().active_connection_id is None
    with pytest.raises(ConnectionProblem) as error:
        AISettingsService(db, owner=other).put(selection)
    assert error.value.status == 404
    connections.put(identifier, payload(enabled=False))
    assert service.get().active_connection_id is None
    with pytest.raises(ConnectionProblem):
        service.put(selection)
    connection, _ = connections.put(identifier, payload(enabled=True))
    with pytest.raises(ConnectionProblem) as error:
        service.put(selection)
    assert error.value.code == "ai-configuration-changed"
    service.put(AISettingsPut(active_connection_id=identifier, configuration_version=connection.configuration_version))
    connections.delete(identifier)
    assert service.get().active_connection_id is None


def test_preferences_rest_auth_csrf_validation_and_no_outbound_work(client: TestClient, db: Session, user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ai import probes
    monkeypatch.setattr(probes, "fetch_provider_json", lambda *args, **kwargs: pytest.fail("selection must never contact provider"))
    path = "/api/v1/settings/ai/preferences"
    assert client.get(path).status_code == 401
    csrf = login(client)
    body = {"active_connection_id": None, "configuration_version": None}
    assert client.put(path, json=body).status_code == 403
    headers = {"X-CSRF-Token": csrf}
    assert client.put(path, headers=headers, json=body).json() == {"active_connection_id": None}
    for invalid in ({}, {"active_connection_id": str(uuid4()), "configuration_version": None}, {**body, "user_id": str(user.id)}):
        response = client.put(path, headers=headers, json=invalid)
        assert response.status_code == 422
    connection, _ = ProviderConnectionService(db, owner=user).put(uuid4(), payload(api_key=KEY, enabled=True))
    response = client.put(path, headers=headers, json={"active_connection_id": str(connection.id), "configuration_version": 1})
    assert response.status_code == 200 and response.json()["active_connection_id"] == str(connection.id)
    assert KEY not in response.text
    assert client.get(path).json() == response.json()


def test_concurrent_delete_during_selection_read_returns_a_valid_snapshot(
    client: TestClient, db: Session, user: User,
) -> None:
    identifier = uuid4()
    connection, _ = ProviderConnectionService(db, owner=user).put(
        identifier, payload(api_key=KEY, enabled=True),
    )
    AISettingsService(db, owner=user).put(
        AISettingsPut(active_connection_id=identifier, configuration_version=connection.configuration_version),
    )
    login(client)
    owner_id = user.id
    deleted = False

    def delete_after_selection_read(state):
        nonlocal deleted
        if deleted or not state.is_select or "ai_active_connections" not in str(state.statement):
            return state.invoke_statement()
        # Finish the reader's SQL snapshot, then delete in another session before
        # the application consumes it. A subsequent lookup must not turn this
        # otherwise valid read into an unhandled not-found exception.
        snapshot = state.invoke_statement().freeze()
        deleted = True
        with SessionLocal() as writer:
            owner = writer.get(User, owner_id)
            assert owner is not None
            ProviderConnectionService(writer, owner=owner).delete(identifier)
        return snapshot()

    event.listen(Session, "do_orm_execute", delete_after_selection_read, retval=True)
    try:
        response = client.get("/api/v1/settings/ai/preferences")
        assert deleted
        assert response.status_code == 200
        assert response.json()["active_connection_id"] in (None, str(identifier))
        assert client.get("/api/v1/settings/ai/preferences").json() == {"active_connection_id": None}
    finally:
        event.remove(Session, "do_orm_execute", delete_after_selection_read)
