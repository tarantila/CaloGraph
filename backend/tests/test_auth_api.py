from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from app.api import analytics
from app.auth import security
from app.config import settings
from app.main import app
from app.models import NutritionTarget, TrackingQualitySettings, User, UserSession
from app.schemas import TargetInput


def test_login_csrf_and_logout(client: TestClient, user: User) -> None:
    del user
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    assert response.status_code == 200
    csrf = response.json()["csrf_token"]
    session_cookie = response.headers["set-cookie"].lower()
    assert "calograph_session=" in session_cookie
    assert "__host-" not in session_cookie
    assert "httponly" in session_cookie
    assert "samesite=lax" in session_cookie
    assert "path=/" in session_cookie
    forbidden = client.post("/api/v1/auth/logout")
    assert forbidden.status_code == 403
    logged_out = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    assert logged_out.status_code == 204


def test_production_session_cookie_uses_host_prefix(
    client: TestClient,
    user: User,
    monkeypatch,
) -> None:
    del user
    monkeypatch.setattr(settings, "cookie_secure", True)

    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )

    session_cookie = response.headers["set-cookie"].lower()
    assert response.status_code == 200
    assert "__host-calograph_session=" in session_cookie
    assert "secure" in session_cookie
    assert "httponly" in session_cookie
    assert "samesite=lax" in session_cookie
    assert "path=/" in session_cookie
    assert "domain=" not in session_cookie


def test_idle_and_absolute_session_timeouts_are_server_enforced(
    client: TestClient,
    user: User,
    db,
) -> None:
    del user
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    assert login.status_code == 200
    session = db.scalar(select(UserSession))
    assert session is not None
    session.last_used_at = datetime.now(UTC) - timedelta(
        hours=settings.session_idle_timeout_hours + 1
    )
    db.commit()

    assert client.get("/api/v1/auth/me").status_code == 401

    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    assert login.status_code == 200
    newest = db.scalars(select(UserSession).order_by(UserSession.created_at.desc())).first()
    assert newest is not None
    newest.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()

    assert client.get("/api/v1/auth/me").status_code == 401


def test_session_activity_timestamp_is_throttled(
    client: TestClient,
    user: User,
    db,
) -> None:
    del user
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    assert login.status_code == 200
    session = db.scalar(select(UserSession))
    assert session is not None
    session_id = session.id
    initial_last_used_at = session.last_used_at
    assert initial_last_used_at is not None

    assert client.get("/api/v1/auth/me").status_code == 200
    db.expire_all()
    session = db.get(UserSession, session_id)
    assert session is not None
    assert session.last_used_at == initial_last_used_at

    stale_last_used_at = datetime.now(UTC) - timedelta(minutes=6)
    session.last_used_at = stale_last_used_at
    db.commit()

    assert client.get("/api/v1/auth/me").status_code == 200
    db.expire_all()
    session = db.get(UserSession, session_id)
    assert session is not None
    assert session.last_used_at is not None
    refreshed_last_used_at = session.last_used_at
    if refreshed_last_used_at.tzinfo is None:
        refreshed_last_used_at = refreshed_last_used_at.replace(tzinfo=UTC)
    assert refreshed_last_used_at > stale_last_used_at


def test_expired_and_revoked_sessions_are_purged(
    client: TestClient,
    user: User,
    db,
) -> None:
    del user
    client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    session = db.scalar(select(UserSession))
    assert session is not None
    session.revoked_at = datetime.now(UTC)
    db.commit()

    assert security.purge_expired_sessions(db) == 1
    assert db.scalar(select(UserSession)) is None


def test_password_change_rejects_common_password(
    client: TestClient,
    user: User,
) -> None:
    del user
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )

    response = client.post(
        "/api/v1/auth/password",
        headers={"X-CSRF-Token": login.json()["csrf_token"]},
        json={
            "current_password": "correct-horse-battery-staple",
            "new_password": "123456789qwerty",
        },
    )

    assert response.status_code == 422
    assert "häufig verwendet" in response.json()["detail"]


def test_password_change_rejects_guessable_repetition(
    client: TestClient,
    user: User,
) -> None:
    del user
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )

    response = client.post(
        "/api/v1/auth/password",
        headers={"X-CSRF-Token": login.json()["csrf_token"]},
        json={
            "current_password": "correct-horse-battery-staple",
            "new_password": "testtesttesttest1",
        },
    )

    assert response.status_code == 422
    assert "Wiederholungs- oder Sequenzmuster" in response.json()["detail"]


def test_successful_password_change_revokes_every_existing_session(
    client: TestClient,
    user: User,
) -> None:
    del user
    other_client = TestClient(app)
    first_login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    second_login = other_client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    assert first_login.status_code == 200
    assert second_login.status_code == 200

    changed = client.post(
        "/api/v1/auth/password",
        headers={"X-CSRF-Token": first_login.json()["csrf_token"]},
        json={
            "current_password": "correct-horse-battery-staple",
            "new_password": "new-unique-passphrase-for-regression",
        },
    )

    assert changed.status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401
    assert other_client.get("/api/v1/auth/me").status_code == 401
    assert (
        client.post(
            "/api/v1/auth/login",
            json={
                "username": "admin",
                "password": "correct-horse-battery-staple",
            },
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/auth/login",
            json={
                "username": "admin",
                "password": "new-unique-passphrase-for-regression",
            },
        ).status_code
        == 200
    )


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


@pytest.mark.parametrize(
    ("calories_kcal", "maintenance_kcal"),
    [
        (2000, 2500),
        (2500, 2500),
        (3000, 2500),
        (3000, None),
    ],
)
def test_existing_target_version_accepts_independent_maintenance_estimates(
    client: TestClient,
    user: User,
    calories_kcal: int,
    maintenance_kcal: int | None,
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
            "calories_kcal": calories_kcal,
            "maintenance_kcal": maintenance_kcal,
            "protein_g": 150,
            "carbs_g": 260,
            "fat_g": 80,
            "fiber_g": 30,
        },
    )

    assert response.status_code == 200
    expected_maintenance = (
        None if maintenance_kcal is None else f"{maintenance_kcal}.000"
    )
    assert response.json()["calories_kcal"] == f"{calories_kcal}.000"
    assert response.json()["maintenance_kcal"] == expected_maintenance
    saved = client.get("/api/v1/settings/targets")
    assert saved.status_code == 200
    assert saved.json()[0]["calories_kcal"] == f"{calories_kcal}.000"
    assert saved.json()[0]["maintenance_kcal"] == expected_maintenance


@pytest.mark.parametrize("maintenance_kcal", [0, -1])
def test_target_rejects_non_positive_maintenance(
    client: TestClient,
    user: User,
    maintenance_kcal: int,
) -> None:
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )

    response = client.put(
        "/api/v1/settings/targets/2024-01-01",
        headers={"X-CSRF-Token": login.json()["csrf_token"]},
        json={
            "valid_from": "2024-01-01",
            "calories_kcal": 3000,
            "maintenance_kcal": maintenance_kcal,
            "protein_g": 150,
        },
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "maintenance_kcal",
    [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")],
)
def test_target_rejects_non_finite_maintenance(
    maintenance_kcal: Decimal,
) -> None:
    with pytest.raises(ValidationError):
        TargetInput(
            valid_from=date(2026, 8, 11),
            calories_kcal=Decimal("3000"),
            maintenance_kcal=maintenance_kcal,
            protein_g=Decimal("140"),
        )


def test_target_delete_preserves_history_and_requires_one_version(
    client: TestClient,
    user: User,
    db,
) -> None:
    other_user = User(
        username="other-target-owner",
        password_hash=security.hash_password("other-password"),
    )
    db.add(other_user)
    db.flush()
    other_target = NutritionTarget(
        user_id=other_user.id,
        valid_from=date(2024, 3, 1),
        calories_kcal=Decimal("1900"),
        carbs_g=Decimal("200"),
        protein_g=Decimal("100"),
        fat_g=Decimal("60"),
    )
    db.add(other_target)
    db.commit()
    del user
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    csrf = login.json()["csrf_token"]
    forbidden_delete = client.delete(
        "/api/v1/settings/targets/2024-03-01",
        headers={"X-CSRF-Token": csrf},
    )
    assert forbidden_delete.status_code == 404
    assert db.scalar(select(NutritionTarget).where(NutritionTarget.id == other_target.id)) is not None
    created = client.post(
        "/api/v1/settings/targets",
        headers={"X-CSRF-Token": csrf},
        json={
            "valid_from": "2024-02-01",
            "calories_kcal": 2100,
            "protein_g": 130,
        },
    )
    assert created.status_code == 201

    deleted = client.delete(
        "/api/v1/settings/targets/2024-01-01",
        headers={"X-CSRF-Token": csrf},
    )
    assert deleted.status_code == 204
    remaining = client.get("/api/v1/settings/targets")
    assert remaining.status_code == 200
    assert [item["valid_from"] for item in remaining.json()] == ["2024-02-01"]
    assert remaining.json()[0]["valid_to"] is None

    deleted = client.delete(
        "/api/v1/settings/targets/2024-02-01",
        headers={"X-CSRF-Token": csrf},
    )
    assert deleted.status_code == 409
    assert deleted.json()["type"].endswith("last-target-required")


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


def test_browser_file_import_has_size_and_rate_limits(
    client: TestClient,
    user: User,
    monkeypatch,
) -> None:
    del user
    monkeypatch.setattr(settings, "max_upload_bytes", 128)
    monkeypatch.setattr(settings, "file_import_user_rate_limit", 1)
    monkeypatch.setattr(settings, "file_import_ip_rate_limit", 100)
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    csrf = login.json()["csrf_token"]

    too_large = client.post(
        "/api/v1/import/apple-health/file",
        headers={"X-CSRF-Token": csrf},
        files={"file": ("export.xml", b"x" * 129, "application/xml")},
    )
    xml = b"""<?xml version="1.0"?><HealthData></HealthData>"""
    accepted = client.post(
        "/api/v1/import/apple-health/file",
        headers={"X-CSRF-Token": csrf},
        files={"file": ("export.xml", xml, "application/xml")},
    )
    limited = client.post(
        "/api/v1/import/apple-health/file",
        headers={"X-CSRF-Token": csrf},
        files={"file": ("export.xml", xml, "application/xml")},
    )

    assert too_large.status_code == 413
    assert accepted.status_code == 200
    assert limited.status_code == 429


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
    invitation_url = invited.json()["invitation_url"]
    raw_token = invitation_url.partition("#token=")[2]
    exchanged = client.post(
        "/api/v1/auth/invitation/exchange",
        json={"token": raw_token},
    )
    replayed_exchange = client.post(
        "/api/v1/auth/invitation/exchange",
        json={"token": raw_token},
    )
    state = client.get("/api/v1/auth/invitation/status")

    guessable = client.post(
        "/api/v1/auth/register",
        json={
            "username": "friend",
            "password": "abcabcabcabcabc1",
        },
    )

    registered = client.post(
        "/api/v1/auth/register",
        json={
            "username": "friend",
            "password": "friend-password-is-long",
        },
    )
    reused = client.post(
        "/api/v1/auth/register",
        json={
            "username": "other",
            "password": "other-password-is-long",
        },
    )

    assert invited.status_code == 201
    assert "token" not in invited.json()
    assert invitation_url == f"https://nutrition.example.test/einladung#token={raw_token}"
    assert raw_token.startswith("invite_")
    assert exchanged.status_code == 204
    registration_cookie = exchanged.headers["set-cookie"].lower()
    assert "calograph_registration=" in registration_cookie
    assert "httponly" in registration_cookie
    assert "samesite=strict" in registration_cookie
    assert "path=/api/v1/auth" in registration_cookie
    assert replayed_exchange.status_code == 400
    assert state.json() == {"valid": True}
    assert guessable.status_code == 422
    assert "Wiederholungs- oder Sequenzmuster" in guessable.json()["detail"]
    assert registered.status_code == 201
    assert registered.json()["is_admin"] is False
    assert "calograph_registration=" in registered.headers["set-cookie"].lower()
    assert "max-age=0" in registered.headers["set-cookie"].lower()
    assert reused.status_code == 400
    registered_user = db.scalar(select(User).where(User.username == "friend"))
    assert registered_user is not None
    assert registered_user.language == "de"
    assert (
        db.scalar(select(NutritionTarget).where(NutritionTarget.user_id == registered_user.id))
        is None
    )
    assert db.get(TrackingQualitySettings, registered_user.id) is not None

    client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    friend_login = client.post(
        "/api/v1/auth/login",
        json={"username": "friend", "password": "friend-password-is-long"},
    )
    assert friend_login.status_code == 200
    assert client.get("/api/v1/imports").json() == []
    assert client.get("/api/v1/yazio/status").json()["configured"] is False
    assert client.get("/api/v1/users").status_code == 403
    assert client.get("/api/v1/settings/targets").json() == []
    targetless = client.get("/api/v1/analytics/daily?start=2026-08-11&end=2026-08-11")
    assert targetless.status_code == 200
    assert targetless.json()[0]["target_kcal"] is None
    targetless_summary = client.get("/api/v1/dashboard/summary")
    assert targetless_summary.status_code == 200
    assert targetless_summary.json()["week"]["budget_kcal"] is None
    assert targetless_summary.json()["week"]["deviation_kcal"] is None
    targetless_week = client.get("/api/v1/analytics/weekly?start=2026-08-11&end=2026-08-11")
    assert targetless_week.status_code == 200
    assert targetless_week.json()["weeks"][0]["budget_kcal"] is None
    assert targetless_week.json()["weeks"][0]["remaining_kcal"] is None
    created_target = client.post(
        "/api/v1/settings/targets",
        headers={"X-CSRF-Token": friend_login.json()["csrf_token"]},
        json={
            "valid_from": "2026-08-11",
            "calories_kcal": 2150,
            "maintenance_kcal": None,
            "protein_g": 135,
            "carbs_g": None,
            "fat_g": None,
            "fiber_g": None,
        },
    )
    assert created_target.status_code == 201
    assert client.get("/api/v1/settings/targets").json()[0]["calories_kcal"] == "2150.000"


def test_invitation_expiration_is_capped_at_seven_days(
    client: TestClient,
    user: User,
    db,
) -> None:
    user.is_admin = True
    db.commit()
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )

    response = client.post(
        "/api/v1/users/invitations",
        headers={"X-CSRF-Token": login.json()["csrf_token"]},
        json={"expires_in_days": 8},
    )

    assert response.status_code == 422

def test_profile_language_is_validated_and_persisted(
    client: TestClient,
    user: User,
) -> None:
    del user
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    csrf = login.json()["csrf_token"]

    updated = client.put(
        "/api/v1/settings/profile",
        headers={"X-CSRF-Token": csrf},
        json={
            "language": "en",
            "timezone": "Europe/Berlin",
            "week_starts_on": 0,
            "raw_payload_retention_days": 0,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["language"] == "en"
    assert client.get("/api/v1/settings/profile").json()["language"] == "en"
    partial = client.put(
        "/api/v1/settings/profile",
        headers={"X-CSRF-Token": csrf},
        json={"language": "de"},
    )
    assert partial.status_code == 200
    assert partial.json()["language"] == "de"
    assert partial.json()["timezone"] == "Europe/Berlin"
    assert partial.json()["week_starts_on"] == 0
    assert partial.json()["raw_payload_retention_days"] == 0


    invalid = client.put(
        "/api/v1/settings/profile",
        headers={"X-CSRF-Token": csrf},
        json={
            "language": "fr",
            "timezone": "Europe/Berlin",
            "week_starts_on": 0,
            "raw_payload_retention_days": 0,
        },
    )
    assert invalid.status_code == 422
    assert invalid.json()["type"] == "urn:calograph:problem:validation-error"


def test_invalid_login_exposes_a_stable_problem_type(
    client: TestClient,
    user: User,
) -> None:
    del user
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "wrong-password"},
    )

    assert response.status_code == 401
    assert response.json()["type"] == "urn:calograph:problem:invalid-credentials"
