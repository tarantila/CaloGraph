from urllib.parse import parse_qs, urlsplit

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from app.config import settings
from app.models import User


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
