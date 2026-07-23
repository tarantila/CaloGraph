from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import yazio as yazio_api
from app.config import settings
from app.models import HealthSample, User, YazioConnection
from app.schemas import ImportSummary
from app.services.credential_crypto import (
    decrypt_credential,
    encrypt_credential,
)
from app.services.yazio_sync import (
    YazioSyncError,
    configure_yazio_connection,
    due_yazio_connection_ids,
    run_manual_yazio_sync,
    run_scheduled_yazio_sync,
)


def _configure_key(monkeypatch) -> str:
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "credential_encryption_key", key)
    return key


def test_credentials_are_authenticated_and_encrypted(monkeypatch) -> None:
    _configure_key(monkeypatch)

    encrypted = encrypt_credential("very-secret")

    assert b"very-secret" not in encrypted
    assert decrypt_credential(encrypted) == "very-secret"


def test_connection_is_per_user_and_due(
    db: Session, user: User, monkeypatch
) -> None:
    _configure_key(monkeypatch)

    connection = configure_yazio_connection(
        user,
        "owner@example.com",
        "yazio-password",
        sync_interval_minutes=360,
        sync_days=7,
    )

    stored = db.scalar(
        select(YazioConnection).where(YazioConnection.user_id == user.id)
    )
    assert stored is not None
    assert stored.id == connection.id
    assert b"owner@example.com" not in stored.encrypted_email
    assert b"yazio-password" not in stored.encrypted_password
    assert stored.source_identifier.startswith("yazio:")
    assert stored.sync_days == 7
    assert connection.id in due_yazio_connection_ids()


def test_manual_sync_imports_only_for_connection_user(
    db: Session, user: User, monkeypatch
) -> None:
    _configure_key(monkeypatch)
    connection = configure_yazio_connection(
        user,
        "owner@example.com",
        "yazio-password",
        sync_interval_minutes=360,
        sync_days=7,
    )
    attempted_at = datetime(2026, 7, 23, 8, tzinfo=UTC)

    def fake_fetch(email, password, start_day, end_day, include_micronutrients):
        assert email == "owner@example.com"
        assert password == "yazio-password"
        assert start_day.isoformat() == "2026-07-17"
        assert end_day.isoformat() == "2026-07-23"
        assert include_micronutrients is True
        return {
            "2026-07-23": {
                "daily_summary": {
                    "meals": {
                        "dinner": {
                            "nutrients": {
                                "energy.energy": 1800,
                                "nutrient.protein": 120,
                                "nutrient.carb": 190,
                                "nutrient.fat": 60,
                            }
                        }
                    }
                }
            }
        }

    summary = run_manual_yazio_sync(
        user.id,
        fetcher=fake_fetch,
        now=attempted_at,
    )

    assert summary is not None
    assert summary.inserted == 4
    samples = list(
        db.scalars(select(HealthSample).where(HealthSample.user_id == user.id))
    )
    assert len(samples) == 4
    db.expire_all()
    stored = db.get(YazioConnection, connection.id)
    assert stored is not None
    assert stored.last_success_at is not None
    assert stored.last_micronutrient_sync_at is not None
    assert stored.last_error is None
    assert stored.next_sync_at is not None
    delay = stored.next_sync_at - stored.last_success_at
    assert timedelta(hours=6, minutes=1) <= delay <= timedelta(hours=6, minutes=30)

    first_micronutrient_sync = stored.last_micronutrient_sync_at

    def fake_scheduled_fetch(
        email, password, start_day, end_day, include_micronutrients
    ):
        del email, password, start_day, end_day
        assert include_micronutrients is False
        return {
            "2026-07-23": {
                "daily_summary": {
                    "meals": {
                        "dinner": {
                            "nutrients": {
                                "energy.energy": 1800,
                                "nutrient.protein": 120,
                                "nutrient.carb": 190,
                                "nutrient.fat": 60,
                            }
                        }
                    }
                }
            }
        }

    assert (
        run_scheduled_yazio_sync(
            connection.id,
            fetcher=fake_scheduled_fetch,
            now=attempted_at + timedelta(hours=6),
        )
        is not None
    )
    db.expire_all()
    stored = db.get(YazioConnection, connection.id)
    assert stored is not None
    assert stored.last_micronutrient_sync_at == first_micronutrient_sync


def test_scheduled_sync_records_safe_failure(
    db: Session, user: User, monkeypatch
) -> None:
    _configure_key(monkeypatch)
    connection = configure_yazio_connection(
        user,
        "owner@example.com",
        "yazio-password",
    )

    def failing_fetch(email, password, start_day, end_day, include_micronutrients):
        del email, password, start_day, end_day, include_micronutrients
        raise YazioSyncError("YAZIO ist vorübergehend nicht erreichbar.")

    summary = run_scheduled_yazio_sync(connection.id, fetcher=failing_fetch)

    assert summary is None
    db.expire_all()
    stored = db.get(YazioConnection, connection.id)
    assert stored is not None
    assert stored.last_success_at is None
    assert stored.last_error == "YAZIO ist vorübergehend nicht erreichbar."
    assert stored.next_sync_at is not None
    assert stored.last_attempt_at is not None
    retry_delay = stored.next_sync_at - stored.last_attempt_at
    assert timedelta(hours=1, minutes=1) <= retry_delay <= timedelta(hours=1, minutes=30)


def test_yazio_api_status_and_manual_sync_are_user_scoped(
    client: TestClient,
    user: User,
    monkeypatch,
) -> None:
    _configure_key(monkeypatch)
    configure_yazio_connection(user, "owner@example.com", "yazio-password")
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    csrf = login.json()["csrf_token"]

    status = client.get("/api/v1/yazio/status")
    assert status.status_code == 200
    assert status.json()["configured"] is True
    assert status.json()["sync_interval_minutes"] == 360
    assert status.json()["sync_days"] == 7

    without_csrf = client.post("/api/v1/yazio/sync")
    assert without_csrf.status_code == 403

    called_for = None

    def fake_manual_sync(user_id):
        nonlocal called_for
        called_for = user_id
        return ImportSummary(
            status="completed",
            received=7,
            inserted=1,
            updated=2,
            skipped=4,
        )

    monkeypatch.setattr(yazio_api, "run_manual_yazio_sync", fake_manual_sync)
    response = client.post(
        "/api/v1/yazio/sync",
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 200
    assert response.json()["inserted"] == 1
    assert called_for == user.id
