from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.config import settings
from app.models import HealthSample, ImportBatch, WithingsConnection, WithingsOAuthFlow
from app.services.credential_crypto import decrypt_credential, encrypt_credential


def _login(client: TestClient) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _set_withings_configuration(monkeypatch, *, enabled: bool, redirect_uri="https://example.test/callback") -> None:
    monkeypatch.setattr(settings, "withings_enabled", enabled)
    monkeypatch.setattr(settings, "withings_redirect_uri", redirect_uri)


def test_status_reports_disabled_when_feature_is_disabled(client: TestClient, user, monkeypatch) -> None:
    _login(client)
    _set_withings_configuration(monkeypatch, enabled=False)

    response = client.get("/api/v1/withings/status")

    assert response.status_code == 200
    assert response.json()["state"] == "disabled"
    assert response.json()["available"] is False
    assert response.json()["configured"] is False


def test_status_reports_not_configured_without_user_credentials(client: TestClient, user, monkeypatch) -> None:
    _login(client)
    _set_withings_configuration(monkeypatch, enabled=True)

    response = client.get("/api/v1/withings/status")

    assert response.status_code == 200
    assert response.json()["state"] == "not_configured"
    assert response.json()["available"] is True
    assert response.json()["configured"] is False
    assert response.json()["credentials_configured"] is False
    assert response.json()["redirect_uri"] == "https://example.test/callback"


def test_status_reports_not_connected_from_current_users_credentials(
    client: TestClient, user, db, monkeypatch
) -> None:
    _login(client)
    _set_withings_configuration(monkeypatch, enabled=True)
    db.add(
        WithingsConnection(
            user_id=user.id,
            client_id="private-client-id",
            encrypted_client_secret=encrypt_credential("private-client-secret"),
        )
    )
    db.commit()

    response = client.get("/api/v1/withings/status")

    assert response.status_code == 200
    assert response.json()["state"] == "not_connected"
    assert response.json()["configured"] is True
    assert response.json()["credentials_configured"] is True
    assert response.json()["redirect_uri"] == "https://example.test/callback"
    assert "private-client-id" not in response.text
    assert "private-client-secret" not in response.text


def test_missing_user_credentials_reject_actions_without_provider_access(
    client: TestClient, user, monkeypatch
) -> None:
    del user
    csrf = _login(client)
    _set_withings_configuration(monkeypatch, enabled=True)

    oauth = client.post("/api/v1/withings/oauth/start", headers={"X-CSRF-Token": csrf})
    connection_test = client.post("/api/v1/withings/connection/test", headers={"X-CSRF-Token": csrf})
    sync = client.post("/api/v1/withings/sync", headers={"X-CSRF-Token": csrf})

    assert oauth.status_code == 409
    assert connection_test.status_code == 409
    assert sync.status_code == 409
    assert oauth.json()["detail"] == "Withings ist nicht konfiguriert."
    assert connection_test.json()["detail"] == "Withings ist nicht konfiguriert."
    assert sync.json()["detail"] == "Withings ist nicht konfiguriert."


def test_manual_sync_uses_exclusive_locked_service_context(
    client: TestClient, user, db, monkeypatch
) -> None:
    import pytest

    from app.models import User
    from app.services.user_operation_lock import (
        UserOperationBusy,
        exclusive_user_lifecycle_operation,
        shared_user_operation,
    )
    from app.withings.sync import WithingsDomainResult, WithingsSyncResult

    csrf = _login(client)
    _set_withings_configuration(monkeypatch, enabled=True)
    db.add(
        WithingsConnection(
            user_id=user.id,
            client_id="sync-client",
            encrypted_client_secret=encrypt_credential("sync-secret"),
        )
    )
    other_user = User(username="other-sync-lock-user", password_hash="synthetic")
    db.add(other_user)
    db.commit()
    captured: dict[str, object] = {}

    class FakeSyncService:
        def sync(self, **kwargs):
            captured.update(kwargs)
            assert kwargs.get("_operation_locked") is True
            with (
                pytest.raises(UserOperationBusy),
                shared_user_operation(kwargs["db"], kwargs["user_id"]),
            ):
                pytest.fail("The route did not hold the exclusive operation lock")
            with exclusive_user_lifecycle_operation(kwargs["db"], other_user.id):
                pass
            with pytest.raises(UserOperationBusy):
                disconnect_withings(kwargs["db"], user)
            start = kwargs["requested_start"]
            end = kwargs["requested_end"]
            domain = WithingsDomainResult(
                status="success",
                fetched_count=0,
                persisted_count=0,
                requested_start=start,
                requested_end=end,
            )
            return WithingsSyncResult("success", domain, domain)

    from app.withings.service import disconnect_withings

    monkeypatch.setattr("app.api.withings.WithingsSyncService", FakeSyncService)
    response = client.post("/api/v1/withings/sync", headers={"X-CSRF-Token": csrf})

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert captured["_operation_locked"] is True


def test_manual_sync_rejects_same_user_operation_already_in_progress(
    client: TestClient, user, db, monkeypatch
) -> None:
    from app.services.user_operation_lock import exclusive_user_lifecycle_operation

    csrf = _login(client)
    _set_withings_configuration(monkeypatch, enabled=True)
    db.add(
        WithingsConnection(
            user_id=user.id,
            client_id="sync-client",
            encrypted_client_secret=encrypt_credential("sync-secret"),
        )
    )
    db.commit()
    monkeypatch.setattr(
        "app.api.withings.WithingsSyncService",
        lambda: (_ for _ in ()).throw(AssertionError("busy sync reached the service")),
    )

    with exclusive_user_lifecycle_operation(db, user.id):
        response = client.post("/api/v1/withings/sync", headers={"X-CSRF-Token": csrf})

    assert response.status_code == 409
    assert response.json()["detail"] == "Für dieses Konto läuft gerade eine administrative Operation."


def test_manual_sync_still_requires_csrf(client: TestClient, user, monkeypatch) -> None:
    _login(client)
    _set_withings_configuration(monkeypatch, enabled=True)
    monkeypatch.setattr(
        "app.api.withings.WithingsSyncService",
        lambda: (_ for _ in ()).throw(AssertionError("CSRF rejection reached the service")),
    )

    response = client.post("/api/v1/withings/sync")

    assert response.status_code == 403


def test_connection_test_rejects_overlapping_shared_operation(
    client: TestClient, user, db, monkeypatch
) -> None:
    import pytest

    from app.services.user_operation_lock import shared_user_operation

    csrf = _login(client)
    _set_withings_configuration(monkeypatch, enabled=True)
    monkeypatch.setattr(
        "app.api.withings.test_withings_connection",
        lambda *_args, **_kwargs: pytest.fail("busy connection test reached service"),
    )

    with shared_user_operation(db, user.id):
        response = client.post(
            "/api/v1/withings/connection/test",
            headers={"X-CSRF-Token": csrf},
        )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Für dieses Konto läuft gerade eine administrative Operation."
    )

def test_credentials_api_requires_csrf_and_never_returns_secrets(client: TestClient, user, monkeypatch) -> None:
    csrf = _login(client)
    _set_withings_configuration(monkeypatch, enabled=True)

    rejected = client.put(
        "/api/v1/withings/credentials",
        json={"client_id": "client-id", "client_secret": "client-secret"},
    )
    delete_rejected = client.delete("/api/v1/withings/credentials")
    response = client.put(
        "/api/v1/withings/credentials",
        headers={"X-CSRF-Token": csrf},
        json={"client_id": "client-id", "client_secret": "client-secret"},
    )
    assert delete_rejected.status_code in {401, 403}

    assert rejected.status_code in {401, 403}
    assert response.status_code == 200
    assert response.json()["state"] == "not_connected"
    assert response.json()["credentials_configured"] is True
    assert "client-id" not in response.text
    assert "client-secret" not in response.text


def test_credentials_replacement_is_user_scoped_and_invalidates_flows(
    client: TestClient, user, db, monkeypatch
) -> None:
    from datetime import UTC, datetime, timedelta

    from app.models import User

    csrf = _login(client)
    _set_withings_configuration(monkeypatch, enabled=True)
    first = WithingsConnection(
        user_id=user.id,
        client_id="old-client",
        encrypted_client_secret=encrypt_credential("old-secret"),
        withings_user_id="withings-user-a",
        encrypted_access_token=encrypt_credential("access-a"),
        encrypted_refresh_token=encrypt_credential("refresh-a"),
        granted_scopes=["user.metrics", "user.activity"],
        state="active",
        last_error="provider_error",
        last_error_category="provider_error",
    )
    other = User(username="credential-owner-b", password_hash="synthetic")
    db.add_all([first, other])
    db.flush()
    second = WithingsConnection(
        user_id=other.id,
        client_id="client-b",
        encrypted_client_secret=encrypt_credential("secret-b"),
        withings_user_id="withings-user-b",
        encrypted_refresh_token=encrypt_credential("refresh-b"),
        granted_scopes=["user.metrics"],
        state="active",
    )
    expiry = datetime.now(UTC) + timedelta(minutes=5)
    flow_a = WithingsOAuthFlow(user_id=user.id, connection_id=first.id, state_hash="a" * 64, expires_at=expiry)
    flow_b = WithingsOAuthFlow(user_id=other.id, connection_id=second.id, state_hash="b" * 64, expires_at=expiry)
    legacy_flow = WithingsOAuthFlow(user_id=user.id, connection_id=None, state_hash="c" * 64, expires_at=expiry)
    db.add_all([second, flow_a, flow_b, legacy_flow])
    db.commit()
    flow_a_id = flow_a.id
    flow_b_id = flow_b.id
    legacy_flow_id = legacy_flow.id

    response = client.put(
        "/api/v1/withings/credentials",
        headers={"X-CSRF-Token": csrf},
        json={"client_id": "new-client", "client_secret": "new-secret"},
    )

    db.refresh(first)
    assert response.status_code == 200
    assert response.json()["state"] == "not_connected"
    assert first.client_id == "new-client"
    assert decrypt_credential(first.encrypted_client_secret) == "new-secret"
    assert first.withings_user_id is None
    assert first.encrypted_access_token is None
    assert first.encrypted_refresh_token is None
    assert first.granted_scopes == []
    assert first.last_error is None
    assert first.last_error_category is None
    db.expire_all()
    assert db.get(WithingsOAuthFlow, flow_a_id) is None
    assert db.get(WithingsOAuthFlow, legacy_flow_id) is None
    assert db.get(WithingsOAuthFlow, flow_b_id) is not None
    assert second.client_id == "client-b"
    assert decrypt_credential(second.encrypted_client_secret) == "secret-b"
    assert response.json()["redirect_uri"] == "https://example.test/callback"


def test_credentials_removal_is_idempotent_and_user_scoped(client: TestClient, user, db, monkeypatch) -> None:
    from app.models import User

    csrf = _login(client)
    _set_withings_configuration(monkeypatch, enabled=True)
    own = WithingsConnection(
        user_id=user.id,
        client_id="client-a",
        encrypted_client_secret=encrypt_credential("secret-a"),
        withings_user_id="id-a",
        encrypted_access_token=encrypt_credential("access-a"),
        encrypted_refresh_token=encrypt_credential("refresh-a"),
        granted_scopes=["user.metrics"],
        state="active",
        access_token_expires_at=datetime(2026, 1, 1, tzinfo=UTC),
        last_error="provider_error",
        last_error_category="provider_error",
    )
    other_user = User(username="credential-delete-b", password_hash="synthetic")
    db.add_all([own, other_user])
    db.flush()
    other = WithingsConnection(
        user_id=other_user.id,
        client_id="client-b",
        encrypted_client_secret=encrypt_credential("secret-b"),
        encrypted_refresh_token=encrypt_credential("refresh-b"),
        granted_scopes=["user.activity"],
        state="active",
    )
    db.add(other)
    batch = ImportBatch(user_id=user.id, source_type="withings")
    db.add(batch)
    db.flush()
    sample = HealthSample(
        user_id=user.id,
        import_batch_id=batch.id,
        external_sample_id="removal-preserves-history",
        fingerprint="c" * 64,
        metric_type="weight_kg",
        value=70,
        unit="kg",
        original_value=70,
        original_unit="kg",
        start_at=datetime(2026, 1, 1, tzinfo=UTC),
        end_at=datetime(2026, 1, 1, tzinfo=UTC),
        local_date=datetime(2026, 1, 1, tzinfo=UTC).date(),
        timezone="UTC",
        source_type="withings_measure_v1",
        source_identifier=str(own.id),
    )
    db.add(sample)
    db.commit()
    sample_id = sample.id

    response = client.delete("/api/v1/withings/credentials", headers={"X-CSRF-Token": csrf})
    second_response = client.delete("/api/v1/withings/credentials", headers={"X-CSRF-Token": csrf})

    db.refresh(own)
    assert response.status_code == 200
    assert response.json()["state"] == "not_configured"
    assert second_response.status_code == 200
    assert second_response.json()["state"] == "not_configured"
    assert own.client_id is None
    assert own.encrypted_client_secret is None
    assert own.withings_user_id is None
    assert own.encrypted_access_token is None
    assert own.encrypted_refresh_token is None
    assert own.granted_scopes == []
    assert own.access_token_expires_at is None
    assert own.last_error is None
    assert own.last_error_category is None
    db.expire_all()
    assert db.get(HealthSample, sample_id) is not None
    assert other.client_id == "client-b"
    assert decrypt_credential(other.encrypted_client_secret) == "secret-b"



def test_credentials_api_rejects_partial_pairs_and_user_selection(client: TestClient, user, monkeypatch) -> None:
    del user
    csrf = _login(client)
    _set_withings_configuration(monkeypatch, enabled=True)

    partial = client.put(
        "/api/v1/withings/credentials",
        headers={"X-CSRF-Token": csrf},
        json={"client_id": "only-id"},
    )
    user_selected = client.put(
        "/api/v1/withings/credentials",
        headers={"X-CSRF-Token": csrf},
        json={"client_id": "id", "client_secret": "secret", "user_id": "someone-else"},
    )

    assert partial.status_code == 422
    assert user_selected.status_code == 422
