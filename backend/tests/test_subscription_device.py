from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.ai import subscription_device as device_module
from app.ai.chatgpt_codex_device import ProtocolError
from app.ai.connections import ProviderConnectionService
from app.ai.subscription_authorization_contracts import (
    SUBSCRIPTION_DEVICE_PRIVACY_ACKNOWLEDGEMENT,
    SubscriptionAuthorizationStart,
)
from app.ai.subscription_device import (
    SubscriptionDeviceAuthorizationService,
    SubscriptionDeviceProblem,
)
from app.ai.subscription_device_contracts import SubscriptionGrant
from app.models import (
    AIProviderConnection,
    SubscriptionCredential,
    SubscriptionDeviceAuthorization,
    SubscriptionDeviceAuthorizationReceipt,
    User,
)
from app.services.credential_crypto import decrypt_credential

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTH_CODE = "synthetic-authorization-code"
VERIFIER = "a" * 43
CHALLENGE = "synthetic-code-challenge"


def start_payload(version: int = 0) -> SubscriptionAuthorizationStart:
    return SubscriptionAuthorizationStart(
        privacy_acknowledgement=SUBSCRIPTION_DEVICE_PRIVACY_ACKNOWLEDGEMENT,
        expected_credential_version=version,
    )


def synthetic_grant() -> SubscriptionGrant:
    now = datetime.now(UTC)
    return SubscriptionGrant(
        access_token=SecretStr("synthetic-access-token"),
        refresh_token=SecretStr("synthetic-refresh-token"),
        id_token=SecretStr("synthetic-id-token"), token_type="Bearer", expires_in=3600,
        scope="openid profile email offline_access api.connectors.read api.connectors.invoke",
        client_id=CLIENT_ID, subject="synthetic-subject", workspace_id="synthetic-workspace",
        received_at=now, expires_at=now + timedelta(hours=1),
    )


def fake_start(calls: list[object], *, interval: int = 2) -> dict[str, object]:
    calls.append("start")
    return {
        "device_auth_id": "synthetic-private-device-id",
        "user_code": "SYNTH-CODE",
        "interval": interval,
        "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
    }


def make_service(db: Session, user: User, **calls) -> SubscriptionDeviceAuthorizationService:
    return SubscriptionDeviceAuthorizationService(db, owner=user, **calls)


def test_synthetic_start_poll_exchange_saves_only_encrypted_verified_grant(db, user):
    start_calls: list[object] = []
    poll_calls: list[object] = []
    exchange_calls: list[object] = []

    def start():
        assert not db.in_transaction()
        return fake_start(start_calls)

    def poll(device_id: str, user_code: str):
        assert not db.in_transaction()
        poll_calls.append((device_id, user_code))
        return "authorized", {
            "authorization_code": AUTH_CODE,
            "code_verifier": VERIFIER,
            "code_challenge": CHALLENGE,
        }

    def exchange(code: str, verifier: str, challenge: str):
        assert not db.in_transaction()
        exchange_calls.append((code, verifier, challenge))
        return synthetic_grant()

    service = make_service(db, user, start_call=start, poll_call=poll, exchange_call=exchange)
    connection_id, attempt_id = uuid4(), uuid4()
    view, created = service.start(connection_id, attempt_id, start_payload())
    assert created and view.state == "pending"
    assert view.user_code == "SYNTH-CODE"
    assert view.verification_uri == "https://auth.openai.com/codex/device"
    assert start_calls == ["start"]

    row = db.get(SubscriptionDeviceAuthorization, (user.id, attempt_id))
    assert row is not None and row.state == "pending" and row.encrypted_device is not None
    assert b"synthetic-private-device-id" not in row.encrypted_device
    assert b"SYNTH-CODE" not in row.encrypted_device
    # A pre-interval poll is an idempotent status read and makes no protocol call.
    early = service.poll(connection_id, attempt_id)
    assert early.state == "pending" and poll_calls == []
    row.next_poll_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    completed = service.poll(connection_id, attempt_id)
    assert completed.state == "completed"
    assert poll_calls == [("synthetic-private-device-id", "SYNTH-CODE")]
    assert exchange_calls == [(AUTH_CODE, VERIFIER, CHALLENGE)]
    assert not {AUTH_CODE, VERIFIER, CHALLENGE, "synthetic-private-device-id"} & set(
        completed.model_dump().values()
    )
    credential = db.get(SubscriptionCredential, (user.id, connection_id))
    assert credential is not None and credential.state == "ready"
    assert credential.encrypted_tokens is not None
    for secret in (b"synthetic-access-token", b"synthetic-refresh-token", b"synthetic-id-token", b"synthetic-subject", b"synthetic-workspace"):
        assert secret not in credential.encrypted_tokens
    clear_tokens = decrypt_credential(credential.encrypted_tokens)
    assert "synthetic-access-token" in clear_tokens
    connection = db.get(AIProviderConnection, (user.id, connection_id))
    assert connection is not None and not connection.enabled and connection.model_id is None
    assert row.encrypted_device is None

    replay, created = service.start(connection_id, attempt_id, start_payload())
    assert not created and replay.state == "completed" and start_calls == ["start"]


def test_start_replay_payload_conflict_retirement_and_prestart_cancel_receipt(db, user):
    calls: list[object] = []
    service = make_service(db, user, start_call=lambda: fake_start(calls))
    connection_id, attempt_id = uuid4(), uuid4()
    service.start(connection_id, uuid4(), start_payload())
    service.cancel(connection_id, attempt_id)
    with pytest.raises(SubscriptionDeviceProblem, match="conflict"):
        service.start(connection_id, attempt_id, start_payload())
    receipt = db.get(SubscriptionDeviceAuthorizationReceipt, (user.id, attempt_id))
    assert receipt is not None and db.get(SubscriptionDeviceAuthorization, (user.id, attempt_id)) is None
    assert calls == ["start"]

    second_id = uuid4()
    service.start(connection_id, second_id, start_payload())
    with pytest.raises(SubscriptionDeviceProblem, match="conflict"):
        service.start(connection_id, second_id, start_payload(1))
    assert calls == ["start", "start"]

    ProviderConnectionService(db, owner=user).delete(connection_id)
    with pytest.raises(SubscriptionDeviceProblem, match="retired"):
        service.start(connection_id, uuid4(), start_payload())
    assert db.get(SubscriptionDeviceAuthorizationReceipt, (user.id, second_id)) is not None


def test_cancel_before_first_start_reserves_id_without_creating_connection(db, user):
    calls: list[object] = []
    service = make_service(db, user, start_call=lambda: fake_start(calls))
    connection_id, attempt_id = uuid4(), uuid4()
    service.cancel(connection_id, attempt_id)
    assert db.get(AIProviderConnection, (user.id, connection_id)) is None
    assert db.get(SubscriptionDeviceAuthorizationReceipt, (user.id, attempt_id)) is not None
    with pytest.raises(SubscriptionDeviceProblem, match="conflict"):
        service.start(connection_id, attempt_id, start_payload())
    assert calls == []
    assert db.get(AIProviderConnection, (user.id, connection_id)) is None


def test_cancel_disconnect_and_configuration_change_prevent_stale_poll(db, user):
    calls: list[object] = []
    service = make_service(db, user, start_call=lambda: fake_start(calls))
    connection_id, attempt_id = uuid4(), uuid4()
    service.start(connection_id, attempt_id, start_payload())
    row = db.get(SubscriptionDeviceAuthorization, (user.id, attempt_id))
    assert row is not None
    connection = db.get(AIProviderConnection, (user.id, connection_id))
    assert connection is not None
    connection.configuration_version += 1
    row.next_poll_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    polled = service.poll(connection_id, attempt_id)
    assert polled.state == "cancelled" and polled.user_code is None

    second_id = uuid4()
    service.start(connection_id, second_id, start_payload())
    service.disconnect(connection_id)
    assert service.get(connection_id, second_id).state == "cancelled"
    assert db.get(SubscriptionCredential, (user.id, connection_id)) is None


def test_denied_poll_is_never_replayed(db, user):
    poll_calls: list[object] = []
    service = make_service(
        db, user,
        start_call=lambda: fake_start([]),
        poll_call=lambda _device, _code: poll_calls.append("poll") or ("denied", None),
    )
    connection_id, attempt_id = uuid4(), uuid4()
    service.start(connection_id, attempt_id, start_payload())
    row = db.get(SubscriptionDeviceAuthorization, (user.id, attempt_id))
    assert row is not None
    row.next_poll_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert service.poll(connection_id, attempt_id).state == "denied"
    assert service.poll(connection_id, attempt_id).state == "denied" and poll_calls == ["poll"]


def test_versioned_input_and_protocol_errors_are_static():
    with pytest.raises(ValidationError):
        SubscriptionAuthorizationStart.model_validate({
            "privacy_acknowledgement": "wrong",
            "expected_credential_version": 0,
        })


def test_rest_device_authorization_requires_csrf_and_is_account_scoped(
    client: TestClient, db: Session, user: User, monkeypatch: pytest.MonkeyPatch,
):
    from app.ai import subscription_device as device_module

    start_calls: list[object] = []
    monkeypatch.setattr(device_module, "begin_device_authorization", lambda: fake_start(start_calls))
    login = client.post("/api/v1/auth/login", json={
        "username": "admin", "password": "correct-horse-battery-staple",
    })
    assert login.status_code == 200
    headers = {"X-CSRF-Token": login.json()["csrf_token"]}
    connection_id, attempt_id = uuid4(), uuid4()
    path = f"/api/v1/settings/ai/connections/{connection_id}/subscription/authorizations/{attempt_id}"
    body = {
        "privacy_acknowledgement": SUBSCRIPTION_DEVICE_PRIVACY_ACKNOWLEDGEMENT,
        "expected_credential_version": 0,
    }
    assert client.put(path, json=body).status_code == 403
    assert start_calls == []
    bad = client.put(path, headers=headers, json={**body, "authorization_code": "must-not-be-accepted"})
    assert bad.status_code == 422 and b"must-not-be-accepted" not in bad.content
    assert start_calls == []

    started = client.put(path, headers=headers, json=body)
    assert started.status_code == 201
    assert started.json()["user_code"] == "SYNTH-CODE"
    assert "authorization_code" not in started.text and "device_auth_id" not in started.text
    assert "code_verifier" not in started.text and "synthetic-private-device-id" not in started.text
    assert start_calls == ["start"]
    status = client.get(f"/api/v1/settings/ai/connections/{connection_id}/subscription")
    assert status.status_code == 200 and status.json()["state"] == "not_connected"
    current = client.get(
        f"/api/v1/settings/ai/connections/{connection_id}/subscription/authorizations/current",
    )
    assert current.status_code == 200 and current.json()["id"] == str(attempt_id)

    user.is_admin = True
    other = User(username="subscription-other-account", password_hash="synthetic-only")
    db.add(other)
    db.commit()
    other_service = SubscriptionDeviceAuthorizationService(
        db, owner=other, start_call=lambda: fake_start([]),
    )
    other_connection, other_attempt = uuid4(), uuid4()
    other_service.start(other_connection, other_attempt, start_payload())
    assert client.get(
        f"/api/v1/settings/ai/connections/{other_connection}/subscription/authorizations/{other_attempt}",
    ).status_code == 404
    assert client.delete(path, headers=headers).status_code == 204
    assert client.delete(path, headers=headers).status_code == 204
    with pytest.raises(ValidationError):
        SubscriptionAuthorizationStart.model_validate({
            "privacy_acknowledgement": SUBSCRIPTION_DEVICE_PRIVACY_ACKNOWLEDGEMENT,
            "expected_credential_version": "0",
            "user_id": str(uuid4()),
        })


def _authorized_result():
    return "authorized", {
        "authorization_code": AUTH_CODE, "code_verifier": VERIFIER, "code_challenge": CHALLENGE,
    }


def _due(db, user, attempt_id):
    row = db.get(SubscriptionDeviceAuthorization, (user.id, attempt_id))
    row.next_poll_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    return row


@pytest.mark.parametrize("phase", ["poll", "exchange"])
@pytest.mark.parametrize("action", ["cancel", "disconnect", "deactivate"])
def test_committed_cancellation_during_provider_io_cannot_restore_credentials(db, user, phase, action):
    exchange_calls = []
    connection_id, attempt_id = uuid4(), uuid4()

    def interrupt():
        if action == "cancel":
            service.cancel(connection_id, attempt_id, _operation_locked=True)
        elif action == "disconnect":
            service.disconnect(connection_id, _operation_locked=True)
        else:
            from app.services.user_lifecycle import _apply_deactivation
            _apply_deactivation(db, user, datetime.now(UTC))
            db.commit()

    def poll(_device, _code):
        if phase == "poll":
            interrupt()
        return _authorized_result()

    def exchange(*_args):
        exchange_calls.append("exchange")
        interrupt()
        return synthetic_grant()

    service = make_service(
        db, user, start_call=lambda: fake_start([]), poll_call=poll, exchange_call=exchange,
    )
    service.start(connection_id, attempt_id, start_payload())
    _due(db, user, attempt_id)
    result = service.poll(connection_id, attempt_id)
    assert result.state == "cancelled" and result.user_code is None
    assert db.get(SubscriptionCredential, (user.id, connection_id)) is None
    assert exchange_calls == ([] if phase == "poll" else ["exchange"])


@pytest.mark.parametrize("invalid_response", [False, True])
def test_cancel_during_start_stays_terminal_even_if_provider_response_fails(db, user, invalid_response):
    connection_id, attempt_id = uuid4(), uuid4()

    def start():
        service.cancel(connection_id, attempt_id, _operation_locked=True)
        if invalid_response:
            return {}
        raise ProtocolError("provider_failure")

    service = make_service(db, user, start_call=start)
    with pytest.raises(SubscriptionDeviceProblem):
        service.start(connection_id, attempt_id, start_payload())
    assert service.get(connection_id, attempt_id).state == "cancelled"


@pytest.mark.parametrize("phase", ["start", "poll", "exchange"])
def test_process_interruption_after_claim_never_reissues_provider_operation(db, user, phase):
    class InterruptedProcess(BaseException):
        pass

    counts = {"start": 0, "poll": 0, "exchange": 0}

    def checkpoint(name):
        counts[name] += 1
        assert not db.in_transaction()
        if phase == name:
            raise InterruptedProcess

    def start():
        checkpoint("start")
        return fake_start([])

    def poll(_device, _code):
        checkpoint("poll")
        return _authorized_result()

    def exchange(*_args):
        checkpoint("exchange")
        return synthetic_grant()

    service = make_service(db, user, start_call=start, poll_call=poll, exchange_call=exchange)
    connection_id, attempt_id = uuid4(), uuid4()
    with pytest.raises(InterruptedProcess):
        service.start(connection_id, attempt_id, start_payload())
        _due(db, user, attempt_id)
        service.poll(connection_id, attempt_id)
    db.expire_all()
    row = db.get(SubscriptionDeviceAuthorization, (user.id, attempt_id))
    claimed = {"start": "starting", "poll": "polling", "exchange": "exchanging"}[phase]
    assert row.state == claimed
    before = counts.copy()
    service.start(connection_id, attempt_id, start_payload())
    service.poll(connection_id, attempt_id)
    assert counts == before and db.get(SubscriptionCredential, (user.id, connection_id)) is None


@pytest.mark.parametrize("committed", [False, True])
def test_final_grant_and_completed_attempt_commit_together_without_repeating_exchange(db, user, monkeypatch, committed):
    exchange_calls = []
    service = make_service(
        db, user, start_call=lambda: fake_start([]),
        poll_call=lambda *_a: _authorized_result(),
        exchange_call=lambda *_a: exchange_calls.append("exchange") or synthetic_grant(),
    )
    connection_id, attempt_id = uuid4(), uuid4()
    service.start(connection_id, attempt_id, start_payload())
    _due(db, user, attempt_id)
    original_commit = db.commit
    injected = []

    def fail_commit():
        row = db.get(SubscriptionDeviceAuthorization, (user.id, attempt_id))
        if row.state == "completed" and not injected:
            injected.append(True)
            if committed:
                original_commit()
            raise OperationalError("synthetic commit failure", None, RuntimeError("synthetic"))
        original_commit()

    monkeypatch.setattr(db, "commit", fail_commit)
    service.poll(connection_id, attempt_id)
    db.expire_all()
    row = db.get(SubscriptionDeviceAuthorization, (user.id, attempt_id))
    credential = db.get(SubscriptionCredential, (user.id, connection_id))
    assert row.state == ("completed" if committed else "exchanging")
    assert (credential is not None and credential.state == "ready") is committed
    assert row.encrypted_device is None
    service.poll(connection_id, attempt_id)
    service.start(connection_id, attempt_id, start_payload())
    assert exchange_calls == ["exchange"] and injected == [True]


@pytest.mark.parametrize("binding", ["owner", "connection", "attempt", "configuration", "generation"])
def test_encrypted_device_metadata_rejects_moved_or_stale_snapshot(db, user, binding):
    calls = []
    service = make_service(db, user, start_call=lambda: fake_start([]))
    connection_id, attempt_id = uuid4(), uuid4()
    service.start(connection_id, attempt_id, start_payload())
    source = db.get(SubscriptionDeviceAuthorization, (user.id, attempt_id)).encrypted_device
    target_owner, target_id, target_attempt = user, connection_id, attempt_id
    if binding in {"owner", "connection", "attempt"}:
        if binding == "owner":
            target_owner = User(username="device-other-owner", password_hash="synthetic")
            db.add(target_owner)
            db.commit()
        if binding == "connection":
            target_id = uuid4()
        target_attempt = uuid4()
        service = make_service(db, target_owner, start_call=lambda: fake_start([]))
        service.start(target_id, target_attempt, start_payload())
    row = _due(db, target_owner, target_attempt)
    if binding == "configuration":
        row.configuration_version += 1
        db.get(AIProviderConnection, (user.id, connection_id)).configuration_version += 1
    elif binding == "generation":
        service._store.save_verified(connection_id, synthetic_grant())
        row.credential_generation = 1
    row.encrypted_device = source
    db.commit()
    service._poll_call = lambda *_a: calls.append("poll") or ("pending", None)
    result = service.poll(target_id, target_attempt)
    assert result.state == "failed" and result.user_code is None and calls == []


def test_rate_limit_blocks_new_receipts_without_provider_io(db, user):
    calls = []
    service = make_service(db, user, start_call=lambda: fake_start(calls))
    connection_id = uuid4()
    for _ in range(10):
        service.cancel(connection_id, uuid4())
    with pytest.raises(SubscriptionDeviceProblem) as error:
        service.start(connection_id, uuid4(), start_payload())
    db.rollback()
    assert error.value.status == 429 and calls == []


def test_pruned_attempt_ids_keep_receipts_and_never_start_again(db, user, monkeypatch):
    clock = [datetime.now(UTC)]
    monkeypatch.setattr(device_module, "utcnow", lambda: clock[0])
    calls = []

    def start():
        calls.append("start")
        return {
            "device_auth_id": "synthetic-private-device-id", "user_code": "SYNTH-CODE",
            "interval": 2, "expires_at": (clock[0] + timedelta(minutes=10)).isoformat(),
        }

    service = make_service(db, user, start_call=start)
    connection_id, first_attempt = uuid4(), uuid4()
    base = clock[0]
    for index in range(45):
        clock[0] = base + timedelta(seconds=61 * (index // 8))
        service.start(connection_id, first_attempt if index == 0 else uuid4(), start_payload())
    count = db.scalar(select(func.count()).select_from(SubscriptionDeviceAuthorization))
    assert count == 40
    assert db.get(SubscriptionDeviceAuthorization, (user.id, first_attempt)) is None
    assert db.get(SubscriptionDeviceAuthorizationReceipt, (user.id, first_attempt)) is not None
    with pytest.raises(SubscriptionDeviceProblem, match="retired"):
        service.start(connection_id, first_attempt, start_payload())
    assert len(calls) == 45


def test_rest_missing_encryption_key_returns_static_503_without_provider_request(client, db, user, monkeypatch):
    from app.config import settings

    calls = []
    monkeypatch.setattr(device_module, "begin_device_authorization", lambda: fake_start(calls))
    login = client.post("/api/v1/auth/login", json={
        "username": "admin", "password": "correct-horse-battery-staple",
    })
    headers = {"X-CSRF-Token": login.json()["csrf_token"]}
    monkeypatch.setattr(settings, "credential_encryption_key", "")
    response = client.put(
        f"/api/v1/settings/ai/connections/{uuid4()}/subscription/authorizations/{uuid4()}",
        headers=headers, json=start_payload().model_dump(),
    )
    assert response.status_code == 503 and calls == []
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["type"] == "urn:calograph:problem:subscription-device-state-unavailable"
