from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session, sessionmaker

from app.database import SessionLocal
from app.models import GoogleHealthConnection, User, YazioConnection
from app.nutrition.models import (
    NutritionDailyProjection,
    NutritionIngestionRun,
    NutritionProjectionHead,
    NutritionSourceObservation,
)
from app.nutrition.projection import lifecycle
from app.nutrition.projection.lifecycle import NutritionProjectionLifecycleError
from app.services import yazio_projection_retry as retry
from app.source_priority.application import create_policy_with_rules
from app.source_priority.contracts import PriorityRuleSpec
from app.source_priority.models import SourcePriorityPolicy, SourcePriorityRule

DAY = date(2026, 9, 1)
NOW = datetime(2026, 10, 21, 12, 30, tzinfo=UTC)


@pytest.fixture(autouse=True)
def retry_clock(monkeypatch):
    class Clock:
        @staticmethod
        def now(tz):
            assert tz is UTC
            return NOW

    monkeypatch.setattr(retry, "datetime", Clock)


@pytest.fixture
def connection(db, user):
    row = YazioConnection(
        user_id=user.id, encrypted_email=b"synthetic-email",
        encrypted_password=b"synthetic-password", source_identifier="projection-retry-test",
    )
    db.add(row)
    db.flush()
    return row


def _run(db, user_id, source_id, *, day=DAY, run_id=None, pending=True,
         provider="yazio", connector="sdk-v22", created_at=NOW):
    row = NutritionIngestionRun(
        id=run_id or uuid4(), user_id=user_id, source_instance_id=source_id,
        provider_key=provider, connector_variant=connector, status="completed",
        coverage_state="complete", projection_pending=pending,
        requested_start_date=day, requested_end_date=day, created_at=created_at,
    )
    db.add(row)
    db.flush()
    return row


def _observation(db, run, *, day=None):
    key = f"retry-{uuid4()}"
    row = NutritionSourceObservation(
        user_id=run.user_id, ingestion_run_id=run.id,
        provider_key=run.provider_key, source_instance_id=run.source_instance_id,
        connector_variant=run.connector_variant, observation_kind="consumption_event",
        source_namespace="yazio.simple_product", source_record_id=key,
        source_revision=1, observation_fingerprint=sha256(key.encode()).hexdigest(),
        local_date=day or run.requested_start_date, presence_state="supplied",
        coverage_state="complete", resolution_state="resolved", lineage_state="confirmed",
    )
    db.add(row)
    db.flush()
    return row


def _policy(db, user_id, *, version=1, effective_from=NOW - timedelta(days=1), provider="yazio"):
    return create_policy_with_rules(
        db, user_id, version, effective_from,
        (PriorityRuleSpec("nutrition", None, provider, 1),),
    )


def _pending(run_id):
    with SessionLocal() as db:
        return db.get(NutritionIngestionRun, run_id).projection_pending


def _drain(user_id, source_id, **kwargs):
    return retry.drain_pending_yazio_projections(
        session_factory=kwargs.get("session_factory", SessionLocal),
        user_id=user_id, source_instance_id=source_id,
    )


def test_pending_selection_is_scoped_and_deterministically_oldest_first(db, user, connection, monkeypatch):
    other = User(username=f"retry-other-{uuid4().hex}", password_hash="synthetic", timezone="UTC")
    db.add(other)
    db.flush()
    earlier = _run(db, user.id, connection.id, created_at=NOW - timedelta(days=1))
    second = _run(db, user.id, connection.id, run_id=UUID(int=1))
    third = _run(db, user.id, connection.id, run_id=UUID(int=2))
    excluded = [
        _run(db, other.id, connection.id),
        _run(db, user.id, uuid4()),
        _run(db, user.id, connection.id, provider="google_health"),
        _run(db, user.id, connection.id, connector="legacy-v15"),
        _run(db, user.id, connection.id, pending=False),
    ]
    db.commit()
    seen = []
    original = retry.rebuild_yazio_projection_run

    def rebuild(**kwargs):
        # This is the real replay/ack boundary, not a replacement for replay.
        assert _pending(kwargs["ingestion_run_id"]) is True
        result = original(**kwargs)
        assert _pending(kwargs["ingestion_run_id"]) is True
        seen.append(kwargs["ingestion_run_id"])
        return result

    monkeypatch.setattr(retry, "rebuild_yazio_projection_run", rebuild)
    result = _drain(user.id, connection.id)
    assert result == (earlier.id, second.id, third.id)
    assert seen == list(result)
    assert all(_pending(run_id) is False for run_id in result)
    assert [_pending(run.id) for run in excluded] == [True, True, True, True, False]


def test_rebuild_current_run_stays_pending_and_does_not_touch_connection(db, user, connection):
    run = _run(db, user.id, connection.id)
    _observation(db, run)
    connection.last_error = "retry-sentinel"
    connection.historical_sync_cursor_date = DAY
    db.commit()
    result = retry.rebuild_yazio_projection_run(
        session_factory=SessionLocal, user_id=user.id, source_instance_id=connection.id,
        ingestion_run_id=run.id, policy_at=NOW,
    )
    assert result.affected_dates == (DAY,)
    assert result.created_dates == (DAY,)
    assert _pending(run.id) is True
    db.expire_all()
    assert connection.last_error == "retry-sentinel"
    assert connection.last_success_at is None
    assert connection.historical_sync_cursor_date == DAY


@pytest.mark.parametrize("mismatch", [
    "user", "source", "provider", "connector", "missing", "missing_bounds", "reversed_bounds",
])
def test_rebuild_and_ack_reject_nonmatching_sdk_run(db, user, connection, mismatch):
    run = _run(db, user.id, connection.id)
    kwargs = dict(user_id=user.id, source_instance_id=connection.id, ingestion_run_id=run.id)
    if mismatch == "user":
        kwargs["user_id"] = uuid4()
    elif mismatch == "source":
        kwargs["source_instance_id"] = uuid4()
    elif mismatch == "provider":
        run.provider_key = "google_health"
    elif mismatch == "connector":
        run.connector_variant = "legacy-v15"
    elif mismatch == "missing_bounds":
        run.requested_start_date = None
    elif mismatch == "reversed_bounds":
        run.requested_end_date = DAY - timedelta(days=1)
    else:
        kwargs["ingestion_run_id"] = uuid4()
    db.commit()
    with pytest.raises(NutritionProjectionLifecycleError):
        retry.rebuild_yazio_projection_run(session_factory=SessionLocal, policy_at=NOW, **kwargs)
    with pytest.raises(NutritionProjectionLifecycleError):
        retry.acknowledge_yazio_projection_run(db, **kwargs)
    db.rollback()
    assert _pending(run.id) is True
    assert db.query(SourcePriorityPolicy).count() == 0


def test_ack_only_changes_pending_and_never_commits(db, user, connection):
    run = _run(db, user.id, connection.id)
    db.commit()
    retry.acknowledge_yazio_projection_run(
        db, user_id=user.id, source_instance_id=connection.id, ingestion_run_id=run.id,
    )
    db.flush()
    assert run.projection_pending is False
    assert run.status == "completed"
    assert db.in_transaction()
    db.rollback()
    assert _pending(run.id) is True


def test_partial_failure_preserves_pending_then_restart_reuses_successful_day(db, user, connection, monkeypatch):
    _policy(db, user.id)
    run = _run(db, user.id, connection.id)
    run.requested_end_date = DAY + timedelta(days=1)
    _observation(db, run)
    _observation(db, run, day=DAY + timedelta(days=1))
    user_id, source_id, run_id = user.id, connection.id, run.id
    db.commit()
    real_rebuild = lifecycle.rebuild_nutrition_day
    failed = False
    completed = []

    def fail_second_once(session, **kwargs):
        nonlocal failed
        if kwargs["local_date"] == DAY + timedelta(days=1) and not failed:
            failed = True
            raise RuntimeError("synthetic projection failure")
        result = real_rebuild(session, **kwargs)
        completed.append(result.status.value)
        return result

    monkeypatch.setattr(lifecycle, "rebuild_nutrition_day", fail_second_once)
    fetched = []

    def downstream_sync():
        _drain(user_id, source_id)
        fetched.append("new-fetch")

    with pytest.raises(NutritionProjectionLifecycleError):
        downstream_sync()
    assert fetched == []
    assert _pending(run_id) is True
    with SessionLocal() as check:
        first_head_id = check.get(NutritionProjectionHead, (user_id, DAY)).current_projection_id
        assert check.query(NutritionDailyProjection).count() == 1

    # A later invocation has a shifted requested window and fresh sessions. It
    # must still recover the older durable run before beginning its own fetch.
    with SessionLocal() as restarted:
        newer = _run(restarted, user_id, source_id, day=DAY + timedelta(days=20), created_at=NOW + timedelta(days=1))
        _observation(restarted, newer)
        newer_id = newer.id
        restarted.commit()
    assert _drain(user_id, source_id) == (run_id, newer_id)
    assert completed == ["created", "unchanged", "created", "created"]
    assert not _pending(run_id)
    assert not _pending(newer_id)
    with SessionLocal() as check:
        assert check.get(NutritionProjectionHead, (user_id, DAY)).current_projection_id == first_head_id
        assert check.query(NutritionDailyProjection).count() == 3


def test_ack_failure_rolls_back_and_halts_later_pending_runs(db, user, connection, monkeypatch):
    first = _run(db, user.id, connection.id, created_at=NOW - timedelta(days=1))
    later = _run(db, user.id, connection.id)
    _observation(db, first)
    _observation(db, later, day=DAY + timedelta(days=1))
    db.commit()
    original = retry.acknowledge_yazio_projection_run

    def failing_ack(db, **kwargs):
        original(db, **kwargs)
        db.flush()
        raise RuntimeError("synthetic acknowledgement failure")

    monkeypatch.setattr(retry, "acknowledge_yazio_projection_run", failing_ack)
    with pytest.raises(RuntimeError, match="acknowledgement failure"):
        _drain(user.id, connection.id)
    assert _pending(first.id)
    assert _pending(later.id)
    with SessionLocal() as check:
        assert check.get(NutritionProjectionHead, (user.id, DAY)) is not None
        assert check.get(NutritionProjectionHead, (user.id, DAY + timedelta(days=1))) is None


def test_each_successful_ack_is_committed_before_later_run_fails(db, user, connection, monkeypatch):
    first = _run(db, user.id, connection.id, created_at=NOW - timedelta(days=1))
    later = _run(db, user.id, connection.id, day=DAY + timedelta(days=1))
    _observation(db, first)
    _observation(db, later)
    db.commit()
    original = lifecycle.rebuild_nutrition_day

    def fail_later(db, **kwargs):
        if kwargs["local_date"] != DAY:
            assert _pending(first.id) is False
            raise RuntimeError("later day failed")
        return original(db, **kwargs)

    monkeypatch.setattr(lifecycle, "rebuild_nutrition_day", fail_later)
    with pytest.raises(NutritionProjectionLifecycleError):
        _drain(user.id, connection.id)
    assert _pending(first.id) is False
    assert _pending(later.id) is True


def test_drain_uses_one_aware_now_and_effective_not_future_policy(db, user, connection, monkeypatch):
    effective = _policy(db, user.id)
    future = _policy(db, user.id, version=2, effective_from=NOW + timedelta(days=10), provider="google_health")
    first = _run(db, user.id, connection.id, created_at=NOW - timedelta(days=1))
    second = _run(db, user.id, connection.id, day=DAY + timedelta(days=1))
    _observation(db, first)
    _observation(db, second)
    db.commit()
    clock_calls = []

    class Clock:
        @staticmethod
        def now(tz):
            clock_calls.append(tz)
            return NOW

    monkeypatch.setattr(retry, "datetime", Clock)
    timestamps = []
    real_bootstrap = retry.bootstrap_nutrition_priority
    real_rebuild = lifecycle.rebuild_nutrition_day

    def bootstrap(**kwargs):
        timestamps.append(kwargs["effective_from"])
        return real_bootstrap(**kwargs)

    def rebuild(db, **kwargs):
        timestamps.append(kwargs["policy_at"])
        return real_rebuild(db, **kwargs)

    monkeypatch.setattr(retry, "bootstrap_nutrition_priority", bootstrap)
    monkeypatch.setattr(lifecycle, "rebuild_nutrition_day", rebuild)
    assert _drain(user.id, connection.id) == (first.id, second.id)
    assert clock_calls == [UTC]
    assert len(timestamps) >= 3
    assert all(timestamp is NOW for timestamp in timestamps)
    with SessionLocal() as check:
        projections = check.scalars(select(NutritionDailyProjection)).all()
        assert {row.priority_policy_id for row in projections} == {effective.policy_id}
        assert check.query(SourcePriorityPolicy).count() == 2
        future_rule = check.scalar(select(SourcePriorityRule).where(SourcePriorityRule.policy_id == future.policy_id))
        assert future_rule.provider_key == "google_health"


def test_future_only_policy_is_not_selected_or_overwritten(db, user, connection):
    future = _policy(db, user.id, effective_from=NOW + timedelta(days=10))
    run = _run(db, user.id, connection.id)
    _observation(db, run)
    db.commit()
    result = retry.rebuild_yazio_projection_run(
        session_factory=SessionLocal, user_id=user.id, source_instance_id=connection.id,
        ingestion_run_id=run.id, policy_at=NOW,
    )
    assert result.policy_missing_dates == (DAY,)
    assert _pending(run.id)
    with SessionLocal() as check:
        assert check.query(NutritionDailyProjection).count() == 0
        assert check.query(SourcePriorityPolicy).one().id == future.policy_id


def test_selection_required_is_safe_without_creating_a_policy(db, user, connection):
    db.add(GoogleHealthConnection(
        client_id="synthetic-client", encrypted_client_secret=b"synthetic-secret",
        user_id=user.id, encrypted_refresh_token=b"synthetic-token", granted_scopes=[],
    ))
    run = _run(db, user.id, connection.id)
    _observation(db, run)
    db.commit()
    result = retry.rebuild_yazio_projection_run(
        session_factory=SessionLocal, user_id=user.id, source_instance_id=connection.id,
        ingestion_run_id=run.id, policy_at=NOW,
    )
    assert result.policy_missing_dates == (DAY,)
    assert db.query(SourcePriorityPolicy).count() == 0
    assert _pending(run.id)
    assert _drain(user.id, connection.id) == (run.id,)
    assert _pending(run.id) is False
    with SessionLocal() as check:
        assert check.query(SourcePriorityPolicy).count() == 0
        assert check.query(NutritionDailyProjection).count() == 0


def test_empty_drain_does_not_bootstrap_a_policy(db, user, connection):
    db.commit()
    assert _drain(user.id, connection.id) == ()
    assert db.query(SourcePriorityPolicy).count() == 0


def test_ack_commit_failure_rolls_back_pending_flag(db, user, connection):
    _policy(db, user.id)
    run = _run(db, user.id, connection.id)
    _observation(db, run)
    db.commit()

    class CommitFailureSession(Session):
        pass

    @event.listens_for(CommitFailureSession, "before_commit")
    def fail_ack_commit(session):
        if any(
            isinstance(row, NutritionIngestionRun) and row.projection_pending is False
            for row in session.dirty
        ):
            raise RuntimeError("synthetic commit failure")

    factory = sessionmaker(bind=db.get_bind(), class_=CommitFailureSession)
    with pytest.raises(RuntimeError, match="commit failure"):
        _drain(user.id, connection.id, session_factory=factory)
    assert _pending(run.id) is True
    with SessionLocal() as check:
        assert check.get(NutritionProjectionHead, (user.id, DAY)) is not None
