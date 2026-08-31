import argparse
from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import hash_password
from app.cli import create_user
from app.models import NutritionTarget, User, UserOnboarding

PASSWORD = "correct-horse-battery-staple"


def _login(client: TestClient, username: str = "admin") -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": PASSWORD},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _new_user(db: Session, username: str) -> User:
    item = User(username=username, password_hash=hash_password(PASSWORD), timezone="UTC")
    db.add(item)
    db.flush()
    return item


def _target(db: Session, user_id: object) -> NutritionTarget:
    item = NutritionTarget(
        user_id=user_id,
        valid_from=date(2024, 1, 1),
        calories_kcal=Decimal("2000"),
        protein_g=Decimal("120"),
    )
    db.add(item)
    return item


def test_legacy_status_distinguishes_missing_target(client: TestClient, db: Session) -> None:
    user = _new_user(db, "legacy-no-target")
    db.commit()
    csrf = _login(client, user.username)
    status = client.get("/api/v1/settings/onboarding")
    assert status.status_code == 200
    assert status.json() == {
        "mode": "legacy",
        "required": True,
        "completed": False,
        "current_step": "targets",
    }
    assert client.post(
        "/api/v1/settings/onboarding/advance",
        headers={"X-CSRF-Token": csrf},
        json={"expected_step": "targets"},
    ).status_code == 409


def test_legacy_target_is_complete(client: TestClient, db: Session) -> None:
    user = _new_user(db, "legacy-with-target")
    _target(db, user.id)
    db.commit()
    _login(client, user.username)
    status = client.get("/api/v1/settings/onboarding")
    assert status.json()["mode"] == "legacy"
    assert status.json()["required"] is False
    assert status.json()["completed"] is True
    assert status.json()["current_step"] == "completed"


def test_full_onboarding_requires_target_and_is_idempotent(
    client: TestClient, db: Session
) -> None:
    user = _new_user(db, "full-onboarding")
    db.add(UserOnboarding(user_id=user.id))
    db.commit()
    csrf = _login(client, user.username)

    assert client.get("/api/v1/settings/onboarding").json()["current_step"] == "personal"
    personal = client.post(
        "/api/v1/settings/onboarding/advance",
        headers={"X-CSRF-Token": csrf},
        json={"expected_step": "personal"},
    )
    assert personal.status_code == 200
    assert personal.json()["current_step"] == "targets"
    retry = client.post(
        "/api/v1/settings/onboarding/advance",
        headers={"X-CSRF-Token": csrf},
        json={"expected_step": "personal"},
    )
    assert retry.status_code == 200
    assert retry.json()["current_step"] == "targets"

    missing = client.post(
        "/api/v1/settings/onboarding/advance",
        headers={"X-CSRF-Token": csrf},
        json={"expected_step": "targets"},
    )
    assert missing.status_code == 422
    _target(db, user.id)
    db.commit()
    assert client.post(
        "/api/v1/settings/onboarding/advance",
        headers={"X-CSRF-Token": csrf},
        json={"expected_step": "targets"},
    ).json()["current_step"] == "security"
    assert client.post(
        "/api/v1/settings/onboarding/advance",
        headers={"X-CSRF-Token": csrf},
        json={"expected_step": "security"},
    ).json()["completed"] is True


def test_full_onboarding_rejects_skips_and_enforces_csrf(
    client: TestClient, db: Session
) -> None:
    user = _new_user(db, "full-invalid-step")
    db.add(UserOnboarding(user_id=user.id))
    db.commit()
    csrf = _login(client, user.username)
    skipped = client.post(
        "/api/v1/settings/onboarding/advance",
        headers={"X-CSRF-Token": csrf},
        json={"expected_step": "security"},
    )
    assert skipped.status_code == 409
    no_csrf = client.post(
        "/api/v1/settings/onboarding/advance", json={"expected_step": "personal"}
    )
    assert no_csrf.status_code == 403


def test_onboarding_status_is_user_isolated(
    client: TestClient, db: Session, user: User
) -> None:
    del user
    other = _new_user(db, "isolated-onboarding")
    db.add(UserOnboarding(user_id=other.id))
    db.commit()
    _login(client)
    status = client.get("/api/v1/settings/onboarding")
    assert status.status_code == 200
    assert status.json()["mode"] == "legacy"
    assert db.scalar(select(UserOnboarding).where(UserOnboarding.user_id == other.id)) is not None
def _cli_args(username: str, *, skip_onboarding: bool) -> argparse.Namespace:
    return argparse.Namespace(
        username=username,
        password=PASSWORD,
        timezone="UTC",
        raw_retention_days=0,
        admin=False,
        if_not_exists=False,
        skip_onboarding=skip_onboarding,
    )


def test_cli_create_user_creates_full_onboarding_row(db: Session) -> None:
    create_user(_cli_args("cli-full", skip_onboarding=False))
    created = db.scalar(select(User).where(User.username == "cli-full"))
    assert created is not None
    assert db.get(UserOnboarding, created.id) is not None


def test_cli_create_user_explicit_opt_out_creates_completed_onboarding_row(db: Session) -> None:
    create_user(_cli_args("cli-legacy", skip_onboarding=True))
    created = db.scalar(select(User).where(User.username == "cli-legacy"))
    assert created is not None
    onboarding = db.get(UserOnboarding, created.id)
    assert onboarding is not None
    assert onboarding.current_step == "completed"
    assert onboarding.completed_at is not None


def test_onboarding_row_cascades_with_user(db: Session) -> None:
    user = _new_user(db, "cascade-onboarding")
    row = UserOnboarding(user_id=user.id)
    db.add(row)
    db.commit()
    db.delete(user)
    db.commit()
    assert db.get(UserOnboarding, user.id) is None
