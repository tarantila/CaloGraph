from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session

import app.ai.connections as connection_module
import app.ai.consents as consent_module
from app.ai.connections import ConnectionProblem, ProviderConnectionService
from app.ai.connections_contracts import ProviderConnectionPut
from app.ai.consents import AIConsentService
from app.ai.consents_contracts import (
    CONSENT_POLICY_VERSION,
    AIConsentListQuery,
    AIConsentPut,
    ConsentDataUse,
)
from app.models import AIConsent, AIConsentRevocation, User
from app.services.user_operation_lock import InactiveUserOperation
from tests.test_ai_connections import KEY, login

PATH = "/api/v1/settings/ai/consents"
CONNECTION_ID = UUID(int=100)


def make_connection(db: Session, user: User, *, changes: dict | None = None) -> UUID:
    identifier = CONNECTION_ID if changes is None else uuid4()
    payload = ProviderConnectionPut.model_validate({
        "label": "Nutrition provider", "provider_kind": "openai_api",
        "model_id": "synthetic-model", "api_key": KEY, "enabled": True,
        **(changes or {}),
    })
    ProviderConnectionService(db, owner=user).put(identifier, payload)
    return identifier


def consent_payload(connection_id: UUID, **changes: object) -> dict:
    return {
        "connection_id": str(connection_id), "configuration_version": 1,
        "purpose": "nutrition_chat", "data_domains": ["nutrition"],
        "include_meal_names": False,
        "history": {"mode": "date_range", "start": "2026-01-01", "end": "2026-02-28"},
        "policy_version": CONSENT_POLICY_VERSION, **changes,
    }


def test_contract_rejects_ambiguous_or_escalating_payloads() -> None:
    valid = consent_payload(CONNECTION_ID)
    invalids = [
        {**valid, "data_domains": []},
        {**valid, "data_domains": ["nutrition", "nutrition"]},
        {**valid, "data_domains": ["nutrition", "unknown"]},
        {**valid, "data_domains": ["nutrition"], "include_meal_names": None},
        {**valid, "data_domains": ["weight"], "include_meal_names": True},
        {**valid, "history": {"mode": "all_available", "start": "2026-01-01", "end": None}},
        {**valid, "history": {"mode": "date_range", "start": None, "end": None}},
        {**valid, "history": {"mode": "date_range", "start": "2026-02-01", "end": "2026-01-01"}},
        {**valid, "policy_version": "old"},
        {**valid, "user_id": str(uuid4())},
    ]
    for candidate in invalids:
        with pytest.raises(ValidationError):
            AIConsentPut.model_validate(candidate)
    canonical = AIConsentPut.model_validate({**valid, "data_domains": ["weight", "nutrition"]})
    assert canonical.data_domains == ("nutrition", "weight")
    all_history = AIConsentPut.model_validate({
        **valid, "history": {"mode": "all_available", "start": None, "end": None},
    })
    assert all_history.history.start is None and all_history.history.end is None


def test_revoking_unknown_id_blocks_delayed_creation_without_affecting_other_accounts(
    db: Session, user: User,
) -> None:
    identifier = uuid4()
    connection_id = make_connection(db, user)
    service = AIConsentService(db, owner=user)
    service.revoke(identifier)
    first = db.get(AIConsentRevocation, (user.id, identifier))
    assert first is not None
    timestamp = first.revoked_at
    service.revoke(identifier)
    assert db.get(AIConsentRevocation, (user.id, identifier)).revoked_at == timestamp
    with pytest.raises(ConnectionProblem) as error:
        service.put(identifier, AIConsentPut.model_validate(consent_payload(connection_id)))
    assert error.value.code == "ai-consent-revoked"
    assert db.get(AIConsent, (user.id, identifier)) is None

    other = User(username="other-revocation-owner", password_hash="synthetic-only")
    db.add(other)
    db.commit()
    other_connection = make_connection(db, other)
    granted, created = AIConsentService(db, owner=other).put(
        identifier, AIConsentPut.model_validate(consent_payload(other_connection)),
    )
    assert created and granted.state == "active"
    assert db.get(AIConsentRevocation, (other.id, identifier)) is None


@pytest.mark.parametrize("explicitly_revoked", [False, True])
def test_connection_recreation_cannot_replay_a_previous_permission(
    db: Session, user: User, explicitly_revoked: bool,
) -> None:
    connection_id = make_connection(db, user)
    identifier = uuid4()
    service = AIConsentService(db, owner=user)
    payload = AIConsentPut.model_validate(consent_payload(connection_id))
    service.put(identifier, payload)
    if explicitly_revoked:
        service.revoke(identifier)
    ProviderConnectionService(db, owner=user).delete(connection_id)
    assert db.get(AIConsent, (user.id, identifier)) is None
    assert db.get(AIConsentRevocation, (user.id, identifier)) is not None
    with pytest.raises(ConnectionProblem) as retired:
        make_connection(db, user)
    assert retired.value.code == "ai-connection-retired"
    with pytest.raises(ConnectionProblem) as error:
        service.put(identifier, payload)
    assert error.value.code == "ai-consent-revoked"
    with pytest.raises(ConnectionProblem):
        service.require_usable(identifier, ConsentDataUse(
            purpose="nutrition_chat", data_domains=("nutrition",), include_meal_names=False,
            start=date(2026, 1, 1), end=date(2026, 1, 2),
        ))
    replacement_connection = make_connection(db, user, changes={"label": "New explicit connection"})
    replacement, created = service.put(
        uuid4(), AIConsentPut.model_validate(consent_payload(replacement_connection)),
    )
    assert created and replacement.state == "active"


def test_delayed_first_permission_cannot_bind_a_recreated_destination(db: Session, user: User) -> None:
    connection_id = make_connection(db, user)
    pending = AIConsentPut.model_validate(consent_payload(connection_id))
    identifier = uuid4()
    connections = ProviderConnectionService(db, owner=user)
    connections.delete(connection_id)
    with pytest.raises(ConnectionProblem) as retired:
        connections.put(connection_id, ProviderConnectionPut.model_validate({
            "label": "Different destination", "provider_kind": "gateway", "enabled": True,
            "model_id": "different-synthetic-model", "base_url": "https://different.invalid/v1",
        }))
    assert retired.value.code == "ai-connection-retired"
    with pytest.raises(ConnectionProblem) as error:
        AIConsentService(db, owner=user).put(identifier, pending)
    assert error.value.code == "ai-connection-not-found"
    assert db.get(AIConsent, (user.id, identifier)) is None


def test_rest_unknown_revocation_rejects_delayed_put(
    client: TestClient, db: Session, user: User,
) -> None:
    connection_id = make_connection(db, user)
    identifier = uuid4()
    headers = {"X-CSRF-Token": login(client)}
    assert client.delete(f"{PATH}/{identifier}", headers=headers).status_code == 204
    assert client.delete(f"{PATH}/{identifier}", headers=headers).status_code == 204
    response = client.put(f"{PATH}/{identifier}", headers=headers, json=consent_payload(connection_id))
    assert response.status_code == 409
    assert response.json()["type"] == "urn:calograph:problem:ai-consent-revoked"
    assert KEY not in response.text


@pytest.mark.parametrize("kind", ["local", "gateway"])
def test_custom_endpoint_is_stored_as_text_and_destination_changes_invalidate_grant(
    db: Session, user: User, kind: str,
) -> None:
    connection_id = make_connection(db, user, changes={
        "provider_kind": kind, "base_url": "http://synthetic.invalid/v1",
    })
    service = AIConsentService(db, owner=user)
    identifier = uuid4()
    view, created = service.put(identifier, AIConsentPut.model_validate(consent_payload(connection_id)))
    assert created and view.base_url == "http://synthetic.invalid/v1"
    assert service.get(identifier).state == "active"
    with pytest.raises(ConnectionProblem) as duplicate:
        service.put(uuid4(), AIConsentPut.model_validate(consent_payload(connection_id)))
    assert duplicate.value.code == "ai-consent-already-exists"
    ProviderConnectionService(db, owner=user).put(connection_id, ProviderConnectionPut.model_validate({
        "label": "Changed destination", "provider_kind": kind, "model_id": "synthetic-model",
        "base_url": "http://another-synthetic.invalid/v1", "api_key": KEY, "enabled": True,
    }))
    assert service.get(identifier).state == "configuration_changed"


def test_immutable_replay_terminal_revocation_and_connection_changes(db: Session, user: User) -> None:
    connection_id = make_connection(db, user)
    service = AIConsentService(db, owner=user)
    identifier = uuid4()
    payload = AIConsentPut.model_validate(consent_payload(connection_id))
    view, created = service.put(identifier, payload)
    assert created and view.state == "active"
    same, created = service.put(identifier, payload)
    assert not created and same == view
    with pytest.raises(ConnectionProblem) as conflict:
        service.put(identifier, payload.model_copy(update={"purpose": "nutrition_check_in"}))
    assert conflict.value.status == 409
    service.revoke(identifier)
    service.revoke(identifier)
    revoked = service.get(identifier)
    assert revoked.state == "revoked" and revoked.revoked_at is not None
    replay, created = service.put(identifier, payload)
    assert not created and replay.state == "revoked"
    with pytest.raises(ConnectionProblem, match="Einwilligung"):
        service.require_usable(identifier, ConsentDataUse(
            purpose="nutrition_chat", data_domains=("nutrition",), include_meal_names=False,
            start=date(2026, 1, 1), end=date(2026, 1, 2),
        ))
    with pytest.raises(ConnectionProblem) as conflict:
        service.put(identifier, payload.model_copy(update={"include_meal_names": True}))
    assert conflict.value.status == 409
    active_identifier = uuid4()
    active_payload = AIConsentPut.model_validate(consent_payload(connection_id))
    service.put(active_identifier, active_payload)
    with pytest.raises(ConnectionProblem) as duplicate:
        service.put(uuid4(), active_payload)
    assert duplicate.value.code == "ai-consent-already-exists"

    replacement = ProviderConnectionPut.model_validate({
        "label": "Changed", "provider_kind": "openai_api", "model_id": "new-model",
        "api_key": KEY, "enabled": True,
    })
    ProviderConnectionService(db, owner=user).put(connection_id, replacement)
    assert service.get(identifier).state == "revoked"
    assert service.get(active_identifier).state == "configuration_changed"


def test_authorization_is_account_scoped_and_checks_exact_scope_and_inclusive_dates(
    db: Session, user: User,
) -> None:
    connection_id = make_connection(db, user)
    other = User(username="consent-other", password_hash="synthetic-only")
    db.add(other)
    db.commit()
    other_connection = make_connection(db, other)
    identifier = uuid4()
    service = AIConsentService(db, owner=user)
    service.put(identifier, AIConsentPut.model_validate(consent_payload(connection_id)))
    other_service = AIConsentService(db, owner=other)
    # Same UUID is valid in a different account, but its snapshot remains private.
    other_service.put(identifier, AIConsentPut.model_validate(consent_payload(other_connection)))
    use = ConsentDataUse(
        purpose="nutrition_chat", data_domains=("nutrition",), include_meal_names=False,
        start=date(2026, 1, 1), end=date(2026, 2, 28),
    )
    assert service.require_usable(identifier, use).state == "active"
    for escalated in [
        use.model_copy(update={"purpose": "nutrition_check_in"}),
        use.model_copy(update={"data_domains": ("nutrition", "weight")}),
        use.model_copy(update={"include_meal_names": True}),
        use.model_copy(update={"start": date(2025, 12, 31)}),
        use.model_copy(update={"end": date(2026, 3, 1)}),
    ]:
        with pytest.raises(ConnectionProblem):
            service.require_usable(identifier, escalated)
    with pytest.raises(ConnectionProblem) as error:
        service.get(uuid4())
    assert error.value.status == 404
    assert other_service.get(identifier).connection_id == other_connection


def test_list_paginates_owner_resources_and_configuration_drift_is_inactive(db: Session, user: User) -> None:
    connection_id = make_connection(db, user)
    service = AIConsentService(db, owner=user)
    identifiers = [UUID(int=i) for i in (101, 102, 103)]
    for index, identifier in enumerate(identifiers):
        service.put(identifier, AIConsentPut.model_validate(consent_payload(
            connection_id, history={"mode": "date_range", "start": f"2026-01-0{index + 1}", "end": "2026-02-28"},
        )))
    first = service.list(AIConsentListQuery(limit=2))
    assert tuple(item.id for item in first.items) == tuple(identifiers[:2])
    assert first.next_after == identifiers[1]
    last = service.list(AIConsentListQuery(limit=2, after=first.next_after))
    assert tuple(item.id for item in last.items) == (identifiers[2],)
    assert last.next_after is None

    changed, _ = ProviderConnectionService(db, owner=user).put(
        connection_id,
        ProviderConnectionPut.model_validate({
            "label": "Changed", "provider_kind": "openai_api", "model_id": "synthetic-model",
            "api_key": KEY, "enabled": False,
        }),
    )
    assert changed.configuration_version == 2
    assert service.get(identifiers[0]).state == "connection_disabled"


def test_native_connection_grants_are_subject_to_active_consent_cap(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(consent_module, "MAX_CONSENTS_PER_ACCOUNT", 1)
    connection_id = make_connection(db, user)
    service = AIConsentService(db, owner=user)
    payload = AIConsentPut.model_validate(consent_payload(connection_id))
    service.put(uuid4(), payload)
    with pytest.raises(ConnectionProblem) as error:
        service.put(uuid4(), AIConsentPut.model_validate(consent_payload(connection_id, purpose="nutrition_check_in")))
    assert error.value.code == "ai-consent-limit"


def test_connection_deletion_cascades_consent_and_inactive_account_cannot_read(
    db: Session, user: User,
) -> None:
    connection_id = make_connection(db, user)
    service = AIConsentService(db, owner=user)
    identifier = uuid4()
    service.put(identifier, AIConsentPut.model_validate(consent_payload(connection_id)))
    ProviderConnectionService(db, owner=user).delete(connection_id)
    with pytest.raises(ConnectionProblem) as error:
        service.get(identifier)
    assert error.value.status == 404

    connection_id = make_connection(db, user, changes={"model_id": "synthetic-model-2"})
    service.put(uuid4(), AIConsentPut.model_validate({
        **consent_payload(connection_id), "configuration_version": 1,
    }))
    user.is_active = False
    user.deactivated_at = datetime.now(UTC)
    db.commit()
    with pytest.raises(InactiveUserOperation):
        service.list(AIConsentListQuery())


def test_rest_auth_csrf_replay_privacy_and_no_decryption(
    client: TestClient, user: User, db: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection_id = make_connection(db, user)
    identifier = uuid4()
    body = consent_payload(connection_id)
    assert client.get(PATH).status_code == 401
    csrf = login(client)
    assert client.put(f"{PATH}/{identifier}", json=body).status_code == 403

    def forbidden(*args, **kwargs):
        pytest.fail("consent metadata operations must not decrypt credentials, read health data, or contact providers")

    monkeypatch.setattr(connection_module, "decrypt_credential", forbidden)
    monkeypatch.setattr("app.ai.probes.fetch_provider_json", forbidden)
    monkeypatch.setattr("app.expert.service.NutritionExpertReader.get_nutrition_days", forbidden)
    response = client.put(f"{PATH}/{identifier}", headers={"X-CSRF-Token": csrf}, json=body)
    assert response.status_code == 201
    assert response.json()["state"] == "active"
    assert KEY not in response.text and "user_id" not in response.json()
    repeated = client.put(f"{PATH}/{identifier}", headers={"X-CSRF-Token": csrf}, json=body)
    assert repeated.status_code == 200 and repeated.json() == response.json()
    assert client.get(PATH).json()["items"] == [response.json()]
    assert client.get(f"{PATH}/{identifier}").json() == response.json()
    assert client.get(PATH, params={"user_id": str(user.id)}).status_code == 422
    assert client.delete(f"{PATH}/{identifier}", headers={"X-CSRF-Token": csrf}).status_code == 204
    assert client.delete(f"{PATH}/{identifier}", headers={"X-CSRF-Token": csrf}).status_code == 204
    assert client.get(f"{PATH}/{identifier}").json()["state"] == "revoked"
