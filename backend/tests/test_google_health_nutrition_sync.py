from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.google_health.client import (
    NutritionLog,
    NutritionLogDataPoint,
    NutritionLogInterval,
    NutritionLogPage,
    NutritionQuantity,
)
from app.google_health.constants import GOOGLE_HEALTH_SCOPE
from app.google_health.errors import GoogleHealthProviderUnavailableError
from app.models import GoogleHealthConnection, User
from app.nutrition.models import (
    NutritionConsumptionEvent,
    NutritionExternalIdentity,
    NutritionExternalIdentityLink,
    NutritionFieldObservation,
    NutritionFoodProfile,
    NutritionFoodSnapshot,
    NutritionIngestionRun,
    NutritionProvenance,
    NutritionServingObservation,
    NutritionSourceObservation,
    NutritionSourceTombstone,
)
from app.services.google_health_nutrition_ingestion import ingest_google_health_nutrition_logs
from app.services.google_health_nutrition_sync import (
    GoogleHealthNutritionSyncError,
    GoogleHealthNutritionSyncService,
)

DAY = date(2026, 9, 1)
NEXT_DAY = DAY + timedelta(days=1)
PHYSICAL_START = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
PHYSICAL_END = datetime(2026, 9, 1, 10, 30, tzinfo=UTC)


@dataclass
class FakePagedClient:
    pages: dict[str | None, NutritionLogPage]
    db: Session
    events: list[str]
    error: Exception | None = None
    check_db_idle: bool = True
    page_tokens: list[str | None] = field(default_factory=list)

    def get_nutrition_log_page(
        self,
        *,
        page_size: int,
        page_token: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        civil_start_time: date | datetime | None = None,
        civil_end_time: date | datetime | None = None,
    ) -> NutritionLogPage:
        if self.check_db_idle:
            assert not self.db.in_transaction()
        del page_size, start_time, end_time, civil_start_time, civil_end_time
        self.page_tokens.append(page_token)
        self.events.append(f"read:{page_token}")
        if self.error is not None:
            raise self.error
        return self.pages[page_token]


@dataclass
class SyncHarness:
    db: Session
    pages: dict[str | None, NutritionLogPage]
    events: list[str] = field(default_factory=list)
    clients: list[FakePagedClient] = field(default_factory=list)
    decrypted: list[bytes] = field(default_factory=list)
    credentials: list[str] = field(default_factory=list)
    remote_error: Exception | None = None

    def service(
        self,
        *,
        session_factory: Callable[[], Session] | None = None,
        adapter: Callable[..., Any] | None = None,
    ) -> GoogleHealthNutritionSyncService:
        def default_session_factory() -> Session:
            return self.db

        def decrypt_refresh_token(value: bytes) -> str:
            self.events.append("decrypt")
            self.decrypted.append(value)
            return "refresh-token-only-in-memory"

        def credentials_factory(refresh_token: str) -> object:
            self.events.append("credentials")
            self.credentials.append(refresh_token)
            return {"refresh_token": refresh_token}

        def client_factory(credentials: object) -> FakePagedClient:
            if session_factory is None:
                assert not self.db.in_transaction()
            assert credentials == {"refresh_token": "refresh-token-only-in-memory"}
            self.events.append("client")
            client = FakePagedClient(
                self.pages,
                self.db,
                self.events,
                error=self.remote_error,
                check_db_idle=session_factory is None,
            )
            self.clients.append(client)
            return client

        def default_adapter(session: Session, **kwargs: Any):
            self.events.append("adapter")
            return ingest_google_health_nutrition_logs(session, **kwargs)

        return GoogleHealthNutritionSyncService(
            session_factory=session_factory or default_session_factory,
            client_factory=client_factory,
            credentials_factory=credentials_factory,
            decrypt_refresh_token=decrypt_refresh_token,
            adapter=adapter or default_adapter,
        )


def _point(
    name: str, *, food: str = "users/me/dataTypes/food/dataPoints/food-1"
) -> NutritionLogDataPoint:
    return NutritionLogDataPoint(
        name=name,
        nutrition_log=NutritionLog(
            interval=NutritionLogInterval(
                start_time=PHYSICAL_START,
                end_time=PHYSICAL_END,
                start_utc_offset="+00:00",
                end_utc_offset="+00:00",
                civil_start_time=datetime.combine(DAY, datetime.min.time()),
                civil_end_time=datetime.combine(DAY, datetime.min.time()) + timedelta(minutes=30),
            ),
            energy=NutritionQuantity(987654.321, "kcal"),
            food=food,
            food_display_name="Secret Food Name",
        ),
    )


def _page(
    points: tuple[NutritionLogDataPoint, ...],
    *,
    page_token: str | None,
    next_page_token: str | None,
) -> NutritionLogPage:
    return NutritionLogPage(
        data_points=points,
        next_page_token=next_page_token,
        page_size=100,
        page_token=page_token,
        start_time=None,
        end_time=None,
        civil_start_time=DAY,
        civil_end_time=NEXT_DAY,
    )


def _connection(
    db: Session,
    user: User,
    *,
    state: str = "active",
    granted_scopes: list[str] | None = None,
) -> GoogleHealthConnection:
    connection = GoogleHealthConnection(
        user_id=user.id,
        encrypted_refresh_token=b"encrypted-refresh-token",
        granted_scopes=[GOOGLE_HEALTH_SCOPE] if granted_scopes is None else granted_scopes,
        state=state,
    )
    db.add(connection)
    db.commit()
    db.refresh(connection)
    return connection


DOMAIN_MODELS = (
    NutritionIngestionRun,
    NutritionSourceObservation,
    NutritionConsumptionEvent,
    NutritionFieldObservation,
    NutritionServingObservation,
    NutritionProvenance,
    NutritionExternalIdentity,
    NutritionExternalIdentityLink,
    NutritionFoodProfile,
    NutritionFoodSnapshot,
    NutritionSourceTombstone,
)


def _domain_counts(db: Session) -> dict[type[Any], int]:
    return {
        model: db.scalar(select(func.count()).select_from(model)) or 0 for model in DOMAIN_MODELS
    }


def _sync_result_run(result: object) -> NutritionIngestionRun:
    run = getattr(result, "run", result)
    assert isinstance(run, NutritionIngestionRun)
    return run


def test_sync_fetches_all_pages_before_one_adapter_write_and_preserves_source_instance(
    db: Session, user: User
) -> None:
    connection = _connection(db, user)
    events: list[str] = []
    pages = {
        None: _page((_point("google-log-page-1"),), page_token=None, next_page_token="page-2"),
        "page-2": _page(
            (_point("google-log-page-2", food="users/me/dataTypes/food/dataPoints/food-2"),),
            page_token="page-2",
            next_page_token=None,
        ),
    }
    harness = SyncHarness(db, pages, events)

    result = harness.service().sync(
        user_id=user.id,
        requested_start=DAY,
        requested_end=DAY,
    )

    assert harness.clients[0].page_tokens == [None, "page-2"]
    assert events.index("adapter") > events.index("read:page-2")
    assert events.count("adapter") == 1
    run = _sync_result_run(result)
    assert run.source_instance_id == connection.id
    assert run.provider_key == "google_health"
    assert run.requested_start_date == DAY
    assert run.requested_end_date == DAY
    assert run.covered_start_date == DAY
    assert run.covered_end_date == DAY
    assert run.status == "completed"


def test_sync_distinct_write_session_commits_for_separate_reader(db: Session, user: User) -> None:
    _connection(db, user)
    db.rollback()
    harness = SyncHarness(
        db,
        {
            None: _page(
                (_point("google-log-distinct-session"),), page_token=None, next_page_token=None
            )
        },
    )
    sessions: list[Session] = []

    def session_factory() -> Session:
        session = SessionLocal()
        sessions.append(session)
        return session

    result = harness.service(session_factory=session_factory).sync(
        user_id=user.id,
        requested_start=DAY,
        requested_end=DAY,
    )

    assert result.persisted_count == 1
    assert len(sessions) == 2
    with SessionLocal() as observer:
        assert observer.scalar(select(func.count()).select_from(NutritionSourceObservation)) == 1
    assert all(not session.in_transaction() for session in sessions)


def test_sync_distinct_write_session_rolls_back_adapter_failure(db: Session, user: User) -> None:
    _connection(db, user)
    db.rollback()
    harness = SyncHarness(
        db,
        {None: _page((_point("google-log-rollback"),), page_token=None, next_page_token=None)},
    )
    sessions: list[Session] = []

    def session_factory() -> Session:
        session = SessionLocal()
        sessions.append(session)
        return session

    def failing_adapter(session: Session, **kwargs: Any) -> NutritionIngestionRun:
        run = ingest_google_health_nutrition_logs(session, **kwargs)
        raise RuntimeError(f"failure after run {run.id}")

    with pytest.raises(GoogleHealthNutritionSyncError) as raised:
        harness.service(
            session_factory=session_factory,
            adapter=failing_adapter,
        ).sync(
            user_id=user.id,
            requested_start=DAY,
            requested_end=DAY,
        )

    assert raised.value.code == "persistence_error"
    assert len(sessions) == 2
    with SessionLocal() as observer:
        assert _domain_counts(observer) == {model: 0 for model in DOMAIN_MODELS}
    assert all(not session.in_transaction() for session in sessions)


def test_sync_commit_failure_rolls_back_and_closes_distinct_write_session(
    db: Session, user: User
) -> None:
    _connection(db, user)
    db.rollback()
    harness = SyncHarness(
        db,
        {
            None: _page(
                (_point("google-log-commit-failure"),), page_token=None, next_page_token=None
            )
        },
    )
    sessions: list[Session] = []

    class CommitRaisingSession:
        def __init__(self, inner: Session) -> None:
            self.inner = inner
            self.commit_called = False
            self.rollback_called = False
            self.close_called = False

        def __getattr__(self, name: str) -> Any:
            return getattr(self.inner, name)

        def commit(self) -> None:
            self.commit_called = True
            raise RuntimeError("synthetic commit failure")

        def rollback(self) -> None:
            self.rollback_called = True
            self.inner.rollback()

        def close(self) -> None:
            self.close_called = True
            self.inner.close()

    write_session: CommitRaisingSession | None = None

    def session_factory() -> Session:
        nonlocal write_session
        session = SessionLocal()
        sessions.append(session)
        if len(sessions) == 1:
            return session
        write_session = CommitRaisingSession(session)
        return write_session  # type: ignore[return-value]

    with pytest.raises(GoogleHealthNutritionSyncError) as raised:
        harness.service(session_factory=session_factory).sync(
            user_id=user.id,
            requested_start=DAY,
            requested_end=DAY,
        )

    assert raised.value.code == "persistence_error"
    assert len(sessions) == 2
    assert write_session is not None
    assert write_session.inner is not sessions[0]
    assert write_session.commit_called is True
    assert write_session.rollback_called is True
    assert write_session.close_called is True
    with SessionLocal() as observer:
        assert _domain_counts(observer) == {model: 0 for model in DOMAIN_MODELS}


def test_sync_rejects_repeated_page_token_without_writing_domain_rows(
    db: Session, user: User
) -> None:
    _connection(db, user)
    pages = {
        None: _page(
            (_point("google-log-page-1"),), page_token=None, next_page_token="repeat-token"
        ),
        "repeat-token": _page(
            (_point("google-log-page-2"),),
            page_token="repeat-token",
            next_page_token="repeat-token",
        ),
    }
    harness = SyncHarness(db, pages)

    with pytest.raises(GoogleHealthNutritionSyncError) as raised:
        harness.service().sync(user_id=user.id, requested_start=DAY, requested_end=DAY)

    assert raised.value.code == "pagination_error"
    assert "repeat-token" not in str(raised.value)
    assert _domain_counts(db) == {model: 0 for model in DOMAIN_MODELS}
    assert harness.clients[0].page_tokens == [None, "repeat-token"]
    assert "adapter" not in harness.events


@pytest.mark.parametrize(
    ("state", "granted_scopes", "expected_code"),
    [
        ("reauth_required", [GOOGLE_HEALTH_SCOPE], "connection_inactive"),
        ("active", ["https://www.googleapis.com/auth/other.readonly"], "scope_missing"),
    ],
)
def test_sync_requires_active_connection_and_exact_nutrition_scope(
    db: Session,
    user: User,
    state: str,
    granted_scopes: list[str],
    expected_code: str,
) -> None:
    _connection(db, user, state=state, granted_scopes=granted_scopes)
    harness = SyncHarness(
        db,
        {None: _page((_point("google-log"),), page_token=None, next_page_token=None)},
    )

    with pytest.raises(GoogleHealthNutritionSyncError) as raised:
        harness.service().sync(user_id=user.id, requested_start=DAY, requested_end=DAY)

    assert raised.value.code == expected_code
    assert _domain_counts(db) == {model: 0 for model in DOMAIN_MODELS}
    assert not harness.clients
    assert "adapter" not in harness.events


def test_sync_requires_connection_owned_by_requested_user_without_writes(
    db: Session, user: User
) -> None:
    other_user = User(username="other", password_hash="not-used", timezone="Europe/Berlin")
    db.add(other_user)
    db.commit()
    _connection(db, other_user)
    harness = SyncHarness(
        db,
        {None: _page((_point("google-log"),), page_token=None, next_page_token=None)},
    )

    with pytest.raises(GoogleHealthNutritionSyncError) as raised:
        harness.service().sync(user_id=user.id, requested_start=DAY, requested_end=DAY)

    assert raised.value.code == "connection_not_configured"
    assert _domain_counts(db) == {model: 0 for model in DOMAIN_MODELS}
    assert not harness.clients
    assert "adapter" not in harness.events


def test_sync_remote_error_is_safe_and_leaves_no_partial_domain_rows(
    db: Session, user: User
) -> None:
    _connection(db, user)
    provider_error = GoogleHealthProviderUnavailableError(
        "provider payload includes Secret Food Name and token=secret-token-123"
    )
    harness = SyncHarness(
        db,
        {None: _page((_point("google-log"),), page_token=None, next_page_token=None)},
        remote_error=provider_error,
    )

    with pytest.raises(GoogleHealthNutritionSyncError) as raised:
        harness.service().sync(user_id=user.id, requested_start=DAY, requested_end=DAY)

    assert raised.value.code == "provider_error"
    safe_error = str(raised.value)
    assert "Secret Food Name" not in safe_error
    assert "secret-token-123" not in safe_error
    assert "987654.321" not in safe_error
    assert _domain_counts(db) == {model: 0 for model in DOMAIN_MODELS}
    assert "adapter" not in harness.events


def test_sync_result_summary_contains_counts_and_coverage_only(db: Session, user: User) -> None:
    connection = _connection(db, user)
    pages = {
        None: _page((_point("google-log-secret"),), page_token=None, next_page_token=None),
    }
    harness = SyncHarness(db, pages)

    result = harness.service().sync(user_id=user.id, requested_start=DAY, requested_end=DAY)
    rendered = repr(result)

    assert result.status == "completed"
    assert result.fetched_count == 1
    assert result.persisted_count == 1
    assert result.requested_start == DAY
    assert result.requested_end == DAY
    assert result.covered_start == DAY
    assert result.covered_end == DAY
    assert _sync_result_run(result).source_instance_id == connection.id
    for secret in ("google-log-secret", "Secret Food Name", "secret-token-123", "987654.321"):
        assert secret not in rendered


def test_repeating_sync_is_idempotent_for_non_run_domain_rows(db: Session, user: User) -> None:
    connection = _connection(db, user)
    pages = {None: _page((_point("google-log-stable"),), page_token=None, next_page_token=None)}
    harness = SyncHarness(db, pages)
    service = harness.service()

    first = service.sync(user_id=user.id, requested_start=DAY, requested_end=DAY)
    db.commit()
    counts_after_first = _domain_counts(db)
    second = service.sync(user_id=user.id, requested_start=DAY, requested_end=DAY)
    db.commit()
    counts_after_second = _domain_counts(db)

    assert _sync_result_run(first).source_instance_id == connection.id
    assert _sync_result_run(second).source_instance_id == connection.id
    assert (
        counts_after_second[NutritionIngestionRun] == counts_after_first[NutritionIngestionRun] + 1
    )
    for model in DOMAIN_MODELS:
        if model is not NutritionIngestionRun:
            assert counts_after_second[model] == counts_after_first[model]
    assert second.fetched_count == 1
    assert second.persisted_count == 0
