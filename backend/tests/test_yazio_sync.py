import json
import logging
from datetime import UTC, date, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import security_events
from app.api import yazio as yazio_api
from app.config import settings
from app.models import HealthSample, User, YazioConnection
from app.schemas import ImportSummary
from app.security_events import security_reference
from app.services import yazio_sync
from app.services.credential_crypto import (
    CredentialEncryptionError,
    decrypt_credential,
    encrypt_credential,
)
from app.services.yazio_guard import YazioOperationBusy, yazio_operation_slot
from app.services.yazio_sync import (
    YazioAuthenticationError,
    YazioConnectionDisabled,
    YazioSyncError,
    configure_yazio_connection,
    due_yazio_connection_ids,
    enqueue_historical_yazio_sync,
    run_due_yazio_syncs,
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
    assert stored.source_identifier == f"yazio:{user.id}"
    assert not hasattr(stored, "account_hash")
    assert stored.sync_days == 7
    assert connection.id in due_yazio_connection_ids()


def test_reconfiguring_yazio_email_keeps_opaque_source_identifier(
    db: Session, user: User, monkeypatch
) -> None:
    _configure_key(monkeypatch)
    first = configure_yazio_connection(
        user,
        "first-owner@example.com",
        "yazio-password",
    )
    second = configure_yazio_connection(
        user,
        "replacement-owner@example.net",
        "replacement-password",
    )

    db.expire_all()
    stored = db.get(YazioConnection, first.id)
    assert stored is not None
    assert second.id == first.id
    assert stored.source_identifier == f"yazio:{user.id}"
    assert "first-owner" not in stored.source_identifier
    assert "replacement-owner" not in stored.source_identifier


def test_connection_slot_covers_the_complete_sync_and_status_update(
    user: User,
    monkeypatch,
) -> None:
    _configure_key(monkeypatch)
    connection = configure_yazio_connection(
        user,
        "owner@example.com",
        "yazio-password",
    )
    expected = ImportSummary(
        status="completed",
        received=0,
        inserted=0,
        updated=0,
        skipped=0,
    )

    def run_while_locked(connection_id, **_kwargs):
        assert connection_id == connection.id
        with pytest.raises(YazioOperationBusy), yazio_operation_slot(user.id):
            pass
        return expected

    monkeypatch.setattr(
        yazio_sync,
        "_run_yazio_connection_sync_locked",
        run_while_locked,
    )

    assert run_manual_yazio_sync(user.id) == expected


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
        assert start_day.isoformat() == "2026-05-25"
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
        sync_days=60,
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
    stored.initial_sync_state = "completed"
    stored.historical_sync_state = "completed"
    db.commit()


    first_micronutrient_sync = stored.last_micronutrient_sync_at

    def fake_scheduled_fetch(
        email, password, start_day, end_day, include_micronutrients
    ):
        del email, password
        assert start_day.isoformat() == "2026-07-17"
        assert end_day.isoformat() == "2026-07-23"
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



@pytest.mark.parametrize("mode", ["manual", "scheduled"])
def test_credential_decryption_failure_emits_one_safe_security_event(
    db: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    _configure_key(monkeypatch)
    email = "sensitive-owner@example.com"
    password = "sensitive-yazio-password"
    raw_error = "sensitive-decryption-exception"
    connection = configure_yazio_connection(user, email, password)
    stored = db.get(YazioConnection, connection.id)
    assert stored is not None
    encrypted_email = stored.encrypted_email.decode()
    encrypted_password = stored.encrypted_password.decode()
    records: list[tuple[int, str]] = []
    monkeypatch.setattr(
        security_events.logger,
        "log",
        lambda level, message: records.append((level, message)),
    )

    def fail_decryption(_value: bytes) -> str:
        raise CredentialEncryptionError(raw_error)

    monkeypatch.setattr(yazio_sync, "decrypt_credential", fail_decryption)

    if mode == "manual":
        with pytest.raises(
            YazioSyncError,
            match="Gespeicherte YAZIO-Zugangsdaten konnten nicht entschlüsselt werden",
        ):
            run_manual_yazio_sync(user.id)
    else:
        assert run_scheduled_yazio_sync(connection.id) is None

    assert len(records) == 1
    level, serialized = records[0]
    payload = json.loads(serialized)
    assert level == logging.WARNING
    assert set(payload) == {
        "timestamp",
        "event",
        "outcome",
        "actor_ref",
        "target_ref",
        "reason",
        "mode",
    }
    assert payload["event"] == "integration.yazio.sync_failed"
    assert payload["outcome"] == "failure"
    assert payload["actor_ref"] == security_reference("user", user.id)
    assert payload["target_ref"] == security_reference(
        "yazio_connection", connection.id
    )
    assert payload["reason"] == "credential_decryption_error"
    assert payload["mode"] == ("initial" if mode == "scheduled" else mode)
    for sensitive_value in (
        email,
        password,
        encrypted_email,
        encrypted_password,
        raw_error,
        str(user.id),
        str(connection.id),
    ):
        assert sensitive_value not in serialized
    db.expire_all()
    stored = db.get(YazioConnection, connection.id)
    assert stored is not None
    assert stored.last_success_at is None
    assert stored.last_error == "Gespeicherte YAZIO-Zugangsdaten konnten nicht entschlüsselt werden."
    assert stored.next_sync_at is not None

def test_fully_rejected_payload_is_not_recorded_as_success(
    db: Session, user: User, monkeypatch
) -> None:
    _configure_key(monkeypatch)
    connection = configure_yazio_connection(
        user,
        "owner@example.com",
        "yazio-password",
    )

    def overprecise_fetch(
        email, password, start_day, end_day, include_micronutrients
    ):
        del email, password, start_day, end_day, include_micronutrients
        return {
            "2026-07-23": {
                "daily_summary": {
                    "meals": {
                        "dinner": {
                            "nutrients": {
                                "energy.energy": "0.1234567890123",
                            }
                        }
                    }
                }
            }
        }

    assert (
        run_scheduled_yazio_sync(connection.id, fetcher=overprecise_fetch) is None
    )
    db.expire_all()
    stored = db.get(YazioConnection, connection.id)
    assert stored is not None
    assert stored.last_success_at is None
    assert stored.last_error == "YAZIO-Daten konnten nicht verarbeitet werden."
    assert stored.next_sync_at is not None

    with pytest.raises(
        YazioSyncError,
        match="YAZIO-Daten konnten nicht verarbeitet werden",
    ):
        run_manual_yazio_sync(user.id, fetcher=overprecise_fetch)


def test_authentication_failure_disables_automatic_retries(
    db: Session, user: User, monkeypatch
) -> None:
    _configure_key(monkeypatch)
    connection = configure_yazio_connection(
        user,
        "owner@example.com",
        "yazio-password",
    )

    def invalid_credentials(
        email, password, start_day, end_day, include_micronutrients
    ):
        del email, password, start_day, end_day, include_micronutrients
        raise YazioAuthenticationError(
            "YAZIO-Anmeldung fehlgeschlagen. Zugangsdaten aktualisieren."
        )

    assert (
        run_scheduled_yazio_sync(connection.id, fetcher=invalid_credentials) is None
    )

    db.expire_all()
    stored = db.get(YazioConnection, connection.id)
    assert stored is not None
    assert stored.sync_enabled is False
    assert stored.next_sync_at is None
    assert "Zugangsdaten aktualisieren" in (stored.last_error or "")
    assert connection.id not in due_yazio_connection_ids()


def test_yazio_feature_flag_stops_api_and_scheduler(
    client: TestClient,
    user: User,
    monkeypatch,
) -> None:
    del user
    monkeypatch.setattr(settings, "yazio_enabled", False)
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    csrf = login.json()["csrf_token"]

    status = client.get("/api/v1/yazio/status")
    response = client.put(
        "/api/v1/yazio/connection",
        headers={"X-CSRF-Token": csrf},
        json={
            "email": "owner@example.com",
            "password": "yazio-password",
            "interval_hours": 6,
            "sync_days": 7,
        },
    )

    assert status.status_code == 200
    assert status.json()["available"] is False
    assert response.status_code == 503
    assert run_due_yazio_syncs() == (0, 0)


def test_yazio_connection_attempts_are_rate_limited(
    client: TestClient,
    user: User,
    monkeypatch,
) -> None:
    _configure_key(monkeypatch)
    monkeypatch.setattr(settings, "yazio_rate_limit", 2)
    monkeypatch.setattr(settings, "yazio_rate_limit_window_seconds", 600)
    monkeypatch.setattr(
        yazio_api,
        "validate_yazio_credentials",
        lambda *_args, **_kwargs: None,
    )
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    csrf = login.json()["csrf_token"]
    payload = {
        "email": "owner@example.com",
        "password": "yazio-password",
        "interval_hours": 6,
        "sync_days": 7,
    }

    responses = [
        client.put(
            "/api/v1/yazio/connection",
            headers={"X-CSRF-Token": csrf},
            json=payload,
        )
        for _ in range(3)
    ]

    assert [response.status_code for response in responses] == [200, 200, 429]
    assert responses[0].json()["available"] is True
    assert "Retry-After" in responses[-1].headers


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

    def fake_manual_sync(user_id, *, sync_days=None):
        nonlocal called_for
        called_for = user_id
        assert sync_days == 60
        return ImportSummary(
            status="completed",
            received=7,
            inserted=1,
            updated=2,
            skipped=4,
        )

    monkeypatch.setattr(yazio_api, "run_manual_yazio_sync", fake_manual_sync)
    response = client.post(
        "/api/v1/yazio/sync?days=60",
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 200
    assert response.json()["inserted"] == 1
    assert called_for == user.id


def test_initial_history_runs_in_bounded_chunks_before_regular_sync(
    db: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_key(monkeypatch)
    monkeypatch.setattr(yazio_sync, "YAZIO_HISTORY_DISCOVERY_START", date(2026, 7, 20))
    monkeypatch.setattr(yazio_sync, "MAX_YAZIO_RANGE_DAYS", 2)
    connection = configure_yazio_connection(
        user,
        "owner@example.com",
        "yazio-password",
    )
    calls: list[tuple[date, date, bool]] = []

    def fake_fetch(_email, _password, start_day, end_day, include_micronutrients):
        calls.append((start_day, end_day, include_micronutrients))
        return {
            end_day.isoformat(): {
                "daily_summary": {
                    "meals": {"dinner": {"nutrients": {"energy.energy": 1800}}}
                }
            }
        }

    assert run_scheduled_yazio_sync(
        connection.id,
        fetcher=fake_fetch,
        now=datetime(2026, 7, 23, 10, tzinfo=UTC),
    ) is not None
    assert run_scheduled_yazio_sync(
        connection.id,
        fetcher=fake_fetch,
        now=datetime(2026, 7, 23, 11, tzinfo=UTC),
    ) is not None

    assert calls == [
        (date(2026, 7, 22), date(2026, 7, 23), True),
        (date(2026, 7, 20), date(2026, 7, 21), True),
    ]
    db.expire_all()
    stored = db.get(YazioConnection, connection.id)
    assert stored is not None
    assert stored.initial_sync_state == "completed"
    assert stored.historical_sync_state == "completed"
    assert stored.next_sync_at is not None


def test_explicit_range_history_is_chunked_and_replaces_prior_values(
    db: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_key(monkeypatch)
    monkeypatch.setattr(yazio_sync, "MAX_YAZIO_RANGE_DAYS", 2)
    connection = configure_yazio_connection(
        user,
        "owner@example.com",
        "yazio-password",
    )
    stored_connection = db.get(YazioConnection, connection.id)
    assert stored_connection is not None
    stored_connection.initial_sync_state = "completed"
    stored_connection.historical_sync_state = "completed"
    db.commit()
    queued = enqueue_historical_yazio_sync(
        user.id,
        kind="range",
        start_day=date(2026, 7, 20),
        end_day=date(2026, 7, 23),
    )
    assert queued.id == connection.id
    calls: list[tuple[date, date]] = []

    def fake_fetch(_email, _password, start_day, end_day, _include_micronutrients):
        calls.append((start_day, end_day))
        return {
            end_day.isoformat(): {
                "daily_summary": {
                    "meals": {"dinner": {"nutrients": {"energy.energy": 1800}}}
                }
            }
        }

    assert run_scheduled_yazio_sync(connection.id, fetcher=fake_fetch) is not None
    assert run_scheduled_yazio_sync(connection.id, fetcher=fake_fetch) is not None
    assert calls == [
        (date(2026, 7, 22), date(2026, 7, 23)),
        (date(2026, 7, 20), date(2026, 7, 21)),
    ]
    db.expire_all()
    stored = db.get(YazioConnection, connection.id)
    assert stored is not None
    assert stored.historical_sync_kind == "range"
    assert stored.historical_sync_state == "completed"




def test_explicit_history_range_is_limited_to_366_days(
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_key(monkeypatch)
    configure_yazio_connection(user, "owner@example.com", "yazio-password")

    with pytest.raises(ValueError, match="länger als 366 Tage"):
        enqueue_historical_yazio_sync(
            user.id,
            kind="range",
            start_day=date(2026, 1, 1),
            end_day=date(2027, 1, 2),
        )

def test_history_range_at_minimum_date_completes_without_underflow(
    db: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_key(monkeypatch)
    connection = configure_yazio_connection(
        user, "owner@example.com", "yazio-password"
    )
    stored = db.get(YazioConnection, connection.id)
    assert stored is not None
    stored.initial_sync_state = "completed"
    stored.historical_sync_state = "completed"
    db.commit()
    enqueue_historical_yazio_sync(
        user.id,
        kind="range",
        start_day=date.min,
        end_day=date.min,
    )

    def fake_fetch(_email, _password, _start_day, end_day, _include_micronutrients):
        return {
            end_day.isoformat(): {
                "daily_summary": {
                    "meals": {"dinner": {"nutrients": {"energy.energy": 1800}}}
                }
            }
        }

    assert run_scheduled_yazio_sync(connection.id, fetcher=fake_fetch) is not None
    db.expire_all()
    stored = db.get(YazioConnection, connection.id)
    assert stored is not None
    assert stored.historical_sync_state == "completed"
    assert stored.historical_sync_cursor_date is None



def test_due_yazio_sync_reports_progress_after_each_connection(
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_key(monkeypatch)
    configure_yazio_connection(user, "owner@example.com", "yazio-password")
    progress: list[None] = []

    def fake_fetch(_email, _password, _start_day, end_day, _include_micronutrients):
        return {
            end_day.isoformat(): {
                "daily_summary": {
                    "meals": {"dinner": {"nutrients": {"energy.energy": 1800}}}
                }
            }
        }

    assert run_due_yazio_syncs(
        fetcher=fake_fetch,
        after_connection=lambda: progress.append(None),
    ) == (1, 1)
    assert progress == [None]


def test_history_queue_rejects_disabled_connection(
    client: TestClient,
    db: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_key(monkeypatch)
    connection = configure_yazio_connection(user, "owner@example.com", "yazio-password")
    stored = db.get(YazioConnection, connection.id)
    assert stored is not None
    stored.sync_enabled = False
    db.commit()

    with pytest.raises(YazioConnectionDisabled):
        enqueue_historical_yazio_sync(user.id, kind="full")

    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    response = client.post(
        "/api/v1/yazio/sync/history",
        headers={"X-CSRF-Token": login.json()["csrf_token"]},
    )
    assert response.status_code == 409

def test_history_api_requires_csrf_and_rejects_overlapping_jobs(
    client: TestClient,
    db: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_key(monkeypatch)
    connection = configure_yazio_connection(user, "owner@example.com", "yazio-password")
    stored = db.get(YazioConnection, connection.id)
    assert stored is not None
    stored.initial_sync_state = "completed"
    stored.historical_sync_state = "completed"
    db.commit()
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    csrf = login.json()["csrf_token"]

    assert client.post("/api/v1/yazio/sync/history").status_code == 403
    response = client.post(
        "/api/v1/yazio/sync/history/range",
        json={"from_date": "2026-07-20", "end_date": "2026-07-23"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    assert response.json()["historical_sync"] == {
        "kind": "range",
        "state": "pending",
        "start_date": "2026-07-20",
        "end_date": "2026-07-23",
        "started_at": None,
        "completed_at": None,
        "last_error": None,
    }
    response = client.post(
        "/api/v1/yazio/sync/history",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 429
    assert "Retry-After" in response.headers
