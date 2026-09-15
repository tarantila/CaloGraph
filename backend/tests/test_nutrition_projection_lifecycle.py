from __future__ import annotations

from datetime import UTC, date, datetime
from hashlib import sha256
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import User
from app.nutrition.enums import (
    ConsumptionEventKind,
    CoverageState,
    LineageState,
    ObservationKind,
    PresenceState,
    ResolutionState,
)
from app.nutrition.models import (
    NutritionConsumptionEvent,
    NutritionIngestionRun,
    NutritionSourceObservation,
)
from app.nutrition.projection import ProjectionPersistenceStatus, lifecycle
from app.nutrition.projection.lifecycle import (
    NutritionProjectionLifecycleError,
    NutritionProjectionLifecycleResult,
    rebuild_affected_nutrition_days,
    resolve_affected_nutrition_dates,
)

PROVIDER = "contract-provider"
OTHER_PROVIDER = "other-provider"
SOURCE_INSTANCE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
OTHER_SOURCE_INSTANCE_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
DAY_ONE = date(2026, 9, 1)
DAY_TWO = date(2026, 9, 2)
DAY_THREE = date(2026, 9, 3)
POLICY_AT = datetime(2026, 9, 13, 12, 34, 56, 789, tzinfo=UTC)


def _run(
    db: Session,
    user_id: UUID,
    *,
    provider_key: str = PROVIDER,
    source_instance_id: UUID = SOURCE_INSTANCE_ID,
    requested_start_date: date | None = None,
    requested_end_date: date | None = None,
    covered_start_date: date | None = None,
    covered_end_date: date | None = None,
    run_id: UUID | None = None,
) -> NutritionIngestionRun:
    run = NutritionIngestionRun(
        id=run_id or uuid4(),
        user_id=user_id,
        provider_key=provider_key,
        source_instance_id=source_instance_id,
        connector_variant="contract-fixture",
        requested_start_date=requested_start_date,
        requested_end_date=requested_end_date,
        covered_start_date=covered_start_date,
        covered_end_date=covered_end_date,
        status="completed",
        coverage_state=CoverageState.COMPLETE.value,
    )
    db.add(run)
    db.flush()
    return run


def _source(
    db: Session,
    user_id: UUID,
    run: NutritionIngestionRun,
    *,
    key: str,
    local_date: date | None,
    provider_key: str | None = None,
    source_instance_id: UUID | None = None,
    source_revision: int = 1,
) -> NutritionSourceObservation:
    fingerprint = sha256(f"source:{key}:{source_revision}".encode()).hexdigest()
    source = NutritionSourceObservation(
        id=uuid4(),
        user_id=user_id,
        ingestion_run_id=run.id,
        provider_key=provider_key or run.provider_key,
        source_instance_id=source_instance_id or run.source_instance_id,
        connector_variant="contract-fixture",
        observation_kind=ObservationKind.CONSUMPTION_EVENT.value,
        source_namespace="contract-fixture",
        source_record_id=key,
        source_revision=source_revision,
        observation_fingerprint=fingerprint,
        local_date=local_date,
        timezone_source="provider" if local_date is not None else None,
        time_confidence="exact" if local_date is not None else None,
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
        lineage_state=LineageState.CONFIRMED.value,
    )
    db.add(source)
    db.flush()
    return source


def _event(
    db: Session,
    user_id: UUID,
    source: NutritionSourceObservation,
    *,
    logical_event_key: str,
    local_date: date | None,
    provider_key: str | None = None,
    source_instance_id: UUID | None = None,
    revision: int = 1,
    supersedes: NutritionConsumptionEvent | None = None,
) -> NutritionConsumptionEvent:
    event = NutritionConsumptionEvent(
        id=uuid4(),
        user_id=user_id,
        source_observation_id=source.id,
        provider_key=provider_key or source.provider_key,
        source_instance_id=source_instance_id or source.source_instance_id,
        event_kind=ConsumptionEventKind.SIMPLE_PRODUCT.value,
        logical_event_key=logical_event_key,
        supersedes_event_id=supersedes.id if supersedes is not None else None,
        revision=revision,
        supersedes_revision=supersedes.revision if supersedes is not None else None,
        local_date=local_date,
        amount_unit="serving",
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
        lineage_state=LineageState.CONFIRMED.value,
    )
    db.add(event)
    db.flush()
    return event


def _other_user(db: Session) -> User:
    other = User(
        username=f"other-{uuid4().hex[:12]}",
        password_hash="fixture-password-hash",
        timezone="UTC",
    )
    db.add(other)
    db.flush()
    return other


def test_lifecycle_result_is_frozen_and_slotted_and_error_is_runtime_error() -> None:
    assert NutritionProjectionLifecycleResult.__dataclass_params__.frozen is True
    assert "user_id" in NutritionProjectionLifecycleResult.__slots__
    assert issubclass(NutritionProjectionLifecycleError, RuntimeError)


def test_resolver_returns_exactly_one_date_for_one_new_observation(db: Session, user: User) -> None:
    run = _run(db, user.id)
    _source(db, user.id, run, key="one-observation", local_date=DAY_ONE)

    assert resolve_affected_nutrition_dates(db, user_id=user.id, ingestion_run_id=run.id) == (
        DAY_ONE,
    )


def test_resolver_returns_sorted_deduplicated_dates_for_multiple_new_observations(
    db: Session, user: User
) -> None:
    run = _run(db, user.id)
    _source(db, user.id, run, key="later-a", local_date=DAY_TWO)
    _source(db, user.id, run, key="earlier", local_date=DAY_ONE)
    _source(db, user.id, run, key="later-b", local_date=DAY_TWO)

    assert resolve_affected_nutrition_dates(db, user_id=user.id, ingestion_run_id=run.id) == (
        DAY_ONE,
        DAY_TWO,
    )


def test_resolver_does_not_invent_dates_from_requested_or_covered_ranges(db: Session, user: User) -> None:
    run = _run(
        db,
        user.id,
        requested_start_date=DAY_ONE,
        requested_end_date=DAY_THREE,
        covered_start_date=DAY_ONE,
        covered_end_date=DAY_THREE,
    )

    assert resolve_affected_nutrition_dates(db, user_id=user.id, ingestion_run_id=run.id) == ()


def test_resolver_ignores_observation_without_date_and_without_consumption_event(
    db: Session, user: User
) -> None:
    run = _run(db, user.id)
    _source(db, user.id, run, key="undated-product", local_date=None)

    assert resolve_affected_nutrition_dates(db, user_id=user.id, ingestion_run_id=run.id) == ()


def test_resolver_includes_event_date_when_source_observation_is_undated(
    db: Session, user: User
) -> None:
    run = _run(db, user.id)
    source = _source(db, user.id, run, key="undated-event", local_date=None)
    _event(
        db,
        user.id,
        source,
        logical_event_key="undated-event-key",
        local_date=DAY_TWO,
    )

    assert resolve_affected_nutrition_dates(db, user_id=user.id, ingestion_run_id=run.id) == (
        DAY_TWO,
    )


def test_resolver_includes_both_dates_for_event_revision_moved_between_days(
    db: Session, user: User
) -> None:
    first_run = _run(db, user.id)
    first_source = _source(db, user.id, first_run, key="meal-v1", local_date=DAY_ONE)
    first_event = _event(
        db,
        user.id,
        first_source,
        logical_event_key="meal",
        local_date=DAY_ONE,
    )

    second_run = _run(db, user.id)
    second_source = _source(db, user.id, second_run, key="meal-v2", local_date=DAY_TWO)
    _event(
        db,
        user.id,
        second_source,
        logical_event_key="meal",
        local_date=DAY_TWO,
        revision=2,
        supersedes=first_event,
    )

    assert resolve_affected_nutrition_dates(db, user_id=user.id, ingestion_run_id=second_run.id) == (
        DAY_ONE,
        DAY_TWO,
    )


def test_resolver_deduplicates_same_date_event_revision(db: Session, user: User) -> None:
    first_run = _run(db, user.id)
    first_source = _source(db, user.id, first_run, key="same-day-v1", local_date=DAY_ONE)
    first_event = _event(
        db,
        user.id,
        first_source,
        logical_event_key="same-day-meal",
        local_date=DAY_ONE,
    )

    second_run = _run(db, user.id)
    second_source = _source(db, user.id, second_run, key="same-day-v2", local_date=DAY_ONE)
    _event(
        db,
        user.id,
        second_source,
        logical_event_key="same-day-meal",
        local_date=DAY_ONE,
        revision=2,
        supersedes=first_event,
    )

    assert resolve_affected_nutrition_dates(db, user_id=user.id, ingestion_run_id=second_run.id) == (
        DAY_ONE,
    )


def test_resolver_excludes_evidence_for_another_user(db: Session, user: User) -> None:
    other = _other_user(db)
    requested_run = _run(db, user.id)
    other_run = _run(db, other.id)
    _source(db, other.id, other_run, key="foreign-user", local_date=DAY_ONE)

    assert resolve_affected_nutrition_dates(
        db, user_id=user.id, ingestion_run_id=requested_run.id
    ) == ()


def test_resolver_rejects_run_owned_by_another_user(db: Session, user: User) -> None:
    other = _other_user(db)
    other_run = _run(db, other.id)

    with pytest.raises(NutritionProjectionLifecycleError):
        resolve_affected_nutrition_dates(db, user_id=user.id, ingestion_run_id=other_run.id)


def test_resolver_rejects_nonexistent_ingestion_run(db: Session, user: User) -> None:
    with pytest.raises(NutritionProjectionLifecycleError):
        resolve_affected_nutrition_dates(db, user_id=user.id, ingestion_run_id=uuid4())


def test_resolver_excludes_evidence_for_another_ingestion_run(db: Session, user: User) -> None:
    requested_run = _run(db, user.id)
    other_run = _run(db, user.id)
    _source(db, user.id, other_run, key="foreign-run", local_date=DAY_ONE)

    assert resolve_affected_nutrition_dates(
        db, user_id=user.id, ingestion_run_id=requested_run.id
    ) == ()


def _break_supersedes_fk(
    db: Session,
    event: NutritionConsumptionEvent,
    *,
    supersedes_event_id: UUID,
    supersedes_revision: int,
    revision: int,
) -> None:
    db.commit()
    connection = db.connection()
    connection.exec_driver_sql("PRAGMA foreign_keys = OFF")
    db.execute(
        NutritionConsumptionEvent.__table__.update()
        .where(NutritionConsumptionEvent.id == event.id)
        .values(
            supersedes_event_id=supersedes_event_id,
            supersedes_revision=supersedes_revision,
            revision=revision,
        )
    )
    db.commit()
    connection = db.connection()
    connection.exec_driver_sql("PRAGMA foreign_keys = ON")
    connection.commit()
    db.expire_all()


def _event_with_malformed_ancestor(
    db: Session,
    user: User,
    *,
    ancestor_user: User | None = None,
    ancestor_provider: str = PROVIDER,
    ancestor_source_instance_id: UUID = SOURCE_INSTANCE_ID,
    ancestor_logical_key: str = "ancestor-key",
    supersedes_revision_delta: int = 0,
) -> NutritionIngestionRun:
    ancestor_user = ancestor_user or user
    ancestor_run = _run(
        db,
        ancestor_user.id,
        provider_key=ancestor_provider,
        source_instance_id=ancestor_source_instance_id,
    )
    ancestor_source = _source(
        db,
        ancestor_user.id,
        ancestor_run,
        key=f"ancestor-{uuid4().hex}",
        local_date=DAY_ONE,
    )
    ancestor = _event(
        db,
        ancestor_user.id,
        ancestor_source,
        logical_event_key=ancestor_logical_key,
        local_date=DAY_ONE,
    )

    current_run = _run(db, user.id)
    current_source = _source(
        db,
        user.id,
        current_run,
        key=f"current-{uuid4().hex}",
        local_date=DAY_TWO,
    )
    current = _event(
        db,
        user.id,
        current_source,
        logical_event_key=ancestor_logical_key,
        local_date=DAY_TWO,
        revision=2,
    )
    _break_supersedes_fk(
        db,
        current,
        supersedes_event_id=ancestor.id,
        supersedes_revision=ancestor.revision + supersedes_revision_delta,
        revision=ancestor.revision + supersedes_revision_delta + 1,
    )
    return current_run


def test_resolver_rejects_cross_user_superseded_ancestor(db: Session, user: User) -> None:
    with pytest.raises(NutritionProjectionLifecycleError):
        _resolve_malformed_ancestor(db, user, ancestor_user=_other_user(db))


def test_resolver_rejects_cross_provider_superseded_ancestor(db: Session, user: User) -> None:
    with pytest.raises(NutritionProjectionLifecycleError):
        _resolve_malformed_ancestor(db, user, ancestor_provider=OTHER_PROVIDER)


def test_resolver_rejects_cross_source_instance_superseded_ancestor(db: Session, user: User) -> None:
    with pytest.raises(NutritionProjectionLifecycleError):
        _resolve_malformed_ancestor(
            db,
            user,
            ancestor_source_instance_id=OTHER_SOURCE_INSTANCE_ID,
        )


def test_resolver_rejects_cross_logical_key_superseded_ancestor(db: Session, user: User) -> None:
    ancestor_run = _run(db, user.id)
    ancestor_source = _source(db, user.id, ancestor_run, key="logical-ancestor", local_date=DAY_ONE)
    ancestor = _event(
        db,
        user.id,
        ancestor_source,
        logical_event_key="old-logical-key",
        local_date=DAY_ONE,
    )
    current_run = _run(db, user.id)
    current_source = _source(db, user.id, current_run, key="logical-current", local_date=DAY_TWO)
    current = _event(
        db,
        user.id,
        current_source,
        logical_event_key="new-logical-key",
        local_date=DAY_TWO,
    )
    _break_supersedes_fk(
        db,
        current,
        supersedes_event_id=ancestor.id,
        supersedes_revision=ancestor.revision,
        revision=ancestor.revision + 1,
    )

    with pytest.raises(NutritionProjectionLifecycleError):
        resolve_affected_nutrition_dates(db, user_id=user.id, ingestion_run_id=current_run.id)


def _resolve_malformed_ancestor(
    db: Session,
    user: User,
    **kwargs: object,
) -> tuple[date, ...]:
    current_run = _event_with_malformed_ancestor(db, user, **kwargs)
    return resolve_affected_nutrition_dates(db, user_id=user.id, ingestion_run_id=current_run.id)


def test_resolver_rejects_malformed_supersedes_revision(db: Session, user: User) -> None:
    with pytest.raises(NutritionProjectionLifecycleError):
        _resolve_malformed_ancestor(db, user, supersedes_revision_delta=1)


def test_resolver_rejects_revision_without_supersedes_ancestry(
    db: Session, user: User
) -> None:
    run = _run(db, user.id)
    source = _source(db, user.id, run, key="truncated-root", local_date=DAY_ONE)
    _event(
        db,
        user.id,
        source,
        logical_event_key="truncated-root-meal",
        local_date=DAY_ONE,
        revision=2,
    )

    with pytest.raises(NutritionProjectionLifecycleError):
        resolve_affected_nutrition_dates(db, user_id=user.id, ingestion_run_id=run.id)


def test_resolver_rejects_truncated_revision_chain(db: Session, user: User) -> None:
    ancestor_run = _run(db, user.id)
    ancestor_source = _source(db, user.id, ancestor_run, key="truncated-v2", local_date=DAY_ONE)
    ancestor = _event(
        db,
        user.id,
        ancestor_source,
        logical_event_key="truncated-chain",
        local_date=DAY_ONE,
        revision=2,
    )

    current_run = _run(db, user.id)
    current_source = _source(db, user.id, current_run, key="truncated-v3", local_date=DAY_TWO)
    _event(
        db,
        user.id,
        current_source,
        logical_event_key="truncated-chain",
        local_date=DAY_TWO,
        revision=3,
        supersedes=ancestor,
    )

    with pytest.raises(NutritionProjectionLifecycleError):
        resolve_affected_nutrition_dates(db, user_id=user.id, ingestion_run_id=current_run.id)


def test_resolver_rejects_missing_supersedes_ancestry(db: Session, user: User) -> None:
    run = _run(db, user.id)
    source = _source(db, user.id, run, key="missing-ancestor", local_date=DAY_TWO)
    event = _event(
        db,
        user.id,
        source,
        logical_event_key="missing-ancestor-meal",
        local_date=DAY_TWO,
    )
    missing_ancestor_id = uuid4()
    _break_supersedes_fk(
        db,
        event,
        supersedes_event_id=missing_ancestor_id,
        supersedes_revision=1,
        revision=2,
    )

    with pytest.raises(NutritionProjectionLifecycleError):
        resolve_affected_nutrition_dates(db, user_id=user.id, ingestion_run_id=run.id)


class _FakeSession:
    def __init__(self, events: list[tuple[str, _FakeSession]]) -> None:
        self.closed = False
        self.events = events

    def close(self) -> None:
        self.closed = True
        self.events.append(("close", self))


def _session_factory(
    sessions: list[_FakeSession],
    events: list[tuple[str, _FakeSession]],
):
    def factory() -> _FakeSession:
        session = _FakeSession(events)
        sessions.append(session)
        events.append(("factory", session))
        return session

    return factory


def _patch_lifecycle_inputs(
    monkeypatch: pytest.MonkeyPatch,
    dates: tuple[date, ...],
    resolver_calls: list[tuple[_FakeSession, UUID, UUID]],
    b6_calls: list[tuple[_FakeSession, UUID, UUID, date, datetime]],
    statuses: dict[date, ProjectionPersistenceStatus],
    *,
    failures: dict[date, BaseException] | None = None,
):
    def fake_resolver(
        db: _FakeSession,
        *,
        user_id: UUID,
        ingestion_run_id: UUID,
    ) -> tuple[date, ...]:
        resolver_calls.append((db, user_id, ingestion_run_id))
        return dates

    monkeypatch.setattr(lifecycle, "resolve_affected_nutrition_dates", fake_resolver)

    def fake_b6(
        db: _FakeSession,
        *,
        user_id: UUID,
        local_date: date,
        policy_at: datetime,
    ):
        b6_calls.append((db, user_id, lifecycle_run_id, local_date, policy_at))
        failure = (failures or {}).get(local_date)
        if failure is not None:
            raise failure
        return SimpleNamespace(status=statuses[local_date])

    lifecycle_run_id = UUID("22222222-2222-2222-2222-222222222222")
    monkeypatch.setattr(lifecycle, "rebuild_nutrition_day", fake_b6)


def test_lifecycle_collects_created_unchanged_and_policy_missing_dates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dates = (DAY_ONE, DAY_TWO, DAY_THREE)
    user_id = UUID("11111111-1111-1111-1111-111111111111")
    ingestion_run_id = UUID("22222222-2222-2222-2222-222222222222")
    resolver_calls: list[tuple[_FakeSession, UUID, UUID]] = []
    b6_calls: list[tuple[_FakeSession, UUID, UUID, date, datetime]] = []
    _patch_lifecycle_inputs(
        monkeypatch,
        dates,
        resolver_calls,
        b6_calls,
        {
            DAY_ONE: ProjectionPersistenceStatus.CREATED,
            DAY_TWO: ProjectionPersistenceStatus.UNCHANGED,
            DAY_THREE: ProjectionPersistenceStatus.POLICY_MISSING,
        },
    )
    sessions: list[_FakeSession] = []
    events: list[tuple[str, _FakeSession]] = []

    result = rebuild_affected_nutrition_days(
        session_factory=_session_factory(sessions, events),
        user_id=user_id,
        ingestion_run_id=ingestion_run_id,
        policy_at=POLICY_AT,
    )

    assert result == NutritionProjectionLifecycleResult(
        user_id=user_id,
        ingestion_run_id=ingestion_run_id,
        affected_dates=dates,
        created_dates=(DAY_ONE,),
        unchanged_dates=(DAY_TWO,),
        policy_missing_dates=(DAY_THREE,),
    )
    assert resolver_calls == [(sessions[0], user_id, ingestion_run_id)]
    assert b6_calls == [
        (sessions[1], user_id, ingestion_run_id, DAY_ONE, POLICY_AT),
        (sessions[2], user_id, ingestion_run_id, DAY_TWO, POLICY_AT),
        (sessions[3], user_id, ingestion_run_id, DAY_THREE, POLICY_AT),
    ]
    assert len({id(session) for session in sessions}) == 4
    assert all(session.closed for session in sessions)
    assert events == [
        ("factory", sessions[0]),
        ("close", sessions[0]),
        ("factory", sessions[1]),
        ("close", sessions[1]),
        ("factory", sessions[2]),
        ("close", sessions[2]),
        ("factory", sessions[3]),
        ("close", sessions[3]),
    ]


def test_lifecycle_processes_dates_sorted_and_passes_same_policy_at_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = UUID("11111111-1111-1111-1111-111111111111")
    ingestion_run_id = UUID("22222222-2222-2222-2222-222222222222")
    resolver_calls: list[tuple[_FakeSession, UUID, UUID]] = []
    b6_calls: list[tuple[_FakeSession, UUID, UUID, date, datetime]] = []
    _patch_lifecycle_inputs(
        monkeypatch,
        (DAY_THREE, DAY_ONE, DAY_TWO),
        resolver_calls,
        b6_calls,
        {day: ProjectionPersistenceStatus.UNCHANGED for day in (DAY_ONE, DAY_TWO, DAY_THREE)},
    )
    sessions: list[_FakeSession] = []
    events: list[tuple[str, _FakeSession]] = []

    rebuild_affected_nutrition_days(
        session_factory=_session_factory(sessions, events),
        user_id=user_id,
        ingestion_run_id=ingestion_run_id,
        policy_at=POLICY_AT,
    )

    assert resolver_calls == [(sessions[0], user_id, ingestion_run_id)]
    assert [call[3] for call in b6_calls] == [DAY_ONE, DAY_TWO, DAY_THREE]
    assert all(call[1] == user_id and call[2] == ingestion_run_id for call in b6_calls)
    assert all(call[4] is POLICY_AT for call in b6_calls)
    assert POLICY_AT.tzinfo is UTC
    assert len({id(call[0]) for call in b6_calls}) == 3
    assert all(session.closed for session in sessions)


def test_lifecycle_with_no_affected_dates_never_calls_b6(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = UUID("11111111-1111-1111-1111-111111111111")
    ingestion_run_id = UUID("22222222-2222-2222-2222-222222222222")
    resolver_calls: list[tuple[_FakeSession, UUID, UUID]] = []
    b6_calls: list[tuple[_FakeSession, UUID, UUID, date, datetime]] = []
    _patch_lifecycle_inputs(monkeypatch, (), resolver_calls, b6_calls, {})
    sessions: list[_FakeSession] = []
    events: list[tuple[str, _FakeSession]] = []

    result = rebuild_affected_nutrition_days(
        session_factory=_session_factory(sessions, events),
        user_id=user_id,
        ingestion_run_id=ingestion_run_id,
        policy_at=POLICY_AT,
    )

    assert result.affected_dates == ()
    assert result.created_dates == ()
    assert result.unchanged_dates == ()
    assert result.policy_missing_dates == ()
    assert resolver_calls == [(sessions[0], user_id, ingestion_run_id)]
    assert b6_calls == []
    assert len(sessions) == 1
    assert sessions[0].closed


def test_lifecycle_closes_resolver_session_when_resolver_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = UUID("11111111-1111-1111-1111-111111111111")
    ingestion_run_id = UUID("22222222-2222-2222-2222-222222222222")
    resolver_calls: list[tuple[_FakeSession, UUID, UUID]] = []
    sessions: list[_FakeSession] = []
    events: list[tuple[str, _FakeSession]] = []

    def failing_resolver(
        db: _FakeSession,
        *,
        user_id: UUID,
        ingestion_run_id: UUID,
    ) -> tuple[date, ...]:
        resolver_calls.append((db, user_id, ingestion_run_id))
        raise RuntimeError("resolver failure")

    monkeypatch.setattr(lifecycle, "resolve_affected_nutrition_dates", failing_resolver)

    with pytest.raises(NutritionProjectionLifecycleError):
        rebuild_affected_nutrition_days(
            session_factory=_session_factory(sessions, events),
            user_id=user_id,
            ingestion_run_id=ingestion_run_id,
            policy_at=POLICY_AT,
        )

    assert resolver_calls == [(sessions[0], user_id, ingestion_run_id)]
    assert len(sessions) == 1
    assert sessions[0].closed
    assert events == [("factory", sessions[0]), ("close", sessions[0])]


def test_lifecycle_wraps_structural_b6_failure_and_stops_later_dates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = UUID("11111111-1111-1111-1111-111111111111")
    ingestion_run_id = UUID("22222222-2222-2222-2222-222222222222")
    resolver_calls: list[tuple[_FakeSession, UUID, UUID]] = []
    b6_calls: list[tuple[_FakeSession, UUID, UUID, date, datetime]] = []
    _patch_lifecycle_inputs(
        monkeypatch,
        (DAY_ONE, DAY_TWO, DAY_THREE),
        resolver_calls,
        b6_calls,
        {DAY_ONE: ProjectionPersistenceStatus.CREATED, DAY_TWO: ProjectionPersistenceStatus.CREATED},
        failures={DAY_TWO: RuntimeError("structural B6 failure")},
    )
    sessions: list[_FakeSession] = []
    events: list[tuple[str, _FakeSession]] = []

    with pytest.raises(NutritionProjectionLifecycleError):
        rebuild_affected_nutrition_days(
            session_factory=_session_factory(sessions, events),
            user_id=user_id,
            ingestion_run_id=ingestion_run_id,
            policy_at=POLICY_AT,
        )

    assert [call[3] for call in b6_calls] == [DAY_ONE, DAY_TWO]
    assert len(sessions) == 3
    assert all(session.closed for session in sessions)
    assert events == [
        ("factory", sessions[0]),
        ("close", sessions[0]),
        ("factory", sessions[1]),
        ("close", sessions[1]),
        ("factory", sessions[2]),
        ("close", sessions[2]),
    ]


def test_lifecycle_retry_after_partial_failure_keeps_prior_date_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dates = (DAY_ONE, DAY_TWO, DAY_THREE)
    user_id = UUID("11111111-1111-1111-1111-111111111111")
    ingestion_run_id = UUID("22222222-2222-2222-2222-222222222222")
    resolver_calls: list[tuple[_FakeSession, UUID, UUID]] = []
    b6_calls: list[tuple[_FakeSession, UUID, UUID, date, datetime]] = []
    attempts = {DAY_ONE: 0, DAY_TWO: 0, DAY_THREE: 0}
    monkeypatch.setattr(
        lifecycle,
        "resolve_affected_nutrition_dates",
        lambda db, *, user_id, ingestion_run_id: (
            resolver_calls.append((db, user_id, ingestion_run_id)) or dates
        ),
    )

    def fake_b6(
        db: _FakeSession,
        *,
        user_id: UUID,
        local_date: date,
        policy_at: datetime,
    ):
        b6_calls.append((db, user_id, ingestion_run_id, local_date, policy_at))
        attempts[local_date] += 1
        if local_date == DAY_TWO and attempts[local_date] == 1:
            raise RuntimeError("partial failure")
        if local_date == DAY_ONE:
            status = (
                ProjectionPersistenceStatus.CREATED
                if attempts[local_date] == 1
                else ProjectionPersistenceStatus.UNCHANGED
            )
        else:
            status = ProjectionPersistenceStatus.CREATED
        return SimpleNamespace(status=status)

    monkeypatch.setattr(lifecycle, "rebuild_nutrition_day", fake_b6)
    sessions: list[_FakeSession] = []
    events: list[tuple[str, _FakeSession]] = []
    factory = _session_factory(sessions, events)

    with pytest.raises(NutritionProjectionLifecycleError):
        rebuild_affected_nutrition_days(
            session_factory=factory,
            user_id=user_id,
            ingestion_run_id=ingestion_run_id,
            policy_at=POLICY_AT,
        )

    retry = rebuild_affected_nutrition_days(
        session_factory=factory,
        user_id=user_id,
        ingestion_run_id=ingestion_run_id,
        policy_at=POLICY_AT,
    )

    assert retry.created_dates == (DAY_TWO, DAY_THREE)
    assert retry.unchanged_dates == (DAY_ONE,)
    assert retry.policy_missing_dates == ()
    assert [call[3] for call in b6_calls] == [
        DAY_ONE,
        DAY_TWO,
        DAY_ONE,
        DAY_TWO,
        DAY_THREE,
    ]
    assert all(call[1] == user_id and call[2] == ingestion_run_id for call in b6_calls)
    assert all(call[4] is POLICY_AT for call in b6_calls)
    assert len({id(call[0]) for call in b6_calls}) == 5
    assert len(sessions) == 7
    assert all(session.closed for session in sessions)
