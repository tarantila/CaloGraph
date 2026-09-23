from datetime import UTC, datetime, timedelta
from typing import ClassVar
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.config import settings
from app.google_health import service as google_health_service
from app.google_health.constants import (
    GOOGLE_HEALTH_NUTRITION_SCOPE,
    GOOGLE_HEALTH_REQUIRED_SCOPES,
    GOOGLE_HEALTH_SCOPES,
)
from app.google_health.errors import GoogleHealthTokenExchangeError
from app.google_health.oauth import hash_oauth_state
from app.google_health.service import (
    GoogleHealthOAuthError,
    _GoogleOAuthAdapter,
    complete_google_health_oauth,
    google_health_status,
    start_google_health_oauth,
)
from app.models import GoogleHealthConnection, GoogleHealthOAuthFlow, User
from app.services.credential_crypto import decrypt_credential, encrypt_credential


class TokenAdapter:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def exchange(self, *, code, redirect_uri, client_id, client_secret, code_verifier):
        self.calls.append(
            {
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
                "code_verifier": code_verifier,
            }
        )
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
    assert query["scope"] == [" ".join(GOOGLE_HEALTH_SCOPES)]
    assert flow.state_hash == hash_oauth_state(query["state"][0])
    assert flow.expires_at.replace(tzinfo=UTC) == now + timedelta(minutes=10)
    verifier = decrypt_credential(flow.encrypted_pkce_verifier)
    assert verifier

    assert query["state"][0] not in flow.state_hash

def test_refresh_credentials_request_complete_readonly_scope_union(monkeypatch):
    import google_auth_oauthlib.flow

    captured: dict[str, object] = {}

    class Credentials:
        refresh_token = "refresh-token"
        token = "access-token"
        expiry = None
        granted_scopes: ClassVar[list[str]] = list(GOOGLE_HEALTH_SCOPES)

    class FakeFlow:
        credentials = Credentials()

        @classmethod
        def from_client_config(cls, config, *, scopes, redirect_uri):
            captured["config"] = config
            captured["scopes"] = scopes
            captured["redirect_uri"] = redirect_uri
            return cls()

        def fetch_token(self, *, code, code_verifier):
            captured["code"] = code
            captured["code_verifier"] = code_verifier
            return {"scope": " ".join(GOOGLE_HEALTH_SCOPES)}

    monkeypatch.setattr(google_auth_oauthlib.flow, "Flow", FakeFlow)
    _GoogleOAuthAdapter().exchange(
        code="auth-code",
        redirect_uri="https://nutrition.example.test/callback",
        client_id="client-id",
        client_secret="client-secret",
        code_verifier="verifier",
    )

    assert captured["scopes"] == list(GOOGLE_HEALTH_SCOPES)


def test_status_marks_nutrition_only_connection_scope_missing_without_deleting_history(
    db, user: User, monkeypatch
):
    _configure(monkeypatch)
    now = datetime(2026, 9, 10, tzinfo=UTC)
    connection = GoogleHealthConnection(
        user_id=user.id,
        encrypted_refresh_token=encrypt_credential("old-refresh-token"),
        granted_scopes=[GOOGLE_HEALTH_NUTRITION_SCOPE],
        state="active",
        last_success_at=now,
    )
    db.add(connection)
    db.commit()
    connection_id = connection.id

    result = google_health_status(db, user)

    db.refresh(connection)
    assert result.state == "scope_missing"
    assert result.granted_scopes == (GOOGLE_HEALTH_NUTRITION_SCOPE,)
    assert connection.id == connection_id
    assert connection.state == "active"
    assert decrypt_credential(connection.encrypted_refresh_token) == "old-refresh-token"
    assert connection.last_success_at.replace(tzinfo=UTC) == now


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
            "scope": " ".join(GOOGLE_HEALTH_SCOPES),
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
            {"refresh_token": "refresh-2", "scope": " ".join(GOOGLE_HEALTH_SCOPES), "expires_in": 7200}
        ),
    )
    db.refresh(connection)
    assert result.state == "active"
    assert connection.id == connection_id
    assert decrypt_credential(connection.encrypted_refresh_token) == "refresh-2"


def test_successful_reauth_updates_existing_nutrition_connection_in_place(
    db, user: User, monkeypatch
):
    _configure(monkeypatch)
    now = datetime(2026, 9, 10, tzinfo=UTC)
    connection = GoogleHealthConnection(
        user_id=user.id,
        encrypted_refresh_token=encrypt_credential("old-refresh-token"),
        granted_scopes=[GOOGLE_HEALTH_NUTRITION_SCOPE],
        state="active",
        last_success_at=now - timedelta(days=1),
    )
    db.add(connection)
    db.commit()
    connection_id = connection.id

    start_url = start_google_health_oauth(db, user, now=now)
    query = parse_qs(urlsplit(start_url).query)
    assert query["prompt"] == ["consent"]
    state = query["state"][0]
    result = complete_google_health_oauth(
        db,
        user,
        state=state,
        code="reauth-code",
        error=None,
        now=now,
        oauth_adapter=TokenAdapter(
            {"refresh_token": "new-refresh-token", "scope": " ".join(GOOGLE_HEALTH_SCOPES)}
        ),
    )

    db.refresh(connection)
    assert result.state == "active"
    assert connection.id == connection_id
    assert connection.state == "active"
    assert set(connection.granted_scopes) == GOOGLE_HEALTH_REQUIRED_SCOPES
    assert decrypt_credential(connection.encrypted_refresh_token) == "new-refresh-token"
    assert connection.last_success_at.replace(tzinfo=UTC) == now


def test_scope_missing_callback_preserves_existing_connection_credentials_and_history(
    db, user: User, monkeypatch
):
    _configure(monkeypatch)
    now = datetime(2026, 9, 10, tzinfo=UTC)
    historical_success = now - timedelta(days=1)
    connection = GoogleHealthConnection(
        user_id=user.id,
        encrypted_refresh_token=encrypt_credential("old-refresh-token"),
        granted_scopes=[GOOGLE_HEALTH_NUTRITION_SCOPE],
        state="active",
        last_success_at=historical_success,
    )
    db.add(connection)
    db.commit()
    connection_id = connection.id
    encrypted_token = connection.encrypted_refresh_token

    start_url = start_google_health_oauth(db, user, now=now)
    state = parse_qs(urlsplit(start_url).query)["state"][0]
    result = complete_google_health_oauth(
        db,
        user,
        state=state,
        code="scope-missing-code",
        error=None,
        now=now,
        oauth_adapter=TokenAdapter(
            {
                "refresh_token": "must-not-replace-old-token",
                "scope": GOOGLE_HEALTH_NUTRITION_SCOPE,
            }
        ),
    )

    db.refresh(connection)
    assert result.state == "scope_missing"
    assert result.last_error == "scope_missing"
    assert connection.id == connection_id
    assert connection.state == "reauth_required"
    assert connection.encrypted_refresh_token == encrypted_token
    assert decrypt_credential(connection.encrypted_refresh_token) == "old-refresh-token"
    assert connection.last_success_at.replace(tzinfo=UTC) == historical_success


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
        db,
        user,
        state=state,
        code="code",
        error=None,
        now=now,
        oauth_adapter=TokenAdapter({"refresh_token": "x", "scope": "other"}),
    )
    assert result.state == "scope_missing"
    assert result.last_error == "scope_missing"

    start_url = start_google_health_oauth(db, user, now=now + timedelta(seconds=1))
    state = parse_qs(urlsplit(start_url).query)["state"][0]
    result = complete_google_health_oauth(
        db,
        user,
        state=state,
        code="code",
        error=None,
        now=now,
        oauth_adapter=TokenAdapter({"scope": " ".join(GOOGLE_HEALTH_SCOPES)}),
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
                {"refresh_token": "secret", "scope": " ".join(GOOGLE_HEALTH_SCOPES)}
            ),
        )
    flow = db.scalar(select(GoogleHealthOAuthFlow))
    assert flow is not None and flow.consumed_at is None


def test_crypto_failure_preserves_consumed_claim_and_replay_rejection(db, user: User, monkeypatch):
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
                {"refresh_token": "secret", "scope": " ".join(GOOGLE_HEALTH_SCOPES)}
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
                {"refresh_token": "secret", "scope": " ".join(GOOGLE_HEALTH_SCOPES)}
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


def test_provider_response_without_scope_cannot_activate(db, user: User, monkeypatch):
    _configure(monkeypatch)
    now = datetime(2026, 9, 10, tzinfo=UTC)
    start_url = start_google_health_oauth(db, user, now=now)
    state = parse_qs(urlsplit(start_url).query)["state"][0]

    result = complete_google_health_oauth(
        db,
        user,
        state=state,
        code="code",
        error=None,
        now=now,
        oauth_adapter=TokenAdapter({"refresh_token": "secret"}),
    )
    assert result.state == "scope_missing"
    assert db.scalar(select(GoogleHealthConnection)) is None


@pytest.mark.parametrize("expires_in", [float("nan"), float("inf"), -1, 10**30, "3600"])
def test_malformed_refresh_expiry_is_safe_error(db, user: User, monkeypatch, expires_in):
    _configure(monkeypatch)
    now = datetime(2026, 9, 10, tzinfo=UTC)
    start_url = start_google_health_oauth(db, user, now=now)
    state = parse_qs(urlsplit(start_url).query)["state"][0]

    with pytest.raises(GoogleHealthTokenExchangeError, match="invalid_response"):
        complete_google_health_oauth(
            db,
            user,
            state=state,
            code="code",
            error=None,
            now=now,
            oauth_adapter=TokenAdapter(
                {
                    "refresh_token": "secret",
                    "scope": " ".join(GOOGLE_HEALTH_SCOPES),
                    "refresh_token_expires_in": expires_in,
                }
            ),
        )


def test_start_purges_expired_flow_rows(db, user: User, monkeypatch):
    _configure(monkeypatch)
    now = datetime(2026, 9, 10, tzinfo=UTC)
    start_google_health_oauth(db, user, now=now - timedelta(minutes=11))
    start_google_health_oauth(db, user, now=now - timedelta(minutes=10))
    start_google_health_oauth(db, user, now=now)
    flows = list(db.scalars(select(GoogleHealthOAuthFlow)))
    assert len(flows) == 1
    assert flows[0].expires_at.replace(tzinfo=UTC) > now
