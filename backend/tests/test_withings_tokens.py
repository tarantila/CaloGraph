from __future__ import annotations

from datetime import UTC, datetime

from app.config import settings
from app.models import WithingsConnection
from app.services.credential_crypto import encrypt_credential
from app.withings.constants import WITHINGS_REQUIRED_SCOPES
from app.withings.credentials import decrypt_token, encrypt_token
from app.withings.service import persist_rotated_withings_tokens
from app.withings.token_service import refresh_withings_tokens


class RefreshAdapter:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def refresh(self, *, refresh_token, client_id, client_secret):
        self.calls.append((refresh_token, client_id, client_secret))
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def _configure(monkeypatch):
    monkeypatch.setattr(settings, "withings_enabled", True)
    monkeypatch.setattr(settings, "withings_redirect_uri", "https://example.test/withings/callback")


def _connected(db, user, scopes=WITHINGS_REQUIRED_SCOPES):
    connection = WithingsConnection(
        user_id=user.id,
        client_id="synthetic-client-id",
        encrypted_client_secret=encrypt_credential("sentinel-client-secret"),
        withings_user_id="synthetic-withings-user",
        encrypted_access_token=encrypt_token("old-access-token"),
        encrypted_refresh_token=encrypt_token("old-refresh-token"),
        granted_scopes=list(scopes),
        state="active",
    )
    db.add(connection)
    db.commit()
    return connection


def _rotated_payload(scope="user.metrics"):
    return {
        "access_token": "sentinel-new-access-token",
        "refresh_token": "sentinel-new-refresh-token",
        "expires_in": 1800,
        "scope": scope,
    }


def test_refresh_rotation_rejects_partial_scopes_before_storing_new_token_family(
    db, user, monkeypatch
):
    _configure(monkeypatch)
    connection = _connected(db, user)
    old_access = connection.encrypted_access_token
    old_refresh = connection.encrypted_refresh_token
    adapter = RefreshAdapter(_rotated_payload("user.metrics"))

    refresh_withings_tokens(
        db,
        user,
        now=datetime(2026, 9, 25, tzinfo=UTC),
        oauth_adapter=adapter,
    )

    db.refresh(connection)
    assert connection.state == "reauth_required"
    assert connection.last_error_category == "scope_missing"
    assert connection.encrypted_access_token == old_access
    assert connection.encrypted_refresh_token == old_refresh
    assert decrypt_token(connection.encrypted_access_token) == "old-access-token"
    assert decrypt_token(connection.encrypted_refresh_token) == "old-refresh-token"
    assert adapter.calls == [
        ("old-refresh-token", "synthetic-client-id", "sentinel-client-secret")
    ]


def test_direct_rotation_rejects_partial_scopes_before_storing_new_token_family(
    db, user, monkeypatch
):
    _configure(monkeypatch)
    connection = _connected(db, user)
    old_access = connection.encrypted_access_token
    old_refresh = connection.encrypted_refresh_token

    status = persist_rotated_withings_tokens(
        db,
        user,
        _rotated_payload("user.metrics"),
        now=datetime(2026, 9, 25, tzinfo=UTC),
    )

    db.refresh(connection)
    assert status.state == "reauth_required"
    assert status.last_error_category == "scope_missing"
    assert connection.state == "reauth_required"
    assert connection.last_error_category == "scope_missing"
    assert connection.encrypted_access_token == old_access
    assert connection.encrypted_refresh_token == old_refresh
    assert decrypt_token(connection.encrypted_access_token) == "old-access-token"
    assert decrypt_token(connection.encrypted_refresh_token) == "old-refresh-token"


def test_refresh_without_scope_field_retains_a_complete_existing_grant(db, user, monkeypatch):
    _configure(monkeypatch)
    connection = _connected(db, user)
    adapter = RefreshAdapter(
        {
            "access_token": "new-access-token",
            "refresh_token": "new-refresh-token",
            "expires_in": 1800,
        }
    )

    refresh_withings_tokens(
        db,
        user,
        now=datetime(2026, 9, 25, tzinfo=UTC),
        oauth_adapter=adapter,
    )

    db.refresh(connection)
    assert connection.state == "active"
    assert connection.last_error_category is None
    assert WITHINGS_REQUIRED_SCOPES.issubset(set(connection.granted_scopes))
    assert decrypt_token(connection.encrypted_access_token) == "new-access-token"
    assert decrypt_token(connection.encrypted_refresh_token) == "new-refresh-token"


def test_refresh_without_scope_field_rejects_an_incomplete_existing_grant(db, user, monkeypatch):
    _configure(monkeypatch)
    connection = _connected(db, user, scopes=("user.metrics",))
    old_access = connection.encrypted_access_token
    old_refresh = connection.encrypted_refresh_token
    adapter = RefreshAdapter(
        {
            "access_token": "sentinel-new-access-token",
            "refresh_token": "sentinel-new-refresh-token",
            "expires_in": 1800,
        }
    )

    refresh_withings_tokens(
        db,
        user,
        now=datetime(2026, 9, 25, tzinfo=UTC),
        oauth_adapter=adapter,
    )


    db.refresh(connection)
    assert connection.state == "reauth_required"
    assert connection.last_error_category == "scope_missing"
    assert connection.encrypted_access_token == old_access
    assert connection.encrypted_refresh_token == old_refresh
    assert "sentinel-new-access-token" not in repr(connection)
    assert "sentinel-new-refresh-token" not in repr(connection)

def test_refresh_accepts_a_complete_provider_grant_over_an_incomplete_saved_scope_set(
    db, user, monkeypatch
):
    _configure(monkeypatch)
    connection = _connected(db, user, scopes=("user.metrics",))
    adapter = RefreshAdapter(_rotated_payload("user.metrics user.activity"))

    refresh_withings_tokens(
        db,
        user,
        now=datetime(2026, 9, 25, tzinfo=UTC),
        oauth_adapter=adapter,
    )

    db.refresh(connection)
    assert connection.state == "active"
    assert WITHINGS_REQUIRED_SCOPES.issubset(set(connection.granted_scopes))
    assert decrypt_token(connection.encrypted_access_token) == "sentinel-new-access-token"
    assert decrypt_token(connection.encrypted_refresh_token) == "sentinel-new-refresh-token"
    assert adapter.calls == [
        ("old-refresh-token", "synthetic-client-id", "sentinel-client-secret")
    ]
