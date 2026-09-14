from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy import select

from app.models import HealthSample, ImportBatch, User
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
    NutritionDailyProjection,
    NutritionIngestionRun,
    NutritionProjectionHead,
    NutritionSourceObservation,
)
from app.source_priority.application import create_policy_with_rules
from app.source_priority.contracts import PriorityRuleSpec
from app.source_priority.models import SourcePriorityPolicy
from app.nutrition.projection.refresh import (
    DEFAULT_REFRESH_BATCH_SIZE,
    list_stale_nutrition_projection_dates,
)

DAY = date(2026, 9, 1)
POLICY_AT = datetime(2026, 9, 13, 12, tzinfo=UTC)
SOURCE_INSTANCE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def _policy(db, user: User, *, version: int = 1) -> SourcePriorityPolicy:
    snapshot = create_policy_with_rules(
        db,
        user.id,
        version,
        POLICY_AT + timedelta(days=version - 1),
        (PriorityRuleSpec("nutrition", None, "yazio", 1),),
    )
    db.commit()
    return db.get(SourcePriorityPolicy, snapshot.policy_id)  # type: ignore[return-value]


def _run(db, user_id: UUID) -> NutritionIngestionRun:
    run = NutritionIngestionRun(
        user_id=user_id,
        provider_key="yazio",
        source_instance_id=SOURCE_INSTANCE_ID,
        connector_variant="refresh-test",
        status="completed",
        coverage_state=CoverageState.COMPLETE.value,
    )
    db.add(run)
    db.flush()
    return run


def _observation(
    db,
    user_id: UUID,
    run: NutritionIngestionRun,
    *,
    key: str,
    local_date: date | None,
    observation_kind: str = ObservationKind.CONSUMPTION_EVENT.value,
) -> NutritionSourceObservation:
    row = NutritionSourceObservation(
        user_id=user_id,
        ingestion_run_id=run.id,
        provider_key="yazio",
        source_instance_id=SOURCE_INSTANCE_ID,
        connector_variant="refresh-test",
        observation_kind=observation_kind,
        source_namespace="refresh-test",
        source_record_id=key,
        source_revision=1,
        observation_fingerprint=sha256(key.encode()).hexdigest(),
        local_date=local_date,
        timezone_source="provider" if local_date is not None else None,
        time_confidence="exact" if local_date is not None else None,
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
        lineage_state=LineageState.CONFIRMED.value,
    )
    db.add(row)
    db.flush()
    return row


def _event(
    db,
    user_id: UUID,
    source: NutritionSourceObservation,
    *,
    key: str,
    local_date: date | None,
) -> NutritionConsumptionEvent:
    row = NutritionConsumptionEvent(
        user_id=user_id,
        source_observation_id=source.id,
        provider_key="yazio",
        source_instance_id=SOURCE_INSTANCE_ID,
        event_kind=ConsumptionEventKind.SIMPLE_PRODUCT.value,
        logical_event_key=key,
        revision=1,
        local_date=local_date,
        amount_unit="serving",
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
        lineage_state=LineageState.CONFIRMED.value,
    )
    db.add(row)
    db.flush()
    return row


def _projection(
    db,
    user_id: UUID,
    policy: SourcePriorityPolicy,
    local_date: date,
    *,
    status: str = "ready",
) -> NutritionDailyProjection:
    row = NutritionDailyProjection(
        user_id=user_id,
        local_date=local_date,
        projection_version=1,
        projection_algorithm_version="refresh-test",
        priority_policy_id=policy.id,
        input_watermark="refresh-test",
        projection_status=status,
    )
    db.add(row)
    db.flush()
    return row


def _head(db, user_id: UUID, local_date: date, projection: NutritionDailyProjection) -> None:
    db.add(
        NutritionProjectionHead(
            user_id=user_id,
            local_date=local_date,
            current_projection_id=projection.id,
        )
    )
    db.flush()


def test_observation_only_date_is_stale(db, user: User) -> None:
    policy = _policy(db, user)
    _observation(db, user.id, _run(db, user.id), key="observation", local_date=DAY)

    assert list_stale_nutrition_projection_dates(db, user_id=user.id, policy=policy, limit=50) == (DAY,)


def test_event_only_date_is_stale_even_when_source_is_undated(db, user: User) -> None:
    policy = _policy(db, user)
    source = _observation(db, user.id, _run(db, user.id), key="event", local_date=None)
    _event(db, user.id, source, key="event", local_date=DAY)

    assert list_stale_nutrition_projection_dates(db, user_id=user.id, policy=policy, limit=50) == (DAY,)


def test_duplicate_observation_and_event_date_is_returned_once(db, user: User) -> None:
    policy = _policy(db, user)
    source = _observation(db, user.id, _run(db, user.id), key="duplicate", local_date=DAY)
    _event(db, user.id, source, key="duplicate", local_date=DAY)

    assert list_stale_nutrition_projection_dates(db, user_id=user.id, policy=policy, limit=50) == (DAY,)


def test_existing_current_ready_head_is_fresh(db, user: User) -> None:
    policy = _policy(db, user)
    _observation(db, user.id, _run(db, user.id), key="fresh", local_date=DAY)
    _head(db, user.id, DAY, _projection(db, user.id, policy, DAY))

    assert list_stale_nutrition_projection_dates(db, user_id=user.id, policy=policy, limit=50) == ()


def test_dates_are_user_scoped(db, user: User) -> None:
    other = User(username="refresh-other", password_hash="fixture", timezone="UTC")
    db.add(other)
    db.flush()
    policy = _policy(db, user)
    _observation(db, other.id, _run(db, other.id), key="other", local_date=DAY)

    assert list_stale_nutrition_projection_dates(db, user_id=user.id, policy=policy, limit=50) == ()


def test_undated_product_profile_is_excluded(db, user: User) -> None:
    policy = _policy(db, user)
    _observation(
        db,
        user.id,
        _run(db, user.id),
        key="undated-profile",
        local_date=None,
        observation_kind=ObservationKind.PRODUCT_PROFILE.value,
    )

    assert list_stale_nutrition_projection_dates(db, user_id=user.id, policy=policy, limit=50) == ()


def test_health_sample_only_legacy_date_is_excluded(db, user: User) -> None:
    policy = _policy(db, user)
    batch = ImportBatch(user_id=user.id, source_type="legacy", status="completed")
    db.add(batch)
    db.flush()
    db.add(
        HealthSample(
            user_id=user.id,
            import_batch_id=batch.id,
            external_sample_id="legacy-refresh",
            fingerprint=sha256(b"legacy-refresh").hexdigest(),
            source_type="legacy",
            source_identifier="legacy",
            metric_type="dietary_energy_kcal",
            value=Decimal("1"),
            unit="kcal",
            original_value=Decimal("1"),
            original_unit="kcal",
            start_at=datetime(2026, 9, 1, tzinfo=UTC),
            end_at=datetime(2026, 9, 1, 1, tzinfo=UTC),
            local_date=DAY,
            timezone="UTC",
        )
    )
    db.flush()

    assert list_stale_nutrition_projection_dates(db, user_id=user.id, policy=policy, limit=50) == ()


def test_dates_are_ascending_deduplicated_and_bounded_at_fifty(db, user: User) -> None:
    policy = _policy(db, user)
    run = _run(db, user.id)
    for offset in range(55, -1, -1):
        day = DAY + timedelta(days=offset)
        source = _observation(db, user.id, run, key=f"source-{offset}", local_date=day)
        if offset % 2 == 0:
            _event(db, user.id, source, key=f"event-{offset}", local_date=day)

    dates = list_stale_nutrition_projection_dates(db, user_id=user.id, policy=policy, limit=DEFAULT_REFRESH_BATCH_SIZE)

    assert len(dates) == DEFAULT_REFRESH_BATCH_SIZE
    assert dates == tuple(sorted(set(dates)))
    assert dates == tuple(DAY + timedelta(days=offset) for offset in range(50))


def test_missing_head_is_stale(db, user: User) -> None:
    policy = _policy(db, user)
    _observation(db, user.id, _run(db, user.id), key="missing-head", local_date=DAY)

    assert list_stale_nutrition_projection_dates(db, user_id=user.id, policy=policy, limit=50) == (DAY,)


def test_head_only_failed_projection_date_is_stale(db, user: User) -> None:
    policy = _policy(db, user)
    _head(db, user.id, DAY, _projection(db, user.id, policy, DAY, status="failed"))

    assert list_stale_nutrition_projection_dates(db, user_id=user.id, policy=policy, limit=50) == (DAY,)


def test_old_policy_head_is_stale(db, user: User) -> None:
    old_policy = _policy(db, user)
    current_policy = _policy(db, user, version=2)
    _observation(db, user.id, _run(db, user.id), key="old-policy", local_date=DAY)
    _head(db, user.id, DAY, _projection(db, user.id, old_policy, DAY))

    assert list_stale_nutrition_projection_dates(db, user_id=user.id, policy=current_policy, limit=50) == (DAY,)


def test_current_policy_ready_head_is_fresh(db, user: User) -> None:
    policy = _policy(db, user)
    _observation(db, user.id, _run(db, user.id), key="current-ready", local_date=DAY)
    _head(db, user.id, DAY, _projection(db, user.id, policy, DAY))

    assert list_stale_nutrition_projection_dates(db, user_id=user.id, policy=policy, limit=50) == ()


def test_current_policy_no_value_ready_head_is_fresh(db, user: User) -> None:
    policy = _policy(db, user)
    _observation(db, user.id, _run(db, user.id), key="current-no-value", local_date=DAY)
    _head(db, user.id, DAY, _projection(db, user.id, policy, DAY))

    assert list_stale_nutrition_projection_dates(db, user_id=user.id, policy=policy, limit=50) == ()


def test_current_policy_failed_head_is_stale(db, user: User) -> None:
    policy = _policy(db, user)
    _observation(db, user.id, _run(db, user.id), key="failed", local_date=DAY)
    _head(db, user.id, DAY, _projection(db, user.id, policy, DAY, status="failed"))

    assert list_stale_nutrition_projection_dates(db, user_id=user.id, policy=policy, limit=50) == (DAY,)
