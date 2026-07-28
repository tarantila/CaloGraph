from datetime import datetime

from fastapi.testclient import TestClient

from app.api import analytics
from app.auth import security
from app.config import settings
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


def test_login_ip_limit_cannot_be_bypassed_with_different_usernames(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "login_ip_rate_limit", 3)
    monkeypatch.setattr(settings, "login_rate_limit", 100)

    responses = [
        client.post(
            "/api/v1/auth/login",
            json={"username": f"unknown-{index}", "password": "wrong-password"},
        )
        for index in range(4)
    ]

    assert [response.status_code for response in responses] == [401, 401, 401, 429]
    assert "Retry-After" in responses[-1].headers


def test_login_account_limit_normalizes_unknown_and_existing_accounts(
    client: TestClient,
    user: User,
    monkeypatch,
) -> None:
    del user
    monkeypatch.setattr(settings, "login_ip_rate_limit", 100)
    monkeypatch.setattr(settings, "login_rate_limit", 2)

    unknown_responses = [
        client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "wrong-password"},
        )
        for username in ("Ghost", " ghost ", "GHOST")
    ]
    existing_responses = [
        client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "wrong-password"},
        )
        for _ in range(3)
    ]

    assert [response.status_code for response in unknown_responses] == [401, 401, 429]
    assert [response.status_code for response in existing_responses] == [401, 401, 429]


def test_unknown_account_uses_dummy_argon2_hash(monkeypatch) -> None:
    verified_hashes: list[str] = []

    def fake_verify(password_hash: str, password: str) -> bool:
        del password
        verified_hashes.append(password_hash)
        return False

    monkeypatch.setattr(security, "verify_password", fake_verify)

    assert security.verify_login_password("wrong-password", None) is False
    assert verified_hashes == [security.DUMMY_PASSWORD_HASH]


def test_successful_login_clears_account_failures(
    client: TestClient,
    user: User,
    monkeypatch,
) -> None:
    del user
    monkeypatch.setattr(settings, "login_ip_rate_limit", 100)
    monkeypatch.setattr(settings, "login_rate_limit", 2)

    failed = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "wrong-password"},
    )
    succeeded = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    after_success = [
        client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "wrong-password"},
        )
        for _ in range(2)
    ]

    assert failed.status_code == 401
    assert succeeded.status_code == 200
    assert [response.status_code for response in after_success] == [401, 401]


def test_password_change_has_independent_failure_limit(
    client: TestClient,
    user: User,
    monkeypatch,
) -> None:
    del user
    monkeypatch.setattr(settings, "password_change_rate_limit", 2)
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    csrf = login.json()["csrf_token"]

    responses = [
        client.post(
            "/api/v1/auth/password",
            headers={"X-CSRF-Token": csrf},
            json={
                "current_password": "wrong-password",
                "new_password": "new-correct-horse-battery-staple",
            },
        )
        for _ in range(3)
    ]

    assert [response.status_code for response in responses] == [400, 400, 429]
    assert "Retry-After" in responses[-1].headers


def test_existing_target_version_can_be_updated(
    client: TestClient,
    user: User,
) -> None:
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    csrf = login.json()["csrf_token"]

    response = client.put(
        "/api/v1/settings/targets/2024-01-01",
        headers={"X-CSRF-Token": csrf},
        json={
            "valid_from": "2024-01-01",
            "calories_kcal": 2500,
            "maintenance_kcal": 2800,
            "protein_g": 150,
            "carbs_g": 260,
            "fat_g": 80,
            "fiber_g": 30,
        },
    )

    assert response.status_code == 200
    assert response.json()["calories_kcal"] == "2500.000"
    assert response.json()["maintenance_kcal"] == "2800.000"
    saved = client.get("/api/v1/settings/targets")
    assert saved.status_code == 200
    assert saved.json()[0]["calories_kcal"] == "2500.000"
    assert saved.json()[0]["maintenance_kcal"] == "2800.000"

    invalid = client.put(
        "/api/v1/settings/targets/2024-01-01",
        headers={"X-CSRF-Token": csrf},
        json={
            "valid_from": "2024-01-01",
            "calories_kcal": 2500,
            "maintenance_kcal": 2400,
            "protein_g": 150,
        },
    )
    assert invalid.status_code == 422


def test_dashboard_week_budget_always_covers_monday_to_sunday(
    client: TestClient, user: User, monkeypatch
) -> None:
    del user

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 23, 12, 0, tzinfo=tz)

    monkeypatch.setattr(analytics, "datetime", FixedDatetime)
    client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )

    response = client.get("/api/v1/dashboard/summary")

    assert response.status_code == 200
    assert "current_weight_kg" not in response.json()
    assert "weight_change_kg" not in response.json()
    assert response.json()["week"] == {
        "consumed_kcal": 0.0,
        "budget_kcal": 14000.0,
        "deviation_kcal": -14000.0,
        "remaining_kcal": 14000.0,
    }


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


def test_authenticated_yazio_json_upload(client: TestClient, user: User) -> None:
    del user
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    csrf = login.json()["csrf_token"]
    payload = b"""{
      "2026-07-20": {
        "daily_summary": {
          "activity_energy": 300,
          "steps": 8000,
          "water_intake": 2000,
          "units": {"unit_energy": "kcal"},
          "meals": {
            "breakfast": {
              "nutrients": {
                "energy.energy": 450,
                "nutrient.protein": 25,
                "nutrient.carb": 60,
                "nutrient.fat": 15
              }
            }
          }
        }
      }
    }"""
    response = client.post(
        "/api/v1/import/yazio/file",
        headers={"X-CSRF-Token": csrf},
        files={"file": ("days.json", payload, "application/json")},
    )
    assert response.status_code == 200
    assert response.json()["inserted"] == 4
    summary = client.get("/api/v1/dashboard/summary")
    assert summary.status_code == 200
    assert summary.json()["data_start_date"] == "2026-07-20"
    assert summary.json()["data_end_date"] == "2026-07-20"
    assert summary.json()["data_day_count"] == 1


def test_import_detail_exposes_only_safe_record_errors(
    client: TestClient,
    user: User,
) -> None:
    del user
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    csrf = login.json()["csrf_token"]
    xml = b"""<?xml version="1.0"?><HealthData><Record type="HKQuantityTypeIdentifierDietaryEnergyConsumed" sourceName="Synthetic" unit="kcal" value="not-a-number" startDate="2024-01-02 12:00:00 +0100" endDate="2024-01-02 12:00:00 +0100" /></HealthData>"""

    imported = client.post(
        "/api/v1/import/apple-health/file",
        headers={"X-CSRF-Token": csrf},
        files={"file": ("export.xml", xml, "application/xml")},
    )
    detail = client.get(f"/api/v1/imports/{imported.json()['batch_id']}")

    assert imported.status_code == 200
    assert imported.json()["status"] == "completed_with_errors"
    assert detail.status_code == 200
    assert detail.json()["errors"] == [
        {
            "item_index": 0,
            "metric_type": "HKQuantityTypeIdentifierDietaryEnergyConsumed",
            "error_code": "invalid_sample",
            "safe_detail": "Messwert ist keine gültige Zahl",
        }
    ]


def test_data_quality_default_window_starts_with_first_real_data_day(
    client: TestClient,
    user: User,
    monkeypatch,
) -> None:
    del user

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2024, 1, 3, 12, 0, tzinfo=tz)

    monkeypatch.setattr(analytics, "datetime", FixedDatetime)
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    csrf = login.json()["csrf_token"]
    xml = b"""<?xml version="1.0"?><HealthData><Record type="HKQuantityTypeIdentifierDietaryEnergyConsumed" sourceName="Synthetic" unit="kcal" value="1800" startDate="2024-01-02 12:00:00 +0100" endDate="2024-01-02 12:00:00 +0100" /></HealthData>"""
    client.post(
        "/api/v1/import/apple-health/file",
        headers={"X-CSRF-Token": csrf},
        files={"file": ("export.xml", xml, "application/xml")},
    )

    response = client.get("/api/v1/analytics/data-quality")

    assert response.status_code == 200
    assert response.json()["start_date"] == "2024-01-02"
    assert response.json()["end_date"] == "2024-01-03"
    assert response.json()["total_days"] == 2
    assert response.json()["recorded_days"] == 1
    assert response.json()["missing_days"] == ["2024-01-03"]


def test_admin_invitation_creates_isolated_personal_account(
    client: TestClient,
    user: User,
    db,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "calograph_public_url", "https://nutrition.example.test")
    user.is_admin = True
    db.commit()
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    csrf = login.json()["csrf_token"]
    invited = client.post(
        "/api/v1/users/invitations",
        headers={"X-CSRF-Token": csrf},
        json={"expires_in_days": 7},
    )

    registered = client.post(
        "/api/v1/auth/register",
        json={
            "invitation_token": invited.json()["token"],
            "username": "friend",
            "password": "friend-password-is-long",
        },
    )
    reused = client.post(
        "/api/v1/auth/register",
        json={
            "invitation_token": invited.json()["token"],
            "username": "other",
            "password": "other-password-is-long",
        },
    )

    assert invited.status_code == 201
    assert invited.json()["invitation_url"] == (
        f"https://nutrition.example.test/einladung/{invited.json()['token']}"
    )
    assert registered.status_code == 201
    assert registered.json()["is_admin"] is False
    assert reused.status_code == 400

    client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    friend_login = client.post(
        "/api/v1/auth/login",
        json={"username": "friend", "password": "friend-password-is-long"},
    )
    assert friend_login.status_code == 200
    assert client.get("/api/v1/imports").json() == []
    assert client.get("/api/v1/yazio/status").json()["configured"] is False
    assert client.get("/api/v1/users").status_code == 403
