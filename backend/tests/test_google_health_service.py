from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.config import settings
from app.google_health import service as google_health_service
from app.google_health.constants import GOOGLE_HEALTH_SCOPE
from app.google_health.errors import GoogleHealthTokenExchangeError
from app.google_health.oauth import hash_oauth_state
from app.models import GoogleHealthConnection, GoogleHealthOAuthFlow, User
from app.services.credential_crypto import decrypt_credential
from app.google_health.service import (
    GoogleHealthOAuthError,
    complete_google_health_oauth,
    google_health_status,
    start_google_health_oauth,
)


class TokenAdapter:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def exchange(self, *, code, redirect_uri, client_id, client_secret, code_verifier):
        self.calls.append({
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
            "code_verifier": code_verifier,
        })
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def _configure(monkeypatch):
    monkeypatch.setattr(settings, "google_health_enabled", True)
    monkeypatch.setattr(settings, "google_health_client_id", "client-id")
    monkeypatch.setattr(settings, "google_health_client_secret", "client-secret")
    monkeypatch.setattr(settings, "calograph_public_url", "https://nutrition.example.test/")
    monkeypatch.setattr(settings, "credential_encryption_key", Fernet.generate_key().decode())


def test_start_persists_hashed_state_and_encrypted_verifier(db, user: User, monkeypatch):
    _configure(monkeypatch)
    now = datetime(2026, 9, 10, tzinfo=UTC)

    url = start_google_health_oauth(db, user, now=now)
    query = parse_qs(urlsplit(url).query)
    flow = db.scalar(select(GoogleHealthOAuthFlow))
    assert flow is not None
    assert query["scope"] == [GOOGLE_HEALTH_SCOPE]
    assert flow.state_hash == hash_oauth_state(query["state"][0])
    assert flow.expires_at.replace(tzinfo=UTC) == now + timedelta(minutes=10)
    verifier = decrypt_credential(flow.encrypted_pkce_verifier)
    assert verifier
    assert query["state"][0] not in flow.state_hash


def test_complete_exchanges_pkce_and_preserves_connection_uuid(db, user: User, monkeypatch):
    _configure(monkeypatch)
    now = datetime(2026, 9, 10, tzinfo=UTC)
    start_url = start_google_health_oauth(db, user, now=now)
    query = parse_qs(urlsplit(start_url).query)
    state = query["state"][0]
    flow = db.scalar(select(GoogleHealthOAuthFlow))
    assert flow is not None
    verifier = decrypt_credential(flow.encrypted_pkce_verifier)
    adapter = TokenAdapter(
        {
            "refresh_token": "refresh-1",
            "scope": GOOGLE_HEALTH_SCOPE,
            "refresh_token_expires_in": 3600,
        }
    )

    result = complete_google_health_oauth(
        db, user, state=state, code="auth-code", error=None, now=now, oauth_adapter=adapter
    )
    connection = db.scalar(select(GoogleHealthConnection))

    assert result.state == "active"
    assert connection is not None
    connection_id = connection.id
    assert decrypt_credential(connection.encrypted_refresh_token) == "refresh-1"
    assert connection.refresh_token_expires_at.replace(tzinfo=UTC) == now + timedelta(seconds=3600)
    assert adapter.calls[0]["code_verifier"] == verifier
    assert adapter.calls[0]["client_id"] == "client-id"

    start_url = start_google_health_oauth(db, user, now=now + timedelta(minutes=1))
    state = parse_qs(urlsplit(start_url).query)["state"][0]
    result = complete_google_health_oauth(
        db,
        user,
        state=state,
        code="auth-code-2",
        error=None,
        now=now,
        oauth_adapter=TokenAdapter(
            {"refresh_token": "refresh-2", "scope": GOOGLE_HEALTH_SCOPE, "expires_in": 7200}
        ),
    )
    db.refresh(connection)
    assert result.state == "active"
    assert connection.id == connection_id
    assert decrypt_credential(connection.encrypted_refresh_token) == "refresh-2"


def test_complete_rejects_unknown_expired_and_replayed_state(db, user: User, monkeypatch):
    _configure(monkeypatch)
    now = datetime(2026, 9, 10, tzinfo=UTC)
    with pytest.raises(GoogleHealthOAuthError, match="unknown_state"):
        complete_google_health_oauth(db, user, state="unknown", code="code", error=None, now=now)

    start_url = start_google_health_oauth(db, user, now=now)
    state = parse_qs(urlsplit(start_url).query)["state"][0]
    with pytest.raises(GoogleHealthOAuthError, match="expired_state"):
        complete_google_health_oauth(
            db, user, state=state, code="code", error=None, now=now + timedelta(minutes=11)
        )


def test_scope_and_refresh_token_are_required(db, user: User, monkeypatch):
    _configure(monkeypatch)
    now = datetime(2026, 9, 10, tzinfo=UTC)
    start_url = start_google_health_oauth(db, user, now=now)
    state = parse_qs(urlsplit(start_url).query)["state"][0]
    result = complete_google_health_oauth(
        db, user, state=state, code="code", error=None, now=now,
        oauth_adapter=TokenAdapter({"refresh_token": "x", "scope": "other"}),
    )
    assert result.state == "scope_missing"
    assert result.last_error == "scope_missing"

    start_url = start_google_health_oauth(db, user, now=now + timedelta(seconds=1))
    state = parse_qs(urlsplit(start_url).query)["state"][0]
    result = complete_google_health_oauth(
        db, user, state=state, code="code", error=None, now=now,
        oauth_adapter=TokenAdapter({"scope": GOOGLE_HEALTH_SCOPE}),
    )
    assert result.state == "reauth_required"
    assert result.last_error == "reauth_required"


def test_status_never_exposes_secret_fields(db, user: User, monkeypatch):
    _configure(monkeypatch)
    status = google_health_status(db, user)
    dumped = status.model_dump()
    assert "encrypted_refresh_token" not in dumped
    assert "access_token" not in dumped
    assert "client_secret" not in dumped


def test_callback_revalidates_active_user_after_lock(db, user: User, monkeypatch):
    _configure(monkeypatch)
    now = datetime(2026, 9, 10, tzinfo=UTC)
    start_url = start_google_health_oauth(db, user, now=now)
    state = parse_qs(urlsplit(start_url).query)["state"][0]
    user.is_active = False
    user.deactivated_at = now
    db.commit()

    with pytest.raises(GoogleHealthOAuthError, match="session_inactive"):
        complete_google_health_oauth(
            db,
            user,
            state=state,
            code="code",
            error=None,
            now=now,
            oauth_adapter=TokenAdapter(
                {"refresh_token": "secret", "scope": GOOGLE_HEALTH_SCOPE}
            ),
        )
    flow = db.scalar(select(GoogleHealthOAuthFlow))
    assert flow is not None and flow.consumed_at is None


def test_crypto_failure_preserves_consumed_claim_and_replay_rejection(
    db, user: User, monkeypatch
):
    _configure(monkeypatch)
    now = datetime(2026, 9, 10, tzinfo=UTC)
    start_url = start_google_health_oauth(db, user, now=now)
    state = parse_qs(urlsplit(start_url).query)["state"][0]

    def fail_encrypt(_value: str) -> bytes:
        raise google_health_service.CredentialEncryptionError("unavailable")

    monkeypatch.setattr(google_health_service, "encrypt_credential", fail_encrypt)
    with pytest.raises(GoogleHealthOAuthError, match="credential_unavailable"):
        complete_google_health_oauth(
            db,
            user,
            state=state,
            code="code",
            error=None,
            now=now,
            oauth_adapter=TokenAdapter(
                {"refresh_token": "secret", "scope": GOOGLE_HEALTH_SCOPE}
            ),
        )
    flow = db.scalar(select(GoogleHealthOAuthFlow))
    assert flow is not None and flow.consumed_at is not None

    with pytest.raises(GoogleHealthOAuthError, match="replayed_state"):
        complete_google_health_oauth(
            db,
            user,
            state=state,
            code="code",
            error=None,
            now=now,
            oauth_adapter=TokenAdapter(
                {"refresh_token": "secret", "scope": GOOGLE_HEALTH_SCOPE}
            ),
        )


@pytest.mark.parametrize(
    ("message", "status", "expected"),
    [
        ("invalid_grant", 400, "reauth_required"),
        ("invalid_scope", 400, "scope_missing"),
        ("invalid_client", 400, "invalid_response"),
        ("protocol error", 400, "invalid_response"),
    ],
)
def test_provider_error_categories_are_fixed(message, status, expected):
    class ProviderError(Exception):
        status_code = status

    assert google_health_service._error_code(ProviderError(message)) == expected
