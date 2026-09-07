from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import User, YazioConnection
from app.services import yazio_sync

NOW = datetime(2026, 7, 22, 21, 30, tzinfo=UTC)


def _connection(db: Session, user: User, **values: object) -> YazioConnection:
    connection = YazioConnection(
        user_id=user.id,
        encrypted_email=b"encrypted-email",
        encrypted_password=b"encrypted-password",
        source_identifier=f"yazio:{user.id}",
        **values,
    )
    db.add(connection)
    db.commit()
    db.refresh(connection)
    return connection


def _stored(db: Session) -> YazioConnection:
    connection = db.scalar(select(YazioConnection))
    assert connection is not None
    return connection


def test_daily_trigger_does_nothing_when_yazio_is_disabled(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = _connection(db, user)
    monkeypatch.setattr(settings, "yazio_enabled", False)

    assert yazio_sync.queue_daily_yazio_sync_if_due(db, user, now=NOW) is False

    db.refresh(connection)
    assert connection.last_daily_sync_trigger_date is None
    assert connection.next_sync_at is None


@pytest.mark.parametrize(
    "connection_values",
    [
        {"sync_enabled": False},
        {"historical_sync_state": "pending"},
        {"historical_sync_state": "running"},
    ],
)
def test_daily_trigger_skips_ineligible_connections_without_marking_the_day(
    db: Session,
    user: User,
    connection_values: dict[str, object],
) -> None:
    connection = _connection(db, user, **connection_values)

    assert yazio_sync.queue_daily_yazio_sync_if_due(db, user, now=NOW) is False

    db.refresh(connection)
    assert connection.last_daily_sync_trigger_date is None
    assert connection.next_sync_at is None


def test_daily_trigger_does_nothing_without_a_connection(
    db: Session, user: User
) -> None:
    assert yazio_sync.queue_daily_yazio_sync_if_due(db, user, now=NOW) is False


def test_first_local_day_trigger_sets_date_and_queues_due_work(
    db: Session, user: User
) -> None:
    connection = _connection(db, user)

    assert yazio_sync.queue_daily_yazio_sync_if_due(db, user, now=NOW) is True

    db.refresh(connection)
    assert connection.last_daily_sync_trigger_date == date(2026, 7, 22)
    assert connection.next_sync_at == NOW.replace(tzinfo=None)


def test_second_trigger_on_same_local_day_is_idempotent(
    db: Session, user: User
) -> None:
    connection = _connection(db, user, next_sync_at=NOW - timedelta(minutes=1))
    assert yazio_sync.queue_daily_yazio_sync_if_due(db, user, now=NOW) is True
    db.refresh(connection)
    original_next_sync_at = connection.next_sync_at

    later = NOW + timedelta(minutes=5)
    assert yazio_sync.queue_daily_yazio_sync_if_due(db, user, now=later) is False

    db.refresh(connection)
    assert connection.last_daily_sync_trigger_date == date(2026, 7, 22)
    assert connection.next_sync_at == original_next_sync_at


def test_recent_success_marks_day_without_queuing_another_sync(
    db: Session, user: User
) -> None:
    recent_success = NOW - timedelta(minutes=29)
    original_next_sync_at = NOW + timedelta(hours=2)
    connection = _connection(
        db,
        user,
        last_success_at=recent_success,
        next_sync_at=original_next_sync_at,
    )

    assert yazio_sync.queue_daily_yazio_sync_if_due(db, user, now=NOW) is True

    db.refresh(connection)
    assert connection.last_daily_sync_trigger_date == date(2026, 7, 22)
    assert connection.next_sync_at == original_next_sync_at.replace(tzinfo=None)


def test_stale_or_missing_success_queues_sync(
    db: Session, user: User
) -> None:
    stale = _connection(
        db,
        user,
        last_success_at=NOW - timedelta(minutes=30),
        next_sync_at=NOW + timedelta(hours=1),
    )
    assert yazio_sync.queue_daily_yazio_sync_if_due(db, user, now=NOW) is True
    db.refresh(stale)
    assert stale.next_sync_at == NOW.replace(tzinfo=None)

    db.delete(stale)
    db.commit()
    missing = _connection(db, user, next_sync_at=None)
    assert yazio_sync.queue_daily_yazio_sync_if_due(db, user, now=NOW) is True
    db.refresh(missing)
    assert missing.last_success_at is None
    assert missing.next_sync_at == NOW.replace(tzinfo=None)


def test_overdue_next_sync_is_never_moved_back(
    db: Session, user: User
) -> None:
    overdue = NOW - timedelta(minutes=15)
    connection = _connection(db, user, next_sync_at=overdue)

    assert yazio_sync.queue_daily_yazio_sync_if_due(db, user, now=NOW) is True

    db.refresh(connection)
    assert connection.next_sync_at == overdue.replace(tzinfo=None)


def test_daily_trigger_uses_user_timezone_at_utc_midnight_boundary(
    db: Session, user: User
) -> None:
    before_midnight = datetime(2026, 7, 22, 21, 59, tzinfo=UTC)
    after_midnight = datetime(2026, 7, 22, 22, 1, tzinfo=UTC)
    connection = _connection(db, user)

    assert yazio_sync.queue_daily_yazio_sync_if_due(db, user, now=before_midnight) is True
    db.refresh(connection)
    assert connection.last_daily_sync_trigger_date == date(2026, 7, 22)

    assert yazio_sync.queue_daily_yazio_sync_if_due(db, user, now=after_midnight) is True
    db.refresh(connection)
    assert connection.last_daily_sync_trigger_date == date(2026, 7, 23)


def test_auth_entry_points_queue_without_provider_access(
    client: TestClient,
    db: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _connection(db, user)
    network_calls: list[str] = []

    def fail_if_called(*_args: object, **_kwargs: object) -> None:
        network_calls.append("provider")
        raise AssertionError("YAZIO provider access is not allowed in auth requests")

    monkeypatch.setattr(yazio_sync, "fetch_yazio_payload_transport", fail_if_called)
    monkeypatch.setattr(yazio_sync, "validate_yazio_credentials_transport", fail_if_called)

    login = client.post(
        "/api/v1/auth/login",
        json={"username": user.username, "password": "correct-horse-battery-staple"},
    )
    assert login.status_code == 200

    db.expire_all()
    connection = _stored(db)
    assert connection.last_daily_sync_trigger_date == datetime.now(UTC).astimezone(
        ZoneInfo(user.timezone)
    ).date()

    connection.last_daily_sync_trigger_date = None
    connection.next_sync_at = None
    db.commit()
    for _ in range(2):
        assert client.get("/api/v1/auth/csrf").status_code == 200

    db.expire_all()
    connection = _stored(db)
    assert connection.last_daily_sync_trigger_date is None

    assert client.get("/api/v1/auth/me").status_code == 200
    db.expire_all()
    connection = _stored(db)
    assert connection.last_daily_sync_trigger_date == datetime.now(UTC).astimezone(
        ZoneInfo(user.timezone)
    ).date()
    assert connection.next_sync_at is not None
    assert network_calls == []


def test_daily_trigger_database_error_does_not_break_auth(
    client: TestClient,
    db: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _connection(db, user)

    def fail_queue(*_args: object, **_kwargs: object) -> bool:
        raise RuntimeError("synthetic database failure")

    monkeypatch.setattr(yazio_sync, "queue_daily_yazio_sync_if_due", fail_queue)

    login = client.post(
        "/api/v1/auth/login",
        json={"username": user.username, "password": "correct-horse-battery-staple"},
    )
    assert login.status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 200
    assert client.get("/api/v1/auth/csrf").status_code == 200
    assert db.scalar(select(User).where(User.id == user.id)) is not None


def test_best_effort_trigger_rolls_back_and_closes_its_own_session(
    db: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TrackingSession:
        rolled_back = False
        closed = False

        def __enter__(self) -> TrackingSession:
            return self

        def __exit__(self, *_args: object) -> None:
            self.closed = True

        def get(self, model: type[User], user_id: object) -> User | None:
            assert model is User
            assert user_id == user.id
            return user

        def rollback(self) -> None:
            self.rolled_back = True

    trigger_session = TrackingSession()

    def fail_queue(*_args: object, **_kwargs: object) -> bool:
        raise RuntimeError("synthetic database failure")

    monkeypatch.setattr(yazio_sync, "SessionLocal", lambda: trigger_session)
    monkeypatch.setattr(yazio_sync, "queue_daily_yazio_sync_if_due", fail_queue)

    yazio_sync.queue_daily_yazio_sync_best_effort(user.id, now=NOW)

    assert trigger_session.rolled_back is True
    assert trigger_session.closed is True
    assert db.scalar(select(User).where(User.id == user.id)) is not None
