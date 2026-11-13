from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.ai.probes as probes
from app.ai.connections import ConnectionProblem, ProviderConnectionService
from app.ai.outbound import OutboundError
from app.ai.probes import ProviderCheckService, provider_catalog
from app.ai.probes_contracts import CONNECTION_PRIVACY_VERSION, ConnectionCheckPut
from app.models import AIProviderCheck, AIProviderCredential, User
from app.services.rate_limit import RateLimitExceeded
from tests.test_ai_connections import KEY, PATH, login, payload


def configured(db: Session, user: User, **changes):
    identifier = uuid4()
    result, _ = ProviderConnectionService(db, owner=user).put(identifier, payload(api_key=KEY, **changes))
    return identifier, ConnectionCheckPut(configuration_version=result.configuration_version, privacy_version=CONNECTION_PRIVACY_VERSION)


def test_catalog_is_explicit_about_chatgpt_and_unknown_capabilities() -> None:
    catalog = provider_catalog()
    assert not catalog.health_data_transmission_enabled
    chatgpt = next(provider for provider in catalog.providers if provider.kind == "chatgpt_plan")
    assert chatgpt.availability == "planned" and chatgpt.authentication == "chatgpt_oauth"
    assert all(provider.streaming is None and provider.tool_calls is None for provider in catalog.providers)


@pytest.mark.parametrize(("kind", "body", "header"), [
    ("openai_api", {"data": [{"id": "synthetic-model"}]}, "Authorization"),
    ("anthropic", {"data": [{"id": "synthetic-model"}], "has_more": False}, "x-api-key"),
    ("gemini", {"models": [{"name": "models/synthetic-model"}]}, "x-goog-api-key"),
])
def test_native_discovery_auth_and_durable_replay(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch, kind: str, body: dict, header: str,
) -> None:
    identifier, request = configured(db, user, provider_kind=kind)
    calls = []

    def fetch(base, path, **kwargs):
        # Claim and acknowledgement have been committed before external I/O.
        assert db.scalar(select(AIProviderCheck)).privacy_version == CONNECTION_PRIVACY_VERSION
        assert path == "/models" and kwargs["native"] is True
        assert KEY in kwargs["headers"][header]
        assert kwargs["timeout_seconds"] <= 30
        calls.append(base)
        return body

    monkeypatch.setattr(probes, "fetch_provider_json", fetch)
    service = ProviderCheckService(db, owner=user)
    assert service.latest(identifier) is None
    check_id = uuid4()
    result = service.put(identifier, check_id, request)
    assert result.status == "available" and result.models == ("synthetic-model",)
    assert not result.generation_verified
    assert KEY not in result.model_dump_json()
    assert service.put(identifier, check_id, request) == result
    assert service.latest(identifier) == result
    assert len(calls) == 1
    ProviderConnectionService(db, owner=user).put(identifier, payload(provider_kind=kind, label="Changed"))
    assert service.latest(identifier) is None
    assert service.put(identifier, check_id, request) == result
    with pytest.raises(ConnectionProblem, match="geändert"):
        service.put(identifier, uuid4(), request)


@pytest.mark.parametrize(("code", "status", "expected"), [
    ("authentication", 401, "authentication"), ("rate_limited", 429, "rate_limited"),
    ("destination_blocked", None, "destination_blocked"), ("timeout", None, "timeout"),
    ("invalid_response", None, "invalid_response"), ("http_error", 404, "manual_selection"),
    ("http_error", 500, "unavailable"),
])
def test_safe_provider_failures_are_replayed_without_second_request(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch, code: str, status: int | None, expected: str,
) -> None:
    identifier, request = configured(db, user, provider_kind="local", base_url="http://model.invalid/v1")
    def fetch(*args, **kwargs):
        raise OutboundError(code, status=status)
    monkeypatch.setattr(probes, "fetch_provider_json", fetch)
    result = ProviderCheckService(db, owner=user).put(identifier, uuid4(), request)
    assert result.status == expected and KEY not in result.model_dump_json()


def test_model_metadata_is_bounded_and_never_echoes_credentials(db: Session, user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    identifier, request = configured(db, user)
    service = ProviderCheckService(db, owner=user)
    for malformed in ({"data": [{"id": KEY}]}, {"data": [{"id": "bad\nmodel"}]}, {"data": "wrong"}):
        monkeypatch.setattr(probes, "fetch_provider_json", lambda *args, body=malformed, **kwargs: body)
        assert service.put(identifier, uuid4(), request).status == "invalid_response"
    many = {"data": [{"id": f"model-{index}"} for index in range(150)], "has_more": True}
    monkeypatch.setattr(probes, "fetch_provider_json", lambda *args, **kwargs: many)
    result = service.put(identifier, uuid4(), request)
    assert len(result.models) == 100 and result.models_truncated
    assert result.selected_model_available is None


def test_missing_or_unreadable_credentials_prevent_network(db: Session, user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    identifier, request = configured(db, user)
    credential = db.get(AIProviderCredential, (user.id, identifier))
    assert credential is not None
    credential.encrypted_api_key = b"synthetic-invalid-ciphertext"
    db.commit()
    monkeypatch.setattr(probes, "fetch_provider_json", lambda *args, **kwargs: pytest.fail("must not send"))
    service = ProviderCheckService(db, owner=user)
    assert service.put(identifier, uuid4(), request).status == "credential_unavailable"
    other, _ = ProviderConnectionService(db, owner=user).put(uuid4(), payload())
    request = ConnectionCheckPut(configuration_version=other.configuration_version, privacy_version=CONNECTION_PRIVACY_VERSION)
    assert service.put(other.id, uuid4(), request).status == "authentication"


def test_ownership_conflicting_replay_and_interrupted_claim(db: Session, user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    identifier, request = configured(db, user)
    other = User(username="other-probe-owner", password_hash="synthetic-only")
    db.add(other)
    db.commit()
    with pytest.raises(ConnectionProblem) as error:
        ProviderCheckService(db, owner=other).latest(identifier)
    assert error.value.status == 404
    check_id = uuid4()
    db.add(AIProviderCheck(user_id=user.id, id=check_id, connection_id=identifier,
        configuration_version=1, privacy_version=CONNECTION_PRIVACY_VERSION,
        started_at=datetime.now(UTC) - timedelta(minutes=6)))
    db.commit()
    monkeypatch.setattr(probes, "fetch_provider_json", lambda *args, **kwargs: pytest.fail("never re-execute an interrupted claim"))
    service = ProviderCheckService(db, owner=user)
    assert service.put(identifier, check_id, request).status == "interrupted"
    different, different_request = configured(db, user)
    with pytest.raises(ConnectionProblem):
        service.put(different, check_id, different_request)
    ProviderConnectionService(db, owner=user).delete(identifier)
    assert db.get(AIProviderCheck, (user.id, check_id), populate_existing=True) is None


def test_rest_requires_explicit_privacy_ack_csrf_and_current_configuration(
    client: TestClient, db: Session, user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    identifier, request = configured(db, user)
    check_id = uuid4()
    url = f"{PATH}/{identifier}/checks/{check_id}"
    assert client.get("/api/v1/settings/ai/providers").status_code == 401
    csrf = login(client)
    assert client.put(url, json=request.model_dump()).status_code == 403
    headers = {"X-CSRF-Token": csrf}
    for invalid in ({"configuration_version": 1}, {**request.model_dump(), "privacy_version": "old"}, {**request.model_dump(), "api_key": KEY}):
        response = client.put(url, headers=headers, json=invalid)
        assert response.status_code == 422 and KEY not in response.text
    monkeypatch.setattr(probes, "fetch_provider_json", lambda *args, **kwargs: {"data": [{"id": "synthetic-model"}]})
    response = client.put(url, headers=headers, json=request.model_dump())
    assert response.status_code == 200 and response.json()["status"] == "available"
    assert client.get(f"{PATH}/{identifier}/checks/latest").json() == response.json()
    assert client.put(url, headers=headers, json=request.model_dump()).json() == response.json()


def test_probe_limits_and_expiration_preserve_replay(db: Session, user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    identifier, request = configured(db, user)
    monkeypatch.setattr(probes, "fetch_provider_json", lambda *args, **kwargs: {"data": []})
    service = ProviderCheckService(db, owner=user)
    first_id = uuid4()
    first = service.put(identifier, first_id, request)
    for _ in range(9):
        service.put(identifier, uuid4(), request)
    assert service.put(identifier, first_id, request) == first
    with pytest.raises(RateLimitExceeded):
        service.put(identifier, uuid4(), request)
    for _ in range(90):
        db.add(AIProviderCheck(user_id=user.id, id=uuid4(), connection_id=identifier,
            configuration_version=1, privacy_version=CONNECTION_PRIVACY_VERSION, started_at=datetime.now(UTC)))
    db.commit()
    with pytest.raises(ConnectionProblem, match="Limit"):
        service.put(identifier, uuid4(), request)
    assert service.put(identifier, first_id, request) == first
    # Expired entries can be reclaimed; recently issued IDs retain replay semantics.
    for row in db.scalars(select(AIProviderCheck).where(AIProviderCheck.id != first_id)):
        row.started_at = datetime.now(UTC) - timedelta(hours=25)
    db.commit()
    monkeypatch.setattr(probes, "check_rate_limit", lambda *args: None)
    service.put(identifier, uuid4(), request)
    assert len(list(db.scalars(select(AIProviderCheck)))) == 2
