from fastapi.testclient import TestClient

from app.models import User


def test_login_csrf_and_logout(client: TestClient, user: User) -> None:
    del user
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    assert response.status_code == 200
    csrf = response.json()["csrf_token"]
    assert "httponly" in response.headers["set-cookie"].lower()
    forbidden = client.post("/api/v1/auth/logout")
    assert forbidden.status_code == 403
    logged_out = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    assert logged_out.status_code == 204


def test_bad_password_is_rejected(client: TestClient, user: User) -> None:
    del user
    response = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "wrong-password"}
    )
    assert response.status_code == 401


def test_authenticated_historical_xml_upload(client: TestClient, user: User) -> None:
    del user
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    csrf = login.json()["csrf_token"]
    xml = b"""<?xml version="1.0"?><HealthData><Record type="HKQuantityTypeIdentifierDietaryEnergyConsumed" sourceName="Synthetic" unit="kcal" value="612.5" startDate="2024-01-02 12:00:00 +0100" endDate="2024-01-02 12:00:00 +0100" /></HealthData>"""
    response = client.post(
        "/api/v1/import/apple-health/file",
        headers={"X-CSRF-Token": csrf},
        files={"file": ("export.xml", xml, "application/xml")},
    )
    assert response.status_code == 200
    assert response.json()["inserted"] == 1
