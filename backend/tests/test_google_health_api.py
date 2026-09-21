from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.api.google_health as google_health_api
from app.api.google_health import _oauth_error
from app.auth.security import hash_password
from app.config import settings
from app.google_health.constants import GOOGLE_HEALTH_REQUIRED_SCOPES
from app.google_health.errors import GoogleHealthOAuthError
from app.models import GoogleHealthConnection, User, UserSession
from app.schemas_google_health import GoogleHealthDomainResult, GoogleHealthStatus
from app.services.credential_crypto import decrypt_credential, encrypt_credential
from app.services.google_health_nutrition_sync import GoogleHealthNutritionSyncError


def test_google_health_sync_success_uses_inclusive_user_local_range(
    client: TestClient, user: User, monkeypatch
):
    monkeypatch.setattr(settings, "google_health_enabled", True)
    csrf = _login(client)
    captured: dict[str, object] = {}


    class FakeService:
        def __init__(self, **kwargs):
            del kwargs

        def sync(self, **kwargs):
            captured.update(kwargs)
            domain = GoogleHealthDomainResult(
                status="success",
                fetched_count=3,
                persisted_count=2,
                requested_start=kwargs["requested_start"],
                requested_end=kwargs["requested_end"],
                covered_start=kwargs["requested_start"],
                covered_end=kwargs["requested_end"],
            )
            return type(
                "Result",
                (),
                {
                    "status": "success",
                    "nutrition": domain,
                    "activity_energy": domain,
                    "weight": domain,
                },
            )()

    monkeypatch.setattr(google_health_api, "GoogleHealthSyncService", FakeService)
    response = client.post(
        "/api/v1/google-health/sync?days=29",
        headers={"X-CSRF-Token": csrf},
    )

    end = datetime.now(ZoneInfo(user.timezone)).date()
    start = end - timedelta(days=28)
    assert response.status_code == 200
    expected_domain = {
        "status": "success",
        "fetched_count": 3,
        "persisted_count": 2,
        "requested_start": start.isoformat(),
        "requested_end": end.isoformat(),
        "covered_start": start.isoformat(),
        "covered_end": end.isoformat(),
        "error_code": None,
    }
    assert response.json() == {
        "status": "success",
        "nutrition": expected_domain,
        "activity_energy": expected_domain,
        "weight": expected_domain,
    }
    assert captured["user_id"] == user.id
    assert captured["requested_start"] == start
    assert captured["requested_end"] == end


def test_google_health_sync_requires_csrf(client: TestClient, user: User):
    _login(client)
    response = client.post("/api/v1/google-health/sync")
    assert response.status_code == 403


def test_google_health_sync_requires_authentication(client: TestClient):
    response = client.post("/api/v1/google-health/sync")
    assert response.status_code == 401


def test_google_health_sync_rejects_invalid_csrf(client: TestClient, user: User):
    _login(client)
    response = client.post(
        "/api/v1/google-health/sync",
        headers={"X-CSRF-Token": "invalid-csrf-token"},
    )
    assert response.status_code == 403


@pytest.mark.parametrize("days", ["0", "367", "invalid"])
def test_google_health_sync_rejects_days_bounds(
    client: TestClient, user: User, monkeypatch, days: str
):
    monkeypatch.setattr(settings, "google_health_enabled", True)
    csrf = _login(client)
    response = client.post(
        f"/api/v1/google-health/sync?days={days}",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 422


def test_google_health_sync_defaults_to_thirty_inclusive_days(
    client: TestClient, user: User, monkeypatch
):
    monkeypatch.setattr(settings, "google_health_enabled", True)
    csrf = _login(client)
    captured: dict[str, object] = {}

    class FakeService:
        def __init__(self, **kwargs):
            del kwargs

        def sync(self, **kwargs):
            captured.update(kwargs)
            domain = GoogleHealthDomainResult(
                status="no_data",
                fetched_count=0,
                persisted_count=0,
                requested_start=kwargs["requested_start"],
                requested_end=kwargs["requested_end"],
            )
            return type(
                "Result",
                (),
                {
                    "status": "no_data",
                    "nutrition": domain,
                    "activity_energy": domain,
                    "weight": domain,
                },
            )()

    monkeypatch.setattr(google_health_api, "GoogleHealthSyncService", FakeService)
    response = client.post(
        "/api/v1/google-health/sync",
        headers={"X-CSRF-Token": csrf},
    )
    end = datetime.now(ZoneInfo(user.timezone)).date()
    assert response.status_code == 200
    assert captured["requested_end"] == end
    assert captured["requested_start"] == end - timedelta(days=29)


@pytest.mark.parametrize(
    ("code", "status", "detail"),
    [
        ("connection_not_configured", 404, "Google Health-Verbindung ist nicht eingerichtet."),
        ("connection_inactive", 409, "Google Health muss erneut autorisiert werden."),
        ("scope_missing", 409, "Die erforderliche schreibgeschützte Berechtigung wurde nicht erteilt."),
        ("rate_limited", 429, "Google Health ist vorübergehend nicht verfügbar."),
        ("pagination_error", 502, "Google Health ist vorübergehend nicht verfügbar."),
        ("provider_error", 502, "Google Health ist vorübergehend nicht verfügbar."),
        ("persistence_error", 503, "Google Health-Daten konnten nicht gespeichert werden."),
    ],
)
def test_google_health_sync_maps_errors_without_exception_text(
    client: TestClient,
    user: User,
    monkeypatch,
    caplog,
    code: str,
    status: int,
    detail: str,
):
    monkeypatch.setattr(settings, "google_health_enabled", True)
    csrf = _login(client)
    sentinel = "refresh-token-sentinel raw-provider-payload 987654.321"

    class FakeService:
        def __init__(self, **kwargs):
            del kwargs

        def sync(self, **kwargs):
            del kwargs
            raise GoogleHealthNutritionSyncError(code, sentinel)

    monkeypatch.setattr(google_health_api, "GoogleHealthSyncService", FakeService)
    response = client.post(
        "/api/v1/google-health/sync",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == status
    assert response.json()["detail"] == detail
    assert sentinel not in response.text
    assert sentinel not in caplog.text


def test_google_health_sync_rejects_disabled_and_unconfigured(
    client: TestClient, user: User, monkeypatch
):
    csrf = _login(client)
    monkeypatch.setattr(settings, "google_health_enabled", False)
    disabled = client.post("/api/v1/google-health/sync", headers={"X-CSRF-Token": csrf})
    assert disabled.status_code == 404
    assert "nicht verfügbar" in disabled.json()["detail"]

    monkeypatch.setattr(settings, "google_health_enabled", True)
    unconfigured = client.post("/api/v1/google-health/sync", headers={"X-CSRF-Token": csrf})
    assert unconfigured.status_code == 200
    assert unconfigured.json()["status"] == "failed"
    assert unconfigured.json()["nutrition"]["error_code"] == "connection_not_configured"


def _login(client: TestClient) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]

def _login_as(client: TestClient, username: str, password: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]

def _add_credentials(db: Session, user: User) -> None:
    db.add(
        GoogleHealthConnection(
            user_id=user.id,
            client_id="client-id",
            encrypted_client_secret=encrypt_credential("client-secret"),
            state="not_connected",
        )
    )
    db.commit()


def test_google_health_requires_authentication(client: TestClient):
    assert client.get("/api/v1/google-health/status").status_code == 401
    assert client.post("/api/v1/google-health/oauth/start").status_code == 401
    assert client.get("/api/v1/google-health/oauth/callback?state=x&code=y").status_code == 401


def test_google_health_start_requires_csrf_and_rejects_bad_origin(
    client: TestClient, user: User, monkeypatch
):
    monkeypatch.setattr(settings, "google_health_enabled", True)
    monkeypatch.setattr(settings, "credential_encryption_key", Fernet.generate_key().decode())
    csrf = _login(client)
    assert client.post("/api/v1/google-health/oauth/start").status_code == 403
    response = client.post(
        "/api/v1/google-health/oauth/start",
        headers={"X-CSRF-Token": csrf, "Origin": "https://evil.example"},
    )
    assert response.status_code == 403


def test_google_health_status_and_start_contract(
    client: TestClient, user: User, db: Session, monkeypatch
):
    monkeypatch.setattr(settings, "google_health_enabled", True)
    monkeypatch.setattr(settings, "credential_encryption_key", Fernet.generate_key().decode())
    _add_credentials(db, user)
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


def test_browser_callback_revalidation_401_redirects_to_login(
    client: TestClient, user: User, monkeypatch
):
    _login(client)

    def fail_revalidation(*args, **kwargs):
        raise HTTPException(status_code=401, detail="Sitzung ungültig")

    monkeypatch.setattr(google_health_api, "complete_google_health_oauth", fail_revalidation)
    response = client.get(
        "/api/v1/google-health/oauth/callback?state=valid&code=code",
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/konto/integrationen&google_health=error"


def test_callback_prefers_json_for_non_html_accept_values(client: TestClient):
    for accept in ("text/html;q=0", "application/json,text/html"):
        response = client.get(
            "/api/v1/google-health/oauth/callback?state=test&code=code",
            headers={"Accept": accept},
            follow_redirects=False,
        )
        assert response.status_code == 401
        assert "location" not in response.headers


def test_google_health_credential_routes_require_auth_and_csrf(
    client: TestClient, user: User, monkeypatch
):
    monkeypatch.setattr(settings, "google_health_enabled", True)
    monkeypatch.setattr(settings, "credential_encryption_key", Fernet.generate_key().decode())
    unauthenticated = client.put(
        "/api/v1/google-health/credentials",
        json={"client_id": "client-a", "client_secret": "secret-a"},
    )
    assert unauthenticated.status_code == 401
    csrf = _login(client)
    missing_csrf = client.put(
        "/api/v1/google-health/credentials",
        json={"client_id": "client-a", "client_secret": "secret-a"},
    )
    assert missing_csrf.status_code == 403
    saved = client.put(
        "/api/v1/google-health/credentials",
        json={"client_id": "client-a", "client_secret": "secret-a"},
        headers={"X-CSRF-Token": csrf},
    )
    assert saved.status_code == 200
    body = saved.json()
    assert body["state"] == "not_connected"
    assert body["client_id_configured"] is True
    assert body["client_secret_configured"] is True
    assert "secret-a" not in saved.text


def test_google_health_connection_delete_keeps_client_credentials(
    client: TestClient, user: User, db: Session, monkeypatch
):
    monkeypatch.setattr(settings, "google_health_enabled", True)
    monkeypatch.setattr(settings, "credential_encryption_key", Fernet.generate_key().decode())
    csrf = _login(client)
    saved = client.put(
        "/api/v1/google-health/credentials",
        json={"client_id": "client-a", "client_secret": "secret-a"},
        headers={"X-CSRF-Token": csrf},
    )
    assert saved.status_code == 200
    connection = db.scalar(select(GoogleHealthConnection).where(GoogleHealthConnection.user_id == user.id))
    assert connection is not None
    connection.encrypted_refresh_token = b"encrypted-refresh"
    connection.granted_scopes = ["scope-a"]
    connection.state = "active"
    db.commit()
    disconnected = client.delete(
        "/api/v1/google-health/connection",
        headers={"X-CSRF-Token": csrf},
    )
    assert disconnected.status_code == 200
    body = disconnected.json()
    assert body["state"] == "not_connected"
    assert body["client_id_configured"] is True
    assert body["client_secret_configured"] is True
    assert "secret-a" not in disconnected.text


def test_google_health_api_operations_are_user_scoped(
    client: TestClient, user: User, db: Session, monkeypatch
):
    monkeypatch.setattr(settings, "google_health_enabled", True)
    monkeypatch.setattr(settings, "credential_encryption_key", Fernet.generate_key().decode())
    second = User(
        username="second-api-user",
        password_hash=hash_password("second-password"),
        timezone="Europe/Berlin",
    )
    db.add(second)
    db.commit()

    csrf_a = _login(client)
    assert (
        client.put(
            "/api/v1/google-health/credentials",
            json={"client_id": "client-a", "client_secret": "secret-a"},
            headers={"X-CSRF-Token": csrf_a},
        ).status_code
        == 200
    )
    csrf_b = _login_as(client, "second-api-user", "second-password")
    assert (
        client.put(
            "/api/v1/google-health/credentials",
            json={"client_id": "client-b", "client_secret": "secret-b"},
            headers={"X-CSRF-Token": csrf_b},
        ).status_code
        == 200
    )

    csrf_a = _login(client)
    own_status = client.get("/api/v1/google-health/status")
    assert own_status.status_code == 200
    assert own_status.json()["state"] == "not_connected"
    assert "secret-b" not in own_status.text
    assert (
        client.put(
            "/api/v1/google-health/credentials",
            json={"client_id": "client-a-replaced", "client_secret": "secret-a-replaced"},
            headers={"X-CSRF-Token": csrf_a},
        ).status_code
        == 200
    )

    db.expire_all()
    connection_b = db.scalar(
        select(GoogleHealthConnection).where(GoogleHealthConnection.user_id == second.id)
    )
    assert connection_b is not None
    connection_b.encrypted_refresh_token = encrypt_credential("refresh-b")
    connection_b.granted_scopes = list(GOOGLE_HEALTH_REQUIRED_SCOPES)
    connection_b.state = "active"
    db.commit()

    assert (
        client.delete(
            "/api/v1/google-health/connection",
            headers={"X-CSRF-Token": csrf_a},
        ).status_code
        == 200
    )
    assert (
        client.delete(
            "/api/v1/google-health/credentials",
            headers={"X-CSRF-Token": csrf_a},
        ).status_code
        == 200
    )
    db.expire_all()
    db.refresh(connection_b)
    assert connection_b.client_id == "client-b"
    assert decrypt_credential(connection_b.encrypted_client_secret) == "secret-b"
    assert decrypt_credential(connection_b.encrypted_refresh_token) == "refresh-b"
    assert connection_b.state == "active"
    assert set(connection_b.granted_scopes) == GOOGLE_HEALTH_REQUIRED_SCOPES

    csrf_b = _login_as(client, "second-api-user", "second-password")
    second_status = client.get("/api/v1/google-health/status")
    assert second_status.status_code == 200
    assert second_status.json()["state"] == "active"
    assert "secret-a-replaced" not in second_status.text
    assert client.delete(
        "/api/v1/google-health/connection", headers={"X-CSRF-Token": csrf_b}
    ).status_code == 200


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("invalid_state", 400),
        ("unknown_state", 400),
        ("expired_state", 400),
        ("replayed_state", 400),
        ("reauth_required", 400),
        ("scope_missing", 400),
        ("rate_limited", 429),
        ("transient_error", 502),
        ("provider_error", 502),
        ("invalid_response", 400),
        ("credential_unavailable", 400),
    ],
)
def test_google_health_oauth_errors_expose_only_safe_category_and_status(code: str, status: int):
    response = _oauth_error(GoogleHealthOAuthError(code))
    assert response.status_code == status
    rendered = str(response.detail)
    assert code not in rendered
    assert "client_secret" not in rendered
    assert "refresh_token" not in rendered