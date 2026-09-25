from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.config import settings
from app.models import User, WithingsConnection, WithingsOAuthFlow
from app.services.credential_crypto import encrypt_credential
from app.withings.constants import WITHINGS_REQUIRED_SCOPES
from app.withings.credentials import decrypt_token
from app.withings.errors import WithingsOAuthError
from app.withings.oauth import hash_oauth_state
from app.withings.service import (
    complete_withings_oauth,
    save_withings_credentials,
    start_withings_oauth,
)


class ExchangeAdapter:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def exchange(self, *, code, redirect_uri, client_id, client_secret):
        self.calls.append((code, redirect_uri, client_id, client_secret))
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def _configure(monkeypatch):
    monkeypatch.setattr(settings, "withings_enabled", True)
    monkeypatch.setattr(settings, "withings_redirect_uri", "https://example.test/withings/callback")


def _set_credentials(db, user):
    connection = WithingsConnection(
        user_id=user.id,
        client_id="synthetic-client-id",
        encrypted_client_secret=encrypt_credential("sentinel-client-secret"),
    )
    db.add(connection)
    db.commit()
    return connection


def _payload(scopes=("user.metrics", "user.activity")):
    return {
        "userid": "synthetic-withings-user",
        "access_token": "sentinel-access-token",
        "refresh_token": "sentinel-refresh-token",
        "expires_in": 3600,
        "scope": " ".join(scopes),
    }


def _start(db, user, now):
    url = start_withings_oauth(db, user, now=now)
    return parse_qs(urlsplit(url).query)["state"][0]


def test_oauth_flow_stores_only_hashed_state_and_binds_user_connection(db, user, monkeypatch):
    _configure(monkeypatch)
    now = datetime(2026, 9, 25, tzinfo=UTC)
    connection = _set_credentials(db, user)

    state = _start(db, user, now)

    flow = db.scalar(select(WithingsOAuthFlow).where(WithingsOAuthFlow.user_id == user.id))
    assert flow is not None
    assert flow.state_hash == hash_oauth_state(state)
    assert flow.state_hash != state
    assert flow.connection_id == connection.id
    assert flow.expires_at.replace(tzinfo=UTC) == now + timedelta(minutes=10)
    assert state not in repr(flow)


def test_oauth_flow_rejects_another_users_callback(db, user, monkeypatch):
    _configure(monkeypatch)
    now = datetime(2026, 9, 25, tzinfo=UTC)
    _set_credentials(db, user)
    other_user = User(username=f"other-{uuid4()}", password_hash="unused-test-hash")
    db.add(other_user)
    db.flush()
    _set_credentials(db, other_user)
    state = _start(db, user, now)
    adapter = ExchangeAdapter(_payload())

    with pytest.raises(WithingsOAuthError) as captured:
        complete_withings_oauth(
            db,
            other_user,
            state=state,
            code="synthetic-code",
            error=None,
            oauth_adapter=adapter,
            now=now,
        )

    assert captured.value.code == "unknown_state"
    assert adapter.calls == []


def test_oauth_flow_expires_at_ten_minutes(db, user, monkeypatch):
    _configure(monkeypatch)
    now = datetime(2026, 9, 25, tzinfo=UTC)
    _set_credentials(db, user)
    state = _start(db, user, now)
    adapter = ExchangeAdapter(_payload())

    with pytest.raises(WithingsOAuthError) as captured:
        complete_withings_oauth(
            db,
            user,
            state=state,
            code="synthetic-code",
            error=None,
            oauth_adapter=adapter,
            now=now + timedelta(minutes=10),
        )

    assert captured.value.code == "expired_state"
    assert adapter.calls == []

def test_oauth_callback_replay_is_rejected_after_success(db, user, monkeypatch):
    _configure(monkeypatch)
    now = datetime(2026, 9, 25, tzinfo=UTC)
    _set_credentials(db, user)
    state = _start(db, user, now)
    adapter = ExchangeAdapter(_payload())
    complete_withings_oauth(
        db,
        user,
        state=state,
        code="synthetic-code",
        error=None,
        oauth_adapter=adapter,
        now=now,
    )

    with pytest.raises(WithingsOAuthError) as captured:
        complete_withings_oauth(
            db,
            user,
            state=state,
            code="synthetic-code",
            error=None,
            oauth_adapter=adapter,
            now=now,
        )

    assert captured.value.code == "replayed_state"
    assert len(adapter.calls) == 1


def test_oauth_callback_rejects_a_flow_bound_to_another_connection(db, user, monkeypatch):
    _configure(monkeypatch)
    now = datetime(2026, 9, 25, tzinfo=UTC)
    connection = _set_credentials(db, user)
    state = _start(db, user, now)
    other_user = User(username=f"other-{uuid4()}", password_hash="unused-test-hash")
    db.add(other_user)
    db.flush()
    other_connection = _set_credentials(db, other_user)
    flow = db.scalar(select(WithingsOAuthFlow).where(WithingsOAuthFlow.user_id == user.id))
    assert flow is not None
    flow.connection_id = other_connection.id
    db.commit()
    adapter = ExchangeAdapter(_payload())

    with pytest.raises(WithingsOAuthError):
        complete_withings_oauth(
            db,
            user,
            state=state,
            code="synthetic-code",
            error=None,
            oauth_adapter=adapter,
            now=now,
        )

    db.refresh(connection)
    assert adapter.calls == []
    assert connection.encrypted_access_token is None
    assert connection.encrypted_refresh_token is None


def test_successful_oauth_grants_weight_and_activity_atomically(db, user, monkeypatch):
    _configure(monkeypatch)
    now = datetime(2026, 9, 25, tzinfo=UTC)
    connection = _set_credentials(db, user)
    state = _start(db, user, now)
    adapter = ExchangeAdapter(_payload())

    status = complete_withings_oauth(
        db,
        user,
        state=state,
        code="synthetic-code",
        error=None,
        oauth_adapter=adapter,
        now=now,
    )

    db.refresh(connection)
    assert status.state == "active"
    assert WITHINGS_REQUIRED_SCOPES.issubset(set(status.granted_scopes))
    assert WITHINGS_REQUIRED_SCOPES.issubset(set(connection.granted_scopes))
    assert connection.withings_user_id == "synthetic-withings-user"
    assert decrypt_token(connection.encrypted_access_token) == "sentinel-access-token"
    assert decrypt_token(connection.encrypted_refresh_token) == "sentinel-refresh-token"
    assert adapter.calls == [
        (
            "synthetic-code",
            "https://example.test/withings/callback",
            "synthetic-client-id",
            "sentinel-client-secret",
        )
    ]


@pytest.mark.parametrize("scopes", [("user.metrics",), ("user.activity",)])
def test_oauth_partial_scope_fails_closed_without_storing_tokens(db, user, monkeypatch, scopes):
    _configure(monkeypatch)
    now = datetime(2026, 9, 25, tzinfo=UTC)
    connection = _set_credentials(db, user)
    state = _start(db, user, now)
    adapter = ExchangeAdapter(_payload(scopes))

    status = complete_withings_oauth(
        db,
        user,
        state=state,
        code="synthetic-code",
        error=None,
        oauth_adapter=adapter,
        now=now,
    )

    db.refresh(connection)
    assert status.state == "reauth_required"
    assert status.last_error_category == "scope_missing"
    assert connection.state == "reauth_required"
    assert connection.last_error_category == "scope_missing"
    assert connection.encrypted_access_token is None
    assert connection.encrypted_refresh_token is None
    assert connection.withings_user_id is None
    assert "sentinel-client-secret" not in repr(status)
    assert "synthetic-code" not in repr(status)
