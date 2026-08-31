from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.requests import Request

import app.auth.dependencies as auth_dependencies
from app.auth.dependencies import current_user, require_csrf_exclusive
from app.auth.security import session_cookie_name
from app.config import settings
from app.models import User, UserAchievement, UserSession

HIDDEN_KEYS = {
    "hidden_leap_day",
    "hidden_time_machine",
    "hidden_break_day",
    "hidden_full_house",
    "make_a_wish",
    "more_headroom",
    "the_big_picture",
    "deja_vu",
}
PASSWORD = "correct-horse-battery-staple"


def _login(client: TestClient) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": PASSWORD},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _request(client: TestClient, csrf: str) -> Request:
    raw_session = client.cookies.get(session_cookie_name())
    assert raw_session
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/achievements/reconcile",
            "headers": [
                (b"cookie", f"{session_cookie_name()}={raw_session}".encode()),
                (b"x-csrf-token", csrf.encode()),
            ],
        }
    )


def test_locked_hidden_achievements_are_opaque_placeholders_in_api(
    client: TestClient,
    user: User,
) -> None:
    del user
    _login(client)

    response = client.get("/api/v1/achievements")

    assert response.status_code == 200
    payload = response.json()
    hidden = [item for item in payload["achievements"] if item["hidden"]]
    assert len(hidden) == len(HIDDEN_KEYS)
    new_hidden = [item for item in hidden if item["sort_order"] in {240, 450, 460, 470}]
    assert len(new_hidden) == 4
    assert len(hidden) >= 3
    assert all(item["placeholder"] is True for item in hidden)
    assert all(item["unlocked"] is False for item in hidden)
    assert all(item["category"] == "hidden" for item in hidden)
    assert all("key" not in item for item in hidden)
    assert all("kind" not in item for item in hidden)
    assert all("icon" not in item for item in hidden)
    assert all("progress" not in item for item in hidden)
    assert all("target" not in item for item in hidden)
    assert not any(key in response.text for key in HIDDEN_KEYS)
    assert "More Headroom" not in response.text
    assert "Da geht noch was." not in response.text
    assert "Manchmal lohnt sich der Blick aufs Ganze." not in response.text
    assert "Moment mal" not in response.text
    assert "The Big Picture" not in response.text
    assert "Déjà Vu" not in response.text


def test_unlocked_hidden_achievement_keeps_real_key(
    client: TestClient,
    user: User,
    db,
) -> None:
    unlocked_at = datetime(2026, 8, 16, 10, tzinfo=UTC)
    db.add(
        UserAchievement(
            user_id=user.id,
            achievement_key="hidden_full_house",
            unlocked_at=unlocked_at,
        )
    )
    db.commit()
    _login(client)

    response = client.get("/api/v1/achievements")

    assert response.status_code == 200
    item = next(
        item for item in response.json()["achievements"] if item.get("key") == "hidden_full_house"
    )
    assert item["hidden"] is True
    assert item["placeholder"] is False
    assert item["unlocked"] is True
    assert item["key"] == "hidden_full_house"
    assert item["icon"] == "trophy"
    assert item["unlocked_at"].startswith("2026-08-16T10:00:00")


def test_unlocked_make_a_wish_reveals_its_definition(
    client: TestClient,
    user: User,
    db,
) -> None:
    db.add(
        UserAchievement(
            user_id=user.id,
            achievement_key="make_a_wish",
            unlocked_at=datetime(2026, 8, 31, 10, tzinfo=UTC),
        )
    )
    db.commit()
    _login(client)
    response = client.get("/api/v1/achievements")
    assert response.status_code == 200
    item = next(
        item for item in response.json()["achievements"] if item.get("key") == "make_a_wish"
    )
    assert item["key"] == "make_a_wish"
    assert item["category"] == "hidden"
    assert item["kind"] == "discovery"
    assert item["icon"] == "calendar"
    assert "progress" not in item
    assert "target" not in item
    assert item["hidden"] is True
    assert item["placeholder"] is False
    assert item["unlocked"] is True
    assert item["sort_order"] == 470
    assert item["unlocked_at"].startswith("2026-08-31T10:00:00")

def test_reconcile_rate_limit_preserves_session(
    client: TestClient,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del user
    monkeypatch.setattr(settings, "reconcile_rate_limit", 1)
    monkeypatch.setattr(settings, "reconcile_ip_rate_limit", 100)
    csrf = _login(client)

    first = client.post("/api/v1/achievements/reconcile", headers={"X-CSRF-Token": csrf})
    second = client.post("/api/v1/achievements/reconcile", headers={"X-CSRF-Token": csrf})

    assert first.status_code == 200
    assert second.status_code == 429
    assert client.get("/api/v1/auth/me").status_code == 200

def test_listing_and_reconcile_share_rate_limit(
    client: TestClient,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del user
    monkeypatch.setattr(settings, "reconcile_rate_limit", 1)
    monkeypatch.setattr(settings, "reconcile_ip_rate_limit", 100)
    csrf = _login(client)

    first = client.get("/api/v1/achievements")
    second = client.post("/api/v1/achievements/reconcile", headers={"X-CSRF-Token": csrf})

    assert first.status_code == 200
    assert second.status_code == 429
    assert client.get("/api/v1/auth/me").status_code == 200


def test_reconcile_rejects_revoked_session_after_initial_authentication(
    client: TestClient,
    user: User,
    db,
) -> None:
    del user
    csrf = _login(client)
    session = db.scalar(select(UserSession))
    assert session is not None
    session.revoked_at = datetime.now(UTC)
    db.commit()

    response = client.post("/api/v1/achievements/reconcile", headers={"X-CSRF-Token": csrf})

    assert response.status_code == 401
    assert db.scalar(select(UserAchievement)) is None


def test_reconcile_revalidates_inactive_user_under_exclusive_lock(
    client: TestClient,
    user: User,
    db,
) -> None:
    csrf = _login(client)
    request = _request(client, csrf)
    authenticated_user = current_user(request, db)
    user.is_active = False
    user.deactivated_at = datetime.now(UTC)
    db.commit()

    with pytest.raises(HTTPException) as error:
        next(require_csrf_exclusive(request, csrf, authenticated_user, db))
        pytest.fail("Inactive user passed the locked revalidation")

    assert error.value.status_code == 401
    assert db.scalar(select(UserAchievement)) is None

def test_reconcile_revalidates_expired_session_under_exclusive_lock(
    client: TestClient,
    user: User,
    db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    csrf = _login(client)
    request = _request(client, csrf)
    authenticated_user = current_user(request, db)
    session = db.scalar(select(UserSession))
    assert session is not None
    session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    monkeypatch.setattr(auth_dependencies, "_validate_csrf", lambda *args: None)

    with pytest.raises(HTTPException) as error:
        next(require_csrf_exclusive(request, csrf, authenticated_user, db))
        pytest.fail("Expired session passed the locked revalidation")

    assert error.value.status_code == 401
    assert db.scalar(select(UserAchievement)) is None
