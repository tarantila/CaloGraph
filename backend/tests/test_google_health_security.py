from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.config import settings
from app.google_health.constants import GOOGLE_HEALTH_SCOPES
from app.google_health.errors import GoogleHealthOAuthError
from app.google_health.service import complete_google_health_oauth, start_google_health_oauth
from app.models import GoogleHealthConnection, GoogleHealthOAuthFlow, User
from app.services.credential_crypto import encrypt_credential


class _Adapter:
    def __init__(self):
        self.calls = []

    def exchange(self, **kwargs):
        self.calls.append(kwargs)
        return {"refresh_token": "refresh", "scope": " ".join(GOOGLE_HEALTH_SCOPES)}


def _add_credentials(db, user: User, client_id: str = "client") -> None:
    db.add(
        GoogleHealthConnection(
            user_id=user.id,
            client_id=client_id,
            encrypted_client_secret=encrypt_credential(f"{client_id}-secret"),
            state="not_connected",
        )
    )
    db.commit()


def test_start_rate_limits_by_user_and_ip(db, user: User, monkeypatch):
    monkeypatch.setattr(settings, "google_health_enabled", True)
    monkeypatch.setattr(settings, "credential_encryption_key", Fernet.generate_key().decode())
    _add_credentials(db, user)
    monkeypatch.setattr(settings, "reconcile_ip_rate_limit", 1)
    monkeypatch.setattr(settings, "reconcile_rate_limit_window_seconds", 300)
    from app.services.rate_limit import RateLimitExceeded

    start_google_health_oauth(db, user, client_ip="203.0.113.7")
    with pytest.raises(RateLimitExceeded):
        start_google_health_oauth(db, user, client_ip="203.0.113.7")


def test_callback_consumes_state_once_and_cross_user_cannot_use_it(db, user: User, monkeypatch):
    monkeypatch.setattr(settings, "google_health_enabled", True)
    monkeypatch.setattr(settings, "credential_encryption_key", Fernet.generate_key().decode())
    _add_credentials(db, user)
    second = User(username="second", password_hash="hash")
    db.add(second)
    db.commit()
    _add_credentials(db, second, "second")
    adapter = _Adapter()
    now = datetime.now(UTC)
    url = start_google_health_oauth(db, user, now=now)
    state = parse_qs(urlsplit(url).query)["state"][0]

    with pytest.raises(GoogleHealthOAuthError):
        complete_google_health_oauth(
            db, second, state=state, code="code", error=None, now=now, oauth_adapter=adapter
        )
    complete_google_health_oauth(
        db, user, state=state, code="code", error=None, now=now, oauth_adapter=adapter
    )
    assert adapter.calls[0]["client_id"] == "client"
    assert adapter.calls[0]["client_secret"] == "client-secret"
    with pytest.raises(GoogleHealthOAuthError):
        complete_google_health_oauth(
            db, user, state=state, code="code", error=None, now=now, oauth_adapter=_Adapter()
        )
    flow = db.scalar(select(GoogleHealthOAuthFlow))
    assert flow is not None and flow.consumed_at is not None
