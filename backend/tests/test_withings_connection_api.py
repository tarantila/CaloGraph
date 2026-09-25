from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.config import settings
from app.models import HealthSample, ImportBatch, WithingsConnection, WithingsOAuthFlow
from app.services.credential_crypto import decrypt_credential, encrypt_credential
from app.withings.credentials import encrypt_token
from app.withings.errors import (
    WithingsAuthenticationError,
    WithingsNotConfiguredError,
    WithingsOAuthError,
    WithingsScopeError,
)
from app.withings.service import disconnect_withings, withings_status
from app.withings.service import test_withings_connection as run_withings_connection_test


def _configured(monkeypatch):
    monkeypatch.setattr(settings, "withings_enabled", True)
    monkeypatch.setattr(settings, "withings_redirect_uri", "https://example.test/callback")


def test_disconnect_preserves_client_credentials_and_health_history(db, user, monkeypatch):
    _configured(monkeypatch)
    secret = "persisted-client-secret"
    connection = WithingsConnection(
        user_id=user.id,
        client_id="persisted-client-id",
        encrypted_client_secret=encrypt_credential(secret),
        withings_user_id="42",
        encrypted_access_token=encrypt_token("opaque-access"),
        encrypted_refresh_token=encrypt_token("opaque-refresh"),
        granted_scopes=["user.metrics", "user.activity"],
        state="active",
        access_token_expires_at=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
    )
    db.add(connection)
    batch = ImportBatch(user_id=user.id, source_type="withings")
    db.add(batch)
    db.flush()
    sample = HealthSample(
        user_id=user.id,
        import_batch_id=batch.id,
        external_sample_id="group-1:1",
        fingerprint="a" * 64,
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
        source_identifier=str(connection.id),
    )
    flow = WithingsOAuthFlow(
        user_id=user.id,
        connection_id=connection.id,
        state_hash="e" * 64,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    db.add_all((sample, flow))
    db.commit()

    result = disconnect_withings(db, user)
    db.refresh(connection)
    assert result.state == "not_connected"
    assert connection.client_id == "persisted-client-id"
    assert decrypt_credential(connection.encrypted_client_secret) == secret
    assert connection.withings_user_id is None
    assert connection.encrypted_access_token is None
    assert connection.encrypted_refresh_token is None
    assert connection.granted_scopes == []
    assert connection.access_token_expires_at is None
    assert db.get(HealthSample, sample.id) is not None
    assert db.scalar(select(WithingsConnection).where(WithingsConnection.user_id == user.id)).id == connection.id
    assert db.scalar(select(WithingsOAuthFlow).where(WithingsOAuthFlow.id == flow.id)) is None
    assert disconnect_withings(db, user).state == "not_connected"


def test_connection_test_uses_injected_adapter_without_network(db, user, monkeypatch):
    _configured(monkeypatch)
    db.add(
        WithingsConnection(
            user_id=user.id,
            client_id="client",
            encrypted_client_secret=encrypt_credential("secret"),
            withings_user_id="42",
            encrypted_access_token=encrypt_token("opaque-access"),
            encrypted_refresh_token=encrypt_token("opaque-refresh"),
            granted_scopes=["user.metrics", "user.activity"],
            state="active",
        )
    )
    db.commit()
    calls: list[dict[str, object]] = []

    class Adapter:
        def test_connection(self, **kwargs):
            calls.append(kwargs)
            return {"ok": True}

    result = run_withings_connection_test(db, user, adapter=Adapter())
    assert result.ok is True
    assert result.state == "active"
    assert calls == [{"access_token": "opaque-access", "withings_user_id": "42"}]


def test_not_configured_connection_test_is_rejected_before_adapter_call(db, user, monkeypatch) -> None:
    _configured(monkeypatch)
    db.add(
        WithingsConnection(
            user_id=user.id,
            client_id="partial-client",
            encrypted_refresh_token=encrypt_token("stored-refresh"),
            state="not_connected",
        )
    )
    db.commit()
    calls: list[dict[str, object]] = []

    class Adapter:
        def test_connection(self, **kwargs):
            calls.append(kwargs)
            return {"ok": True}

    with pytest.raises(WithingsNotConfiguredError) as captured:
        run_withings_connection_test(db, user, adapter=Adapter())

    assert getattr(captured.value, "code", None) == "not_configured"
    assert calls == []


def test_connection_test_without_refresh_token_is_rejected_before_adapter_call(
    db, user, monkeypatch
):
    _configured(monkeypatch)
    db.add(
        WithingsConnection(
            user_id=user.id,
            client_id="client",
            encrypted_client_secret=encrypt_credential("secret"),
            withings_user_id="42",
            encrypted_access_token=encrypt_token("opaque-access"),
            state="not_connected",
        )
    )
    db.commit()
    calls: list[dict[str, object]] = []

    class Adapter:
        def test_connection(self, **kwargs):
            calls.append(kwargs)
            return {"ok": True}

    with pytest.raises(WithingsOAuthError) as captured:
        run_withings_connection_test(db, user, adapter=Adapter())

    assert captured.value.code == "not_connected"
    assert captured.value.status_code == 409
    assert calls == []


def test_scope_missing_connection_test_requires_reauthorization_and_hides_exception(
    db, user, monkeypatch
):
    _configured(monkeypatch)
    attempted_at = datetime(2025, 5, 6, 7, 8, tzinfo=UTC)
    db.add(
        WithingsConnection(
            user_id=user.id,
            client_id="client",
            encrypted_client_secret=encrypt_credential("secret"),
            withings_user_id="42",
            encrypted_access_token=encrypt_token("opaque-access"),
            encrypted_refresh_token=encrypt_token("opaque-refresh"),
            granted_scopes=["user.metrics", "user.activity"],
            state="active",
        )
    )
    db.commit()

    class Adapter:
        def test_connection(self, **kwargs):
            raise WithingsScopeError("private health details must not be disclosed")

    result = run_withings_connection_test(db, user, adapter=Adapter(), now=attempted_at)
    status = withings_status(db, user)
    connection = db.scalar(select(WithingsConnection).where(WithingsConnection.user_id == user.id))

    assert result.ok is False
    assert result.state == "reauth_required"
    assert result.error_category == "scope_missing"
    assert status.state == "reauth_required"
    assert status.last_error_category == "scope_missing"
    assert status.last_attempt_at is not None
    assert status.last_attempt_at.replace(tzinfo=UTC) == attempted_at
    assert connection is not None
    assert connection.last_error == "scope_missing"
    assert "private health details" not in str(result)


def test_false_connection_test_persists_provider_error_without_changing_last_success(
    db, user, monkeypatch
):
    _configured(monkeypatch)
    last_success_at = datetime(2025, 5, 5, 7, 8, tzinfo=UTC)
    attempted_at = datetime(2025, 5, 6, 7, 8, tzinfo=UTC)
    db.add(
        WithingsConnection(
            user_id=user.id,
            client_id="client",
            encrypted_client_secret=encrypt_credential("secret"),
            withings_user_id="42",
            encrypted_access_token=encrypt_token("opaque-access"),
            encrypted_refresh_token=encrypt_token("opaque-refresh"),
            granted_scopes=["user.metrics", "user.activity"],
            state="active",
            last_success_at=last_success_at,
        )
    )
    db.commit()

    class Adapter:
        def test_connection(self, **kwargs):
            return {"ok": False}

    result = run_withings_connection_test(db, user, adapter=Adapter(), now=attempted_at)
    status = withings_status(db, user)
    connection = db.scalar(select(WithingsConnection).where(WithingsConnection.user_id == user.id))

    assert result.ok is False
    assert result.state == "error"
    assert result.error_category == "provider_error"
    assert status.state == "error"
    assert status.last_error_category == "provider_error"
    assert status.last_attempt_at is not None
    assert status.last_attempt_at.replace(tzinfo=UTC) == attempted_at
    assert status.last_success_at is not None
    assert status.last_success_at.replace(tzinfo=UTC) == last_success_at
    assert connection is not None
    assert connection.last_error == "provider_error"


def test_authentication_error_requires_reauthorization(db, user, monkeypatch):
    _configured(monkeypatch)
    db.add(
        WithingsConnection(
            user_id=user.id,
            client_id="client",
            encrypted_client_secret=encrypt_credential("secret"),
            withings_user_id="42",
            encrypted_access_token=encrypt_token("opaque-access"),
            encrypted_refresh_token=encrypt_token("opaque-refresh"),
            granted_scopes=["user.metrics", "user.activity"],
            state="active",
        )
    )
    db.commit()

    class Adapter:
        def test_connection(self, **kwargs):
            raise WithingsAuthenticationError("private credential details")

    result = run_withings_connection_test(db, user, adapter=Adapter())
    status = withings_status(db, user)

    assert result.ok is False
    assert result.state == "reauth_required"
    assert result.error_category == "reauth_required"
    assert status.state == "reauth_required"
    assert status.last_error_category == "reauth_required"
