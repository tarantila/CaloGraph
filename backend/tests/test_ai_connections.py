from datetime import UTC, date, datetime
from io import BytesIO
from uuid import UUID, uuid4
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.ai.connections as connection_service
from app.ai.connections import ConnectionProblem, ProviderConnectionService
from app.ai.connections_contracts import ProviderConnectionListQuery, ProviderConnectionPut
from app.config import settings
from app.database import SessionLocal
from app.models import AIProviderConnection, AIProviderCredential, User
from app.services.analysis_demo import (
    AnalysisDemoOwnershipError,
    remove_analysis_demo,
    seed_analysis_demo,
)
from app.services.credential_crypto import decrypt_credential
from app.services.data_export import _write_export
from app.services.user_lifecycle import deactivate_user, delete_user
from app.services.user_operation_lock import UserOperationBusy, exclusive_user_lifecycle_operation

KEY = "synthetic-provider-key-for-tests"
PATH = "/api/v1/settings/ai/connections"


def payload(**changes) -> ProviderConnectionPut:
    return ProviderConnectionPut.model_validate({
        "label": "Personal provider", "provider_kind": "openai_api", "model_id": "synthetic-model",
        **changes,
    })


def login(client: TestClient) -> str:
    response = client.post("/api/v1/auth/login", json={
        "username": "admin", "password": "correct-horse-battery-staple",
    })
    assert response.status_code == 200
    return response.json()["csrf_token"]


def test_create_repeat_preserve_replace_clear_and_delete(db: Session, user: User) -> None:
    service = ProviderConnectionService(db, owner=user)
    identifier = uuid4()
    configured = payload(api_key=KEY, enabled=True)
    first, created = service.put(identifier, configured)
    assert created and first.configuration_version == 1 and first.has_credential
    credential = db.get(AIProviderCredential, (user.id, identifier))
    assert credential is not None
    original_ciphertext = credential.encrypted_api_key
    assert KEY.encode() not in original_ciphertext
    assert decrypt_credential(original_ciphertext) == KEY
    repeated, created = service.put(identifier, configured)
    assert not created
    assert repeated == first
    assert credential.encrypted_api_key == original_ciphertext

    retained, _ = service.put(identifier, payload(label="Renamed", enabled=True))
    assert retained.has_credential and retained.configuration_version == 2
    assert credential.encrypted_api_key == original_ciphertext
    changed, _ = service.put(identifier, payload(api_key="synthetic-replacement-key", enabled=True))
    assert changed.configuration_version == 3
    assert decrypt_credential(credential.encrypted_api_key) == "synthetic-replacement-key"
    removed, _ = service.put(identifier, payload(api_key=None))
    assert not removed.has_credential
    assert db.get(AIProviderCredential, (user.id, identifier)) is None
    service.delete(identifier)
    service.delete(identifier)
    assert db.get(AIProviderConnection, (user.id, identifier)) is None


def test_key_cannot_be_carried_to_another_provider_or_changed_destination(db: Session, user: User) -> None:
    service = ProviderConnectionService(db, owner=user)
    identifier = uuid4()
    service.put(identifier, payload(api_key=KEY))
    with pytest.raises(ConnectionProblem, match="Provider-Typ"):
        service.put(identifier, payload(provider_kind="anthropic"))
    local = uuid4()
    service.put(local, payload(provider_kind="local", base_url="http://ollama.lan:11434/v1", api_key=KEY))
    with pytest.raises(ConnectionProblem, match="Zieländerung"):
        service.put(local, payload(provider_kind="local", base_url="http://other.lan:11434/v1"))
    result, _ = service.put(local, payload(provider_kind="local", base_url="http://other.lan:11434/v1", api_key=None))
    assert not result.has_credential


def test_presence_reads_never_decrypt_or_return_credential(db: Session, user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    service = ProviderConnectionService(db, owner=user)
    identifier = uuid4()
    service.put(identifier, payload(api_key=KEY))

    def forbidden(*args, **kwargs):
        pytest.fail("configuration reads must not decrypt credentials")

    monkeypatch.setattr(connection_service, "decrypt_credential", forbidden)
    result = service.get(identifier)
    assert result.has_credential
    assert KEY not in result.model_dump_json()
    assert "encrypted_api_key" not in result.model_dump_json()
    assert "api_key" not in payload(api_key=KEY).model_dump_json()
    assert KEY not in repr(payload(api_key=KEY))
    assert service.list(ProviderConnectionListQuery()).items == (result,)


def test_encryption_failure_is_atomic_and_existing_bad_cipher_can_be_replaced(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ProviderConnectionService(db, owner=user)
    identifier = uuid4()
    with monkeypatch.context() as context:
        context.setattr(settings, "credential_encryption_key", "")
        with pytest.raises(ConnectionProblem) as error:
            service.put(identifier, payload(api_key=KEY))
        assert error.value.status == 503 and KEY not in str(error.value)
    assert db.get(AIProviderConnection, (user.id, identifier)) is None
    service.put(identifier, payload(api_key=KEY))
    credential = db.get(AIProviderCredential, (user.id, identifier))
    assert credential is not None
    credential.encrypted_api_key = b"unreadable-synthetic-ciphertext"
    db.commit()
    assert service.get(identifier).has_credential
    service.put(identifier, payload(api_key="synthetic-recovery-key"))
    assert decrypt_credential(credential.encrypted_api_key) == "synthetic-recovery-key"


def test_composite_account_scope_pagination_and_admin_isolation(db: Session, user: User) -> None:
    other = User(username="other-provider-owner", password_hash="synthetic-only")
    user.is_admin = True
    db.add(other)
    db.commit()
    own = ProviderConnectionService(db, owner=user)
    theirs = ProviderConnectionService(db, owner=other)
    identifiers = [UUID(int=i) for i in (1, 2, 3)]
    for identifier in identifiers:
        own.put(identifier, payload(api_key=KEY))
    theirs.put(identifiers[0], payload(label="Other account", api_key="synthetic-other-key"))
    assert own.get(identifiers[0]).label == "Personal provider"
    first = own.list(ProviderConnectionListQuery(limit=2))
    assert [item.id for item in first.items] == identifiers[:2]
    assert first.next_after == identifiers[1]
    last = own.list(ProviderConnectionListQuery(limit=2, after=first.next_after))
    assert [item.id for item in last.items] == identifiers[2:]
    assert last.next_after is None
    with pytest.raises(ConnectionProblem) as error:
        theirs.get(identifiers[2])
    assert error.value.status == 404
    own.delete(identifiers[0])
    assert theirs.get(identifiers[0]).has_credential


def test_mutations_enforce_limit_and_exclusive_account_lock(db: Session, user: User) -> None:
    service = ProviderConnectionService(db, owner=user)
    with exclusive_user_lifecycle_operation(db, user.id), pytest.raises(UserOperationBusy):
        service.put(uuid4(), payload())
    for _ in range(20):
        service.put(uuid4(), payload())
    with pytest.raises(ConnectionProblem) as error:
        service.put(uuid4(), payload())
    assert error.value.status == 409
    assert db.scalar(select(func.count()).select_from(AIProviderConnection)) == 20
    existing = service.list(ProviderConnectionListQuery(limit=1)).items[0]
    service.put(existing.id, payload(label="Update at limit"))


def test_reused_session_refreshes_configuration_and_key_after_other_writer(db: Session, user: User) -> None:
    service = ProviderConnectionService(db, owner=user)
    identifier = uuid4()
    service.put(identifier, payload(api_key=KEY))
    cached_record = db.get(AIProviderConnection, (user.id, identifier))
    cached_credential = db.get(AIProviderCredential, (user.id, identifier))
    assert cached_record is not None and cached_credential is not None
    with SessionLocal() as writer:
        owner = writer.get(User, user.id)
        assert owner is not None
        second = ProviderConnectionService(writer, owner=owner)
        second.put(identifier, payload(label="Second writer", api_key=None))
    # The original Session has expire_on_commit=False and holds old objects.
    with pytest.raises(ConnectionProblem) as error:
        service.put(identifier, payload(enabled=True))
    assert error.value.code == "ai-connection-key-required"
    assert not service.get(identifier).has_credential
    third, _ = service.put(identifier, payload(label="Third update"))
    assert third.configuration_version == 3
    with SessionLocal() as writer:
        owner = writer.get(User, user.id)
        assert owner is not None
        ProviderConnectionService(writer, owner=owner).put(identifier, payload(label="Latest label"))
    assert service.list(ProviderConnectionListQuery()).items[0].label == "Latest label"


@pytest.mark.parametrize("changes", [
    {"provider_kind": "chatgpt_plan"}, {"provider_kind": "unknown"}, {"label": "  "},
    {"model_id": "bad\nmodel"}, {"timeout_seconds": 0}, {"timeout_seconds": 301},
    {"api_key": ""}, {"api_key": " leading-space"}, {"api_key": "x" * 8193},
    {"base_url": "https://evil.invalid/v1"}, {"provider_kind": "local"},
    {"provider_kind": "local", "base_url": "http://user:pass@host/v1"},
    {"provider_kind": "gateway", "base_url": "https://host/v1?key=private"},
    {"provider_kind": "gateway", "base_url": "https://host/v1#fragment"},
    {"provider_kind": "local", "base_url": "file:///etc/passwd"},
    {"user_id": "caller-selected-account"},
])
def test_invalid_configuration_is_rejected_without_input_in_error(changes: dict) -> None:
    with pytest.raises(ValidationError) as error:
        payload(**changes)
    assert "leading-space" not in str(error.value)


def test_endpoint_length_matches_storage_including_url_normalization() -> None:
    prefix = "https://example.invalid/"
    endpoint = prefix + "a" * (2048 - len(prefix))
    assert len(str(payload(provider_kind="gateway", base_url=endpoint).base_url)) == 2048
    for invalid in (endpoint + "a", "https://example.invalid/" + "ü" * 340):
        with pytest.raises(ValidationError):
            payload(provider_kind="gateway", base_url=invalid)


def test_rest_auth_csrf_safe_responses_and_idempotency(
    client: TestClient, user: User, db: Session, caplog: pytest.LogCaptureFixture,
) -> None:
    identifier = uuid4()
    data = {"label": "Personal", "provider_kind": "openai_api", "model_id": "synthetic-model", "api_key": KEY}
    assert client.get(PATH).status_code == 401
    csrf = login(client)
    assert client.put(f"{PATH}/{identifier}", json=data).status_code == 403
    response = client.put(f"{PATH}/{identifier}", headers={"X-CSRF-Token": csrf}, json=data)
    assert response.status_code == 201
    assert response.json()["has_credential"]
    assert KEY not in response.text and "api_key" not in response.json()
    repeat = client.put(f"{PATH}/{identifier}", headers={"X-CSRF-Token": csrf}, json=data)
    assert repeat.status_code == 200 and repeat.json() == response.json()
    assert client.get(f"{PATH}/{identifier}").json() == response.json()
    assert client.get(PATH).json()["items"] == [response.json()]
    assert client.get(PATH, params={"user_id": str(user.id)}).status_code == 422
    invalid = client.put(f"{PATH}/{identifier}", headers={"X-CSRF-Token": csrf}, json={**data, "model_id": ""})
    assert invalid.status_code == 422
    assert invalid.headers["content-type"] == "application/problem+json"
    assert KEY not in invalid.text and KEY not in caplog.text
    assert client.delete(f"{PATH}/{identifier}", headers={"X-CSRF-Token": csrf}).status_code == 204
    assert client.delete(f"{PATH}/{identifier}", headers={"X-CSRF-Token": csrf}).status_code == 204
    assert db.get(AIProviderCredential, (user.id, identifier)) is None


def test_enabled_native_provider_needs_key_and_local_endpoint_is_not_probed(db: Session, user: User) -> None:
    service = ProviderConnectionService(db, owner=user)
    with pytest.raises(ConnectionProblem) as error:
        service.put(uuid4(), payload(enabled=True))
    assert error.value.status == 422
    result, _ = service.put(uuid4(), payload(provider_kind="local", base_url="http://127.0.0.1:11434/v1", enabled=True))
    assert result.enabled and not result.has_credential


def test_rest_does_not_allow_admin_to_read_or_mutate_another_accounts_connection(
    client: TestClient, user: User, db: Session,
) -> None:
    user.is_admin = True
    other = User(username="separate-provider-owner", password_hash="synthetic-only")
    db.add(other)
    db.commit()
    identifier = uuid4()
    their_service = ProviderConnectionService(db, owner=other)
    original, _ = their_service.put(identifier, payload(api_key=KEY))
    csrf = login(client)
    assert client.get(f"{PATH}/{identifier}").status_code == 404
    assert client.get(PATH).json()["items"] == []
    # A colliding client UUID creates an independent resource in the caller's account.
    response = client.put(f"{PATH}/{identifier}", headers={"X-CSRF-Token": csrf}, json={
        "label": "Own configuration", "provider_kind": "gemini", "model_id": "synthetic-model",
    })
    assert response.status_code == 201
    assert client.delete(f"{PATH}/{identifier}", headers={"X-CSRF-Token": csrf}).status_code == 204
    assert their_service.get(identifier) == original


def test_account_lifecycle_conflict_returns_problem_and_inactive_owner_cannot_mutate(
    client: TestClient, user: User, db: Session,
) -> None:
    csrf = login(client)
    identifier = uuid4()
    data = {"label": "Draft", "provider_kind": "gemini", "model_id": "synthetic-model"}
    with exclusive_user_lifecycle_operation(db, user.id):
        response = client.put(f"{PATH}/{identifier}", headers={"X-CSRF-Token": csrf}, json=data)
    assert response.status_code == 409
    assert response.headers["content-type"] == "application/problem+json"
    user.is_active = False
    user.deactivated_at = datetime.now(UTC)
    db.commit()
    with pytest.raises(connection_service.InactiveUserOperation):
        ProviderConnectionService(db, owner=user).put(identifier, payload())
    assert db.get(AIProviderConnection, (user.id, identifier)) is None


def test_account_hard_delete_cascades_configuration_and_credentials(db: Session, user: User) -> None:
    user.is_admin = True
    target = User(username="provider-delete-target", password_hash="synthetic-only")
    db.add(target)
    db.commit()
    identifier = uuid4()
    ProviderConnectionService(db, owner=target).put(identifier, payload(api_key=KEY))
    own, _ = ProviderConnectionService(db, owner=user).put(identifier, payload(api_key=KEY))
    deactivate_user(db, user.id, target.id)
    target_id = target.id
    delete_user(db, user.id, target_id, target.username)
    db.expire_all()
    assert db.get(AIProviderConnection, (target_id, identifier)) is None
    assert db.get(AIProviderCredential, (target_id, identifier)) is None
    assert ProviderConnectionService(db, owner=user).get(identifier) == own


def test_provider_settings_and_credentials_are_excluded_from_export(db: Session, user: User) -> None:
    identifier = uuid4()
    ProviderConnectionService(db, owner=user).put(identifier, payload(label="Synthetic private connection", api_key=KEY))
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        _write_export(archive, db, user)
    with ZipFile(buffer) as archive:
        exported = b"\n".join(archive.read(name) for name in archive.namelist())
    assert KEY.encode() not in exported
    assert b"Synthetic private connection" not in exported
    assert str(identifier).encode() not in exported


def test_demo_reset_and_removal_preserve_user_added_provider_connections(db: Session, user: User) -> None:
    demo_id = seed_analysis_demo(db, password="synthetic-analysis-demo-password", today=date(2026, 9, 29))
    demo = db.get(User, demo_id)
    assert demo is not None
    service = ProviderConnectionService(db, owner=demo)
    identifier = uuid4()
    original, _ = service.put(identifier, payload(api_key=KEY))
    with pytest.raises(AnalysisDemoOwnershipError):
        seed_analysis_demo(db, password="synthetic-analysis-demo-password", today=date(2026, 9, 29))
    with pytest.raises(AnalysisDemoOwnershipError):
        remove_analysis_demo(db)
    assert service.get(identifier) == original
    assert db.get(AIProviderCredential, (demo_id, identifier)) is not None
