from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from uuid import UUID, uuid4

import pytest

from app.analytics.nutrition_projection import (
    NutritionProjectionReadError,
    NutritionProjectionReadState,
    read_canonical_nutrition_day,
)
from app.services.yazio_nutrition_ingestion import ingest_yazio_food_diary
from app.services.yazio_provider import YazioDailyNutrientSummary, YazioFoodDiary, YazioNutrientValues
from sqlalchemy import select
from app.database import SessionLocal
from app.models import GoogleHealthConnection, HealthSample, ImportBatch, User, YazioConnection
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
    NutritionDailyProjectionFact,
    NutritionDailyProjectionLineage,
    NutritionFieldObservation,
    NutritionIngestionRun,
    NutritionProjectionHead,
    NutritionSourceObservation,
)
from app.nutrition.projection.contracts import (
    ProjectionPersistenceResult,
    ProjectionPersistenceStatus,
)
from app.nutrition.projection.refresh import (
    DEFAULT_REFRESH_BATCH_SIZE,
    NutritionProjectionRefreshError,
    NutritionProjectionRefreshResult,
    list_stale_nutrition_projection_dates,
    refresh_stale_nutrition_projections,
)
from app.source_priority.application import create_policy_with_rules
from app.source_priority.contracts import PriorityRuleSpec
from app.source_priority.models import SourcePriorityPolicy

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


def _yazio_connection(db, user: User) -> YazioConnection:
    connection = YazioConnection(
        user_id=user.id,
        encrypted_email=b"refresh-email",
        encrypted_password=b"refresh-password",
        source_identifier="refresh-test",
    )
    db.add(connection)
    db.flush()
    return connection


def _google_connection(db, user: User) -> GoogleHealthConnection:
    connection = GoogleHealthConnection(
        user_id=user.id,
        encrypted_refresh_token=b"refresh-token",
        granted_scopes=[],
    )
    db.add(connection)
    db.flush()
    return connection


def _run(
    db,
    user_id: UUID,
    *,
    provider_key: str = "yazio",
    source_instance_id: UUID = SOURCE_INSTANCE_ID,
) -> NutritionIngestionRun:
    run = NutritionIngestionRun(
        user_id=user_id,
        provider_key=provider_key,
        source_instance_id=source_instance_id,
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
        provider_key=run.provider_key,
        source_instance_id=run.source_instance_id,
        connector_variant="refresh-test",
        observation_kind=observation_kind,
        source_namespace=(
            "google_health.nutrition_log"
            if run.provider_key == "google_health"
            else "yazio.simple_product"
        ),
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
        provider_key=source.provider_key,
        source_instance_id=source.source_instance_id,
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


def _field(
    db,
    user_id: UUID,
    source: NutritionSourceObservation,
    *,
    value: Decimal,
    metric_key: str = "protein_g",
) -> NutritionFieldObservation:
    unit = "kcal" if metric_key == "dietary_energy_kcal" else "g"
    field = NutritionFieldObservation(
        user_id=user_id,
        source_observation_id=source.id,
        provider_field_path=f"nutrition.{metric_key}",
        provider_raw_value_decimal=value,
        provider_raw_unit=unit,
        metric_key=metric_key,
        canonical_value=value,
        canonical_unit=unit,
        observation_role="canonical" if source.provider_key == "google_health" else "provider",
        presence_state=(
            PresenceState.EXPLICIT_ZERO.value if value == Decimal("0") else PresenceState.SUPPLIED.value
        ),
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
        lineage_state=LineageState.CONFIRMED.value,
    )
    db.add(field)
    db.flush()
    return field

def _projection(
    db,
    user_id: UUID,
    policy: SourcePriorityPolicy,
    local_date: date,
    *,
    status: str = "ready",
    projection_version: int = 1,
) -> NutritionDailyProjection:
    row = NutritionDailyProjection(
        user_id=user_id,
        local_date=local_date,
        projection_version=projection_version,
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

def test_refresh_processes_120_dates_in_bounded_resumable_batches(
    db, user: User
) -> None:
    _policy(db, user)
    connection = _yazio_connection(db, user)
    run = _run(db, user.id, source_instance_id=connection.id)
    first_date = DAY - timedelta(days=119)
    for offset in range(120):
        local_date = first_date + timedelta(days=offset)
        source = _observation(
            db,
            user.id,
            run,
            key=f"historical-{offset}",
            local_date=local_date,
        )
        _event(db, user.id, source, key=f"historical-{offset}", local_date=local_date)
        _field(db, user.id, source, value=Decimal(offset + 1))
    db.commit()

    results = tuple(
        refresh_stale_nutrition_projections(
            session_factory=SessionLocal,
            user_id=user.id,
            expected_version=1,
        )
        for _ in range(4)
    )

    assert [result.processed_count for result in results] == [50, 50, 20, 0]
    assert [result.created_count for result in results] == [50, 50, 20, 0]
    assert [result.unchanged_count for result in results] == [0, 0, 0, 0]
    assert results[-1].has_more is False
    assert results[-1].projection_refresh_required is False
    assert db.query(NutritionDailyProjection).count() == 120
    assert db.query(NutritionProjectionHead).count() == 120

    last_head = db.get(NutritionProjectionHead, (user.id, DAY))
    assert last_head is not None
    last_projection = db.get(NutritionDailyProjection, last_head.current_projection_id)
    assert last_projection is not None
    last_fact = db.scalar(
        select(NutritionDailyProjectionFact).where(
            NutritionDailyProjectionFact.projection_id == last_projection.id,
            NutritionDailyProjectionFact.metric_key == "protein_g",
        )
    )
    assert last_fact is not None
    assert last_fact.value == Decimal("120")
    last_lineage = db.scalar(
        select(NutritionDailyProjectionLineage).where(
            NutritionDailyProjectionLineage.projection_fact_id == last_fact.id,
            NutritionDailyProjectionLineage.role == "selected",
        )
    )
    assert last_lineage is not None
    assert last_lineage.provider_key == "yazio"

def test_refresh_all_fresh_dates_is_a_no_op(db, user: User, monkeypatch) -> None:
    policy = _policy(db, user)
    _yazio_connection(db, user)
    _observation(db, user.id, _run(db, user.id), key="fresh", local_date=DAY)
    _head(db, user.id, DAY, _projection(db, user.id, policy, DAY))
    db.commit()

    from app.nutrition.projection import refresh as refresh_module

    def should_not_rebuild(*args, **kwargs):
        raise AssertionError("fresh dates must not be rebuilt")

    monkeypatch.setattr(refresh_module, "rebuild_nutrition_day", should_not_rebuild)
    result = refresh_stale_nutrition_projections(
        session_factory=SessionLocal,
        user_id=user.id,
        expected_version=1,
    )

    assert result == NutritionProjectionRefreshResult(
        policy_version=1,
        processed_count=0,
        created_count=0,
        unchanged_count=0,
        has_more=False,
        projection_refresh_required=False,
    )


def test_refresh_rejects_an_unexpected_policy_version(db, user: User, monkeypatch) -> None:
    _policy(db, user)
    calls = 0

    from app.nutrition.projection import refresh as refresh_module

    def should_not_rebuild(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("policy validation must precede rebuild")

    monkeypatch.setattr(refresh_module, "rebuild_nutrition_day", should_not_rebuild)
    with pytest.raises(NutritionProjectionRefreshError, match="expected policy version"):
        refresh_stale_nutrition_projections(
            session_factory=SessionLocal,
            user_id=user.id,
            expected_version=2,
        )
    assert calls == 0


def test_refresh_rejects_an_unusable_effective_policy(db, user: User) -> None:
    create_policy_with_rules(db, user.id, 1, POLICY_AT, ())
    db.commit()

    with pytest.raises(NutritionProjectionRefreshError, match="usable effective"):
        refresh_stale_nutrition_projections(
            session_factory=SessionLocal,
            user_id=user.id,
            expected_version=1,
        )


def test_refresh_preserves_committed_dates_when_a_later_date_fails(db, user: User, monkeypatch) -> None:
    policy = _policy(db, user)
    _yazio_connection(db, user)
    run = _run(db, user.id)
    dates = (DAY, DAY + timedelta(days=1), DAY + timedelta(days=2))
    for local_date in dates:
        _observation(db, user.id, run, key=f"partial-{local_date}", local_date=local_date)
    db.commit()

    from app.nutrition.projection import refresh as refresh_module

    calls: list[date] = []

    def fail_on_second(
        session,
        *,
        user_id,
        local_date,
        policy_at,
    ):
        calls.append(local_date)
        if local_date == dates[1] and calls.count(local_date) == 1:
            raise NutritionProjectionRefreshError("safe rebuild failure")
        projection = _projection(session, user_id, policy, local_date)
        _head(session, user_id, local_date, projection)
        session.commit()
        return ProjectionPersistenceResult(
            user_id=user_id,
            local_date=local_date,
            projection_id=projection.id,
            projection_version=projection.projection_version,
            input_watermark="refresh-test",
            created=True,
            status=ProjectionPersistenceStatus.CREATED,
        )

    monkeypatch.setattr(refresh_module, "rebuild_nutrition_day", fail_on_second)
    with pytest.raises(NutritionProjectionRefreshError, match="safe rebuild failure"):
        refresh_stale_nutrition_projections(
            session_factory=SessionLocal,
            user_id=user.id,
            expected_version=1,
        )

    db.expire_all()
    assert db.scalar(
        select(NutritionProjectionHead).where(
            NutritionProjectionHead.user_id == user.id,
            NutritionProjectionHead.local_date == dates[0],
        )
    ) is not None
    assert db.scalar(
        select(NutritionProjectionHead).where(
            NutritionProjectionHead.user_id == user.id,
            NutritionProjectionHead.local_date == dates[1],
        )
    ) is None

    retry = refresh_stale_nutrition_projections(
        session_factory=SessionLocal,
        user_id=user.id,
        expected_version=1,
    )
    assert retry.processed_count == 2
    assert calls == [dates[0], dates[1], dates[1], dates[2]]


def test_refresh_accepts_advanced_metric_specific_policy_without_flattening(
    db, user: User
) -> None:
    policy_snapshot = create_policy_with_rules(
        db,
        user.id,
        1,
        datetime.now(UTC) - timedelta(minutes=1),
        (PriorityRuleSpec("nutrition", "dietary_energy_kcal", "yazio", 1),),
    )
    connection = _yazio_connection(db, user)
    policy = db.get(SourcePriorityPolicy, policy_snapshot.policy_id)
    assert policy is not None
    ingest_yazio_food_diary(
        db,
        user_id=user.id,
        source_instance_id=connection.id,
        requested_start=DAY,
        requested_end=DAY,
        diary=YazioFoodDiary(
            requested_start_day=DAY,
            requested_end_day=DAY,
            consumed_products=(),
            consumed_simple_products=(),
            product_profiles=(),
            daily_summaries=(
                YazioDailyNutrientSummary(
                    local_date=DAY,
                    nutrients=YazioNutrientValues(
                        energy=Decimal("2100"),
                        protein=Decimal("120"),
                        carb=Decimal("230"),
                        fat=Decimal("70"),
                        fiber=Decimal("30"),
                        sugar=Decimal("50"),
                        saturated_fat=Decimal("20"),
                    ),
                    energy_goal=None,
                ),
            ),
        ),
    )
    db.commit()

    result = refresh_stale_nutrition_projections(
        session_factory=SessionLocal,
        user_id=user.id,
        expected_version=1,
    )

    assert result.processed_count == 1
    assert result.created_count == 1
    assert result.unchanged_count == 0
    head = db.get(NutritionProjectionHead, (user.id, DAY))
    assert head is not None
    projection = db.get(NutritionDailyProjection, head.current_projection_id)
    assert projection is not None
    facts = db.scalars(
        select(NutritionDailyProjectionFact).where(
            NutritionDailyProjectionFact.projection_id == projection.id,
        )
    ).all()
    assert len(facts) == 7
    assert {
        fact.metric_key: fact.value
        for fact in facts
        if fact.value is not None
    } == {"dietary_energy_kcal": Decimal("2100")}
    assert projection.priority_policy_id == policy.id


def test_refresh_rebuilds_same_evidence_under_new_provider_policy(
    db, user: User, monkeypatch
) -> None:
    v1_snapshot = create_policy_with_rules(
        db,
        user.id,
        1,
        datetime.now(UTC) - timedelta(minutes=2),
        (
            PriorityRuleSpec("nutrition", None, "yazio", 1),
            PriorityRuleSpec("nutrition", None, "google_health", 2),
        ),
    )
    v2_snapshot = create_policy_with_rules(
        db,
        user.id,
        2,
        datetime.now(UTC) - timedelta(minutes=1),
        (
            PriorityRuleSpec("nutrition", None, "google_health", 1),
            PriorityRuleSpec("nutrition", None, "yazio", 2),
        ),
    )
    _yazio_connection(db, user)
    _google_connection(db, user)
    db.commit()
    v1 = db.get(SourcePriorityPolicy, v1_snapshot.policy_id)
    v2 = db.get(SourcePriorityPolicy, v2_snapshot.policy_id)
    assert v1 is not None and v2 is not None
    source = _observation(db, user.id, _run(db, user.id), key="same-value", local_date=DAY)
    _event(db, user.id, source, key="same-value", local_date=DAY)
    _head(db, user.id, DAY, _projection(db, user.id, v1, DAY))
    db.commit()

    from app.nutrition.projection import refresh as refresh_module

    def rebuild_with_new_lineage(
        session,
        *,
        user_id,
        local_date,
        policy_at,
    ):
        return ProjectionPersistenceResult(
            user_id=user_id,
            local_date=local_date,
            projection_id=uuid4(),
            projection_version=2,
            input_watermark="new-policy",
            created=True,
            status=ProjectionPersistenceStatus.CREATED,
        )

    monkeypatch.setattr(refresh_module, "rebuild_nutrition_day", rebuild_with_new_lineage)
    result = refresh_stale_nutrition_projections(
        session_factory=SessionLocal,
        user_id=user.id,
        expected_version=2,
    )

    assert result.policy_version == 2
    assert result.processed_count == 1
    assert result.created_count == 1
    assert result.unchanged_count == 0


def test_refresh_treats_policy_lineage_change_as_created_even_for_same_value(
    db, user: User, monkeypatch
) -> None:
    v1_snapshot = create_policy_with_rules(
        db,
        user.id,
        1,
        datetime.now(UTC) - timedelta(minutes=2),
        (
            PriorityRuleSpec("nutrition", None, "yazio", 1),
            PriorityRuleSpec("nutrition", None, "google_health", 2),
        ),
    )
    v2_snapshot = create_policy_with_rules(
        db,
        user.id,
        2,
        datetime.now(UTC) - timedelta(minutes=1),
        (
            PriorityRuleSpec("nutrition", None, "google_health", 1),
            PriorityRuleSpec("nutrition", None, "yazio", 2),
        ),
    )
    _yazio_connection(db, user)
    _google_connection(db, user)
    db.commit()
    v1 = db.get(SourcePriorityPolicy, v1_snapshot.policy_id)
    v2 = db.get(SourcePriorityPolicy, v2_snapshot.policy_id)
    assert v1 is not None and v2 is not None
    _observation(db, user.id, _run(db, user.id), key="lineage", local_date=DAY)
    _head(db, user.id, DAY, _projection(db, user.id, v1, DAY))
    db.commit()

    from app.nutrition.projection import refresh as refresh_module

    def same_value_new_lineage(
        session,
        *,
        user_id,
        local_date,
        policy_at,
    ):
        return ProjectionPersistenceResult(
            user_id=user_id,
            local_date=local_date,

            projection_id=uuid4(),
            projection_version=2,
            input_watermark="lineage-changed",
            created=True,
            status=ProjectionPersistenceStatus.CREATED,
        )

    monkeypatch.setattr(refresh_module, "rebuild_nutrition_day", same_value_new_lineage)
    result = refresh_stale_nutrition_projections(
        session_factory=SessionLocal,
        user_id=user.id,
        expected_version=2,
    )
    assert result.created_count == 1
    assert result.unchanged_count == 0


def test_refresh_real_b6_rebuilds_cross_provider_policy_lineage_with_equal_values(
    db, user: User
) -> None:
    yazio = _yazio_connection(db, user)
    google = _google_connection(db, user)
    yazio_run = _run(db, user.id, provider_key="yazio", source_instance_id=yazio.id)
    google_run = _run(db, user.id, provider_key="google_health", source_instance_id=google.id)
    yazio_source = _observation(db, user.id, yazio_run, key="real-yazio", local_date=DAY)
    google_source = _observation(db, user.id, google_run, key="real-google", local_date=DAY)
    _event(db, user.id, yazio_source, key="real-yazio", local_date=DAY)
    _event(db, user.id, google_source, key="real-google", local_date=DAY)
    _field(db, user.id, yazio_source, value=Decimal("7"))
    _field(db, user.id, google_source, value=Decimal("7"))

    v1_snapshot = create_policy_with_rules(
        db,
        user.id,
        1,
        datetime.now(UTC) - timedelta(minutes=2),
        (
            PriorityRuleSpec("nutrition", None, "yazio", 1),
            PriorityRuleSpec("nutrition", None, "google_health", 2),
        ),
    )
    db.commit()

    from app.nutrition.projection.orchestration import rebuild_nutrition_day

    initial = rebuild_nutrition_day(
        db,
        user_id=user.id,
        local_date=DAY,
        policy_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    assert initial.status is ProjectionPersistenceStatus.CREATED
    db.expire_all()
    initial_fact = db.scalar(
        select(NutritionDailyProjectionFact).where(
            NutritionDailyProjectionFact.projection_id == initial.projection_id,
            NutritionDailyProjectionFact.metric_key == "protein_g",
        )
    )
    assert initial_fact is not None
    assert initial_fact.value == Decimal("7")
    initial_lineage = db.scalar(
        select(NutritionDailyProjectionLineage).where(
            NutritionDailyProjectionLineage.projection_fact_id == initial_fact.id,
            NutritionDailyProjectionLineage.role == "selected",
        )
    )
    assert initial_lineage is not None
    assert initial_lineage.provider_key == "yazio"

    v2_snapshot = create_policy_with_rules(
        db,
        user.id,
        2,
        datetime.now(UTC) - timedelta(seconds=1),
        (
            PriorityRuleSpec("nutrition", None, "google_health", 1),
            PriorityRuleSpec("nutrition", None, "yazio", 2),
        ),
    )
    db.commit()
    v2 = db.get(SourcePriorityPolicy, v2_snapshot.policy_id)
    assert v2 is not None

    result = refresh_stale_nutrition_projections(
        session_factory=SessionLocal,
        user_id=user.id,
        expected_version=2,
    )

    assert result.processed_count == 1
    assert result.created_count == 1
    db.expire_all()
    head = db.scalar(
        select(NutritionProjectionHead).where(
            NutritionProjectionHead.user_id == user.id,
            NutritionProjectionHead.local_date == DAY,
        )
    )
    assert head is not None
    assert head.current_projection_id != initial.projection_id
    current = db.get(NutritionDailyProjection, head.current_projection_id)
    assert current is not None
    assert current.priority_policy_id == v2.id

    facts = db.scalars(
        select(NutritionDailyProjectionFact).where(
            NutritionDailyProjectionFact.projection_id == current.id,
            NutritionDailyProjectionFact.metric_key == "protein_g",
        )
    ).all()
    assert len(facts) == 1
    assert facts[0].value == Decimal("7")
    lineages = db.scalars(
        select(NutritionDailyProjectionLineage).where(
            NutritionDailyProjectionLineage.projection_fact_id == facts[0].id,
        )
    ).all()
    assert [(lineage.provider_key, lineage.role) for lineage in lineages if lineage.role == "selected"] == [
        ("google_health", "selected")
    ]


def test_refresh_replaces_missing_and_old_heads_without_deleting_no_value_head(
    db, user: User
) -> None:
    v1_snapshot = create_policy_with_rules(
        db,
        user.id,
        1,
        datetime.now(UTC) - timedelta(minutes=2),
        (PriorityRuleSpec("nutrition", None, "yazio", 1),),
    )
    connection = _yazio_connection(db, user)
    old_date = DAY + timedelta(days=1)
    old_policy = db.get(SourcePriorityPolicy, v1_snapshot.policy_id)
    assert old_policy is not None
    old_projection = _projection(db, user.id, old_policy, old_date)
    _head(db, user.id, old_date, old_projection)

    missing_date = DAY
    source = _observation(
        db,
        user.id,
        _run(db, user.id, source_instance_id=connection.id),
        key="missing-head-refresh",
        local_date=missing_date,
    )
    _event(db, user.id, source, key="missing-head-refresh", local_date=missing_date)
    _field(db, user.id, source, value=Decimal("12"))
    v2_snapshot = create_policy_with_rules(
        db,
        user.id,
        2,
        datetime.now(UTC) - timedelta(minutes=1),
        (PriorityRuleSpec("nutrition", None, "yazio", 1),),
    )
    db.commit()

    result = refresh_stale_nutrition_projections(
        session_factory=SessionLocal,
        user_id=user.id,
        expected_version=2,
    )

    assert result.processed_count == 2
    assert result.created_count == 2
    db.expire_all()
    current_head = db.get(NutritionProjectionHead, (user.id, old_date))
    assert current_head is not None
    assert current_head.current_projection_id != old_projection.id
    assert db.get(NutritionDailyProjection, old_projection.id) is not None
    current = db.get(NutritionDailyProjection, current_head.current_projection_id)
    assert current is not None
    assert current.priority_policy_id == v2_snapshot.policy_id

    no_value_day = read_canonical_nutrition_day(db, user.id, old_date)
    assert no_value_day.state is NutritionProjectionReadState.READY
    assert no_value_day.has_any_nutrition_value is False
    assert all(fact.value is None for fact in no_value_day.facts)
    missing_day = read_canonical_nutrition_day(db, user.id, DAY + timedelta(days=2))
    assert missing_day.state is NutritionProjectionReadState.NOT_PROJECTED

    missing_head = db.get(NutritionProjectionHead, (user.id, missing_date))
    assert missing_head is not None
    missing_projection = db.get(NutritionDailyProjection, missing_head.current_projection_id)
    assert missing_projection is not None
    missing_fact = db.scalar(
        select(NutritionDailyProjectionFact).where(
            NutritionDailyProjectionFact.projection_id == missing_projection.id,
            NutritionDailyProjectionFact.metric_key == "protein_g",
        )
    )
    assert missing_fact is not None
    assert missing_fact.value == Decimal("12")
    missing_lineage = db.scalar(
        select(NutritionDailyProjectionLineage).where(
            NutritionDailyProjectionLineage.projection_fact_id == missing_fact.id,
            NutritionDailyProjectionLineage.role == "selected",
        )
    )
    assert missing_lineage is not None
    assert missing_lineage.provider_key == "yazio"

    current.projection_status = "failed"
    db.commit()
    with pytest.raises(NutritionProjectionReadError, match="not READY"):
        read_canonical_nutrition_day(db, user.id, old_date)

def test_refresh_rejects_policy_without_a_matching_provider_connection(
    db, user: User
) -> None:
    _policy(db, user)
    with pytest.raises(NutritionProjectionRefreshError, match="usable effective"):
        refresh_stale_nutrition_projections(
            session_factory=SessionLocal,
            user_id=user.id,
            expected_version=1,
        )

def test_refresh_converts_unexpected_policy_missing_to_safe_error(
    db, user: User, monkeypatch
) -> None:
    _policy(db, user)
    _yazio_connection(db, user)
    _observation(db, user.id, _run(db, user.id), key="missing-policy", local_date=DAY)
    db.commit()

    from app.nutrition.projection import refresh as refresh_module

    def policy_missing(
        session,
        *,
        user_id,
        local_date,
        policy_at,
    ):
        return ProjectionPersistenceResult(
            user_id=user_id,
            local_date=local_date,
            projection_id=None,
            projection_version=None,
            input_watermark=None,
            created=False,
            status=ProjectionPersistenceStatus.POLICY_MISSING,
        )

    monkeypatch.setattr(refresh_module, "rebuild_nutrition_day", policy_missing)
    with pytest.raises(NutritionProjectionRefreshError, match="policy disappeared"):
        refresh_stale_nutrition_projections(
            session_factory=SessionLocal,
            user_id=user.id,
            expected_version=1,
        )

