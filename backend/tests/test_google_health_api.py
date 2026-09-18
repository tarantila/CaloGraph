from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.api.google_health as google_health_api
from app.api.google_health import _oauth_error
from app.config import settings
from app.google_health.errors import GoogleHealthOAuthError
from app.models import User, UserSession
from app.schemas_google_health import GoogleHealthStatus


def _login(client: TestClient) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def test_google_health_requires_authentication(client: TestClient):
    assert client.get("/api/v1/google-health/status").status_code == 401
    assert client.post("/api/v1/google-health/oauth/start").status_code == 401
    assert client.get("/api/v1/google-health/oauth/callback?state=x&code=y").status_code == 401


def test_google_health_start_requires_csrf_and_rejects_bad_origin(
    client: TestClient, user: User, monkeypatch
):
    monkeypatch.setattr(settings, "google_health_enabled", True)
    monkeypatch.setattr(settings, "google_health_client_id", "client-id")
    monkeypatch.setattr(settings, "google_health_client_secret", "client-secret")
    monkeypatch.setattr(settings, "credential_encryption_key", Fernet.generate_key().decode())
    csrf = _login(client)
    assert client.post("/api/v1/google-health/oauth/start").status_code == 403
    response = client.post(
        "/api/v1/google-health/oauth/start",
        headers={"X-CSRF-Token": csrf, "Origin": "https://evil.example"},
    )
    assert response.status_code == 403


def test_google_health_status_and_start_contract(client: TestClient, user: User, monkeypatch):
    monkeypatch.setattr(settings, "google_health_enabled", True)
    monkeypatch.setattr(settings, "google_health_client_id", "client-id")
    monkeypatch.setattr(settings, "google_health_client_secret", "client-secret")
    monkeypatch.setattr(settings, "credential_encryption_key", Fernet.generate_key().decode())
    csrf = _login(client)
    status = client.get("/api/v1/google-health/status")
    assert status.status_code == 200
    assert set(status.json()) >= {"available", "configured", "state", "granted_scopes"}

    response = client.post("/api/v1/google-health/oauth/start", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"authorization_url"}
    query = parse_qs(urlsplit(body["authorization_url"]).query)
    assert query["client_id"] == ["client-id"]
    assert query["redirect_uri"] == ["http://localhost:8180/api/v1/google-health/oauth/callback"]
    assert "verifier" not in body["authorization_url"]


def test_google_health_disabled_is_safe(client: TestClient, user: User, monkeypatch):
    monkeypatch.setattr(settings, "google_health_enabled", False)
    csrf = _login(client)
    status = client.get("/api/v1/google-health/status")
    assert status.status_code == 200
    assert status.json()["available"] is False
    response = client.post("/api/v1/google-health/oauth/start", headers={"X-CSRF-Token": csrf})
    assert response.status_code in {200, 403, 404}
    assert "authorization_url" not in response.text


def test_rate_limited_callback_uses_http_429():
    response = _oauth_error(GoogleHealthOAuthError("rate_limited"))
    assert response.status_code == 429


def test_browser_oauth_callback_redirects_to_integrations(
    client: TestClient, user: User, monkeypatch
):
    _login(client)
    monkeypatch.setattr(
        google_health_api,
        "complete_google_health_oauth",
        lambda *args, **kwargs: GoogleHealthStatus(
            available=True,
            configured=True,
            state="active",
        ),
    )
    response = client.get(
        "/api/v1/google-health/oauth/callback?state=test&code=code",
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/konto/integrationen?google_health=connected"


def test_expired_browser_oauth_callback_redirects_to_login(
    client: TestClient, user: User, db: Session
):
    _login(client)
    session = db.scalar(select(UserSession))
    assert session is not None
    session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()

    response = client.get(
        "/api/v1/google-health/oauth/callback?state=test&code=code",
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/konto/integrationen&google_health=error"


def test_browser_oauth_error_redirects_to_integrations(client: TestClient, user: User, monkeypatch):
    _login(client)

    def fail_oauth(*args, **kwargs):
        raise GoogleHealthOAuthError("invalid_state")

    monkeypatch.setattr(google_health_api, "complete_google_health_oauth", fail_oauth)
    response = client.get(
        "/api/v1/google-health/oauth/callback?state=expired&code=code",
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/konto/integrationen?google_health=error"


def test_callback_prefers_json_for_non_html_accept_values(client: TestClient):
    for accept in ("text/html;q=0", "application/json,text/html"):
        response = client.get(
            "/api/v1/google-health/oauth/callback?state=test&code=code",
            headers={"Accept": accept},
            follow_redirects=False,
        )
        assert response.status_code == 401
        assert "location" not in response.headers
