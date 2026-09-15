from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import MappingProxyType
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.analytics.aggregate_parity as aggregate_module
from app.activity import ACTIVE_ENERGY_METRIC
from app.analytics.aggregate_parity import (
    CANONICAL_HISTORY_CHUNK_SIZE,
    CanonicalHistorySummary,
    DataQualityParity,
    DataQualityParityClassification,
    HistoricalBudgetBalanceClassification,
    HistoricalBudgetBalanceParity,
    MovingAverageParity,
    MovingAverageParityClassification,
    MovingAverageRangeParity,
    canonical_history_summary,
    compare_data_quality_range,
    compare_historical_budget_balance,
    compare_moving_average_range,
    iter_canonical_history_date_chunks,
)
from app.analytics.daily_point_parity import (
    CanonicalDailyPointReason,
    CanonicalDailyPointResult,
    CanonicalDailyPointResultState,
    DailyPointFieldDifference,
    DailyPointFieldParity,
    DailyPointParity,
    DailyPointParityClassification,
    DailyPointTrackingParity,
)
from app.analytics.nutrition_parity import NutritionDayParity
from app.analytics.service import TrackingInputs, _build_daily_point
from app.models import HealthSample, ImportBatch, NutritionTarget, TrackingOverride, User
from app.nutrition.enums import (
    CoverageState,
    LineageState,
    PresenceState,
    ProjectionGranularity,
    ResolutionState,
)
from app.nutrition.models import (
    NutritionConsumptionEvent,
    NutritionDailyProjection,
    NutritionDailyProjectionFact,
    NutritionIngestionRun,
    NutritionProjectionHead,
    NutritionSourceObservation,
)
from app.nutrition.resolution.metrics import CANONICAL_METRICS
from app.schemas import DailyPoint
from app.source_priority.application import create_policy_with_rules
from app.source_priority.contracts import PriorityRuleSpec

_DATE_0 = date(2025, 12, 31)
_DATE_1 = date(2026, 1, 1)
_DATE_2 = date(2026, 1, 2)
_DATE_3 = date(2026, 1, 3)
_DATE_4 = date(2026, 1, 4)
_DATE_5 = date(2026, 1, 5)


def _run(db: Session, user: User) -> NutritionIngestionRun:
    run = NutritionIngestionRun(
        user_id=user.id,
        provider_key="test-provider",
        source_instance_id=uuid4(),
        status="completed",
    )
    db.add(run)
    db.flush()
    return run


def _observation(
    db: Session,
    user: User,
    local_date: date | None,
    *,
    run: NutritionIngestionRun | None = None,
) -> NutritionSourceObservation:
    run = run or _run(db, user)
    observation = NutritionSourceObservation(
        user_id=user.id,
        ingestion_run_id=run.id,
        provider_key="test-provider",
        source_instance_id=run.source_instance_id,
        observation_kind="consumption_event",
        source_namespace="tests",
        source_record_id=None,
        source_revision=1,
        observation_fingerprint=(uuid4().hex + uuid4().hex)[:64],
        local_date=local_date,
        presence_state="supplied",
        coverage_state="complete",
        resolution_state="resolved",
        lineage_state="unknown",
    )
    db.add(observation)
    db.flush()
    return observation


def _event(db: Session, user: User, local_date: date, observation: NutritionSourceObservation) -> None:
    db.add(
        NutritionConsumptionEvent(
            user_id=user.id,
            source_observation_id=observation.id,
            provider_key="test-provider",
            source_instance_id=observation.source_instance_id,
            event_kind="product",
            logical_event_key=f"event-{uuid4()}",
            revision=1,
            local_date=local_date,
            presence_state="supplied",
            coverage_state="complete",
            resolution_state="resolved",
            lineage_state="unknown",
        )
    )
    db.flush()


def _projection_head(db: Session, user: User, local_date: date) -> None:
    policy = create_policy_with_rules(
        db,
        user.id,
        version=1,
        effective_from=datetime(2025, 1, 1, tzinfo=UTC),
        rules=(
            PriorityRuleSpec(
                data_area="nutrition",
                metric_key=None,
                provider_key="yazio",
                priority_rank=1,
            ),
        ),
    )
    projection = NutritionDailyProjection(
        user_id=user.id,
        local_date=local_date,
        projection_version=1,
        projection_algorithm_version="nutrition-daily-v1",
        priority_policy_id=policy.policy_id,
        input_watermark="aggregate-parity-test",
        projection_status="ready",
    )
    db.add(projection)
    db.flush()
    db.add(
        NutritionProjectionHead(
            user_id=user.id,
            local_date=local_date,
            current_projection_id=projection.id,
        )
    )
    db.flush()


def _legacy_sample(
    db: Session,
    user: User,
    local_date: date,
    *,
    metric_type: str = "dietary_energy_kcal",
    value: Decimal = Decimal("1900"),
    source_type: str = "yazio",
) -> HealthSample:
    batch = ImportBatch(user_id=user.id, source_type=source_type, status="completed")
    db.add(batch)
    db.flush()
    sample = HealthSample(
        user_id=user.id,
        import_batch_id=batch.id,
        external_sample_id=str(uuid4()),
        fingerprint=(uuid4().hex + uuid4().hex)[:64],
        source_type=source_type,
        source_identifier=str(uuid4()),
        metric_type=metric_type,
        value=value,
        unit=(
            "kcal"
            if metric_type in {"dietary_energy_kcal", ACTIVE_ENERGY_METRIC}
            else "g"
        ),
        original_value=value,
        original_unit=(
            "kcal"
            if metric_type in {"dietary_energy_kcal", ACTIVE_ENERGY_METRIC}
            else "g"
        ),
        start_at=datetime(local_date.year, local_date.month, local_date.day, tzinfo=UTC),
        end_at=datetime(local_date.year, local_date.month, local_date.day, 0, 1, tzinfo=UTC),
        local_date=local_date,
        timezone="UTC",
    )
    db.add(sample)
    db.flush()
    return sample


def _canonical_projection(
    db: Session,
    user: User,
    policy: object,
    local_date: date,
    *,
    calories: Decimal | None = Decimal("1900"),
    protein: Decimal | None = Decimal("1"),
    calorie_presence: str | None = None,
    calorie_coverage: str = CoverageState.COMPLETE.value,
    calorie_resolution: str = ResolutionState.RESOLVED.value,
) -> NutritionDailyProjection:
    projection = NutritionDailyProjection(
        user_id=user.id,
        local_date=local_date,
        projection_version=1,
        projection_algorithm_version="nutrition-daily-v1",
        priority_policy_id=policy.policy_id,
        input_watermark="aggregate-parity-test",
        projection_status="ready",
    )
    db.add(projection)
    db.flush()
    for metric_key, definition in CANONICAL_METRICS.items():
        value = (
            calories
            if metric_key == "dietary_energy_kcal"
            else protein
            if metric_key == "protein_g"
            else Decimal("1")
        )
        if value is None:
            presence_state = PresenceState.MISSING.value
            coverage_state = (
                calorie_coverage
                if metric_key == "dietary_energy_kcal"
                else CoverageState.UNKNOWN.value
            )
            resolution_state = (
                calorie_resolution
                if metric_key == "dietary_energy_kcal"
                else ResolutionState.UNRESOLVED.value
            )
            provider = None
            granularity = None
        else:
            presence_state = (
                calorie_presence
                or (
                    PresenceState.EXPLICIT_ZERO.value
                    if value == Decimal("0")
                    else PresenceState.SUPPLIED.value
                )
            )
            coverage_state = CoverageState.COMPLETE.value
            resolution_state = ResolutionState.RESOLVED.value
            provider = "yazio"
            granularity = ProjectionGranularity.SUMMARY.value
        db.add(
            NutritionDailyProjectionFact(
                user_id=user.id,
                projection_id=projection.id,
                metric_key=metric_key,
                value=value,
                unit=definition.canonical_unit,
                selected_provider_key=provider,
                selected_granularity=granularity,
                presence_state=presence_state,
                coverage_state=coverage_state,
                resolution_state=resolution_state,
                lineage_state=LineageState.CONFIRMED.value if value is not None else LineageState.UNKNOWN.value,
            )
        )
    db.add(
        NutritionProjectionHead(
            user_id=user.id,
            local_date=local_date,
            current_projection_id=projection.id,
        )
    )
    db.flush()
    return projection


def test_canonical_history_union_is_scoped_bounded_and_keyset_paginated(
    db: Session, user: User
) -> None:
    assert CANONICAL_HISTORY_CHUNK_SIZE == 500
    run = _run(db, user)
    _observation(db, user, _DATE_0, run=run)  # observation-only date
    observation = _observation(db, user, _DATE_1, run=run)
    _observation(db, user, None, run=run)  # undated observation excluded
    _event(db, user, _DATE_2, observation)  # event-only date
    _projection_head(db, user, _DATE_3)  # projection-only date
    _observation(db, user, _DATE_4, run=run)  # outside the upper bound
    _event(db, user, _DATE_1, observation)  # duplicate date across sources

    other = User(username="aggregate-parity-other", password_hash="synthetic-password-hash")
    db.add(other)
    db.flush()
    other_run = _run(db, other)
    other_observation = _observation(db, other, _DATE_5, run=other_run)
    _event(db, other, _DATE_5, other_observation)
    _projection_head(db, other, _DATE_5)

    all_chunks = tuple(
        iter_canonical_history_date_chunks(
            db,
            user.id,
            chunk_size=2,
        )
    )
    assert all_chunks == ((_DATE_0, _DATE_1), (_DATE_2, _DATE_3), (_DATE_4,))

    bounded_chunks = tuple(
        iter_canonical_history_date_chunks(
            db,
            user.id,
            start_date=_DATE_1,
            end_date=_DATE_3,
            chunk_size=2,
        )
    )
    assert bounded_chunks == ((_DATE_1, _DATE_2), (_DATE_3,))
    assert all(len(chunk) <= 2 for chunk in all_chunks)


def test_canonical_history_empty_result_is_safe(db: Session, user: User) -> None:
    assert tuple(iter_canonical_history_date_chunks(db, user.id)) == ()


@pytest.mark.parametrize("chunk_size", (0, -1, True, 1.5))
def test_canonical_history_rejects_invalid_chunk_sizes(
    db: Session, user: User, chunk_size: object
) -> None:
    with pytest.raises(ValueError):
        iter_canonical_history_date_chunks(db, user.id, chunk_size=chunk_size)  # type: ignore[arg-type]


def test_canonical_history_rejects_chunks_above_bounded_maximum(
    db: Session, user: User
) -> None:
    with pytest.raises(ValueError):
        iter_canonical_history_date_chunks(
            db,
            user.id,
            chunk_size=CANONICAL_HISTORY_CHUNK_SIZE + 1,
        )


def test_canonical_history_rejects_reversed_or_invalid_date_bounds(
    db: Session, user: User
) -> None:
    with pytest.raises(ValueError):
        iter_canonical_history_date_chunks(db, user.id, start_date=_DATE_2, end_date=_DATE_1)
    with pytest.raises(ValueError):
        iter_canonical_history_date_chunks(
            db,
            user.id,
            start_date=datetime(2026, 1, 1),  # type: ignore[arg-type]
        )


def test_aggregate_contracts_are_frozen_slotted_and_identifier_free() -> None:
    moving = MovingAverageParity(
        local_date=_DATE_1,
        window=7,
        legacy_value=Decimal("1"),
        canonical_value=Decimal("2"),
        classification=MovingAverageParityClassification.UNEXPLAINED_MISMATCH,
    )
    moving_range = MovingAverageRangeParity(_DATE_1, _DATE_3, (moving,))
    budget = HistoricalBudgetBalanceParity(
        legacy_counts={"tracked_days": 1},
        canonical_counts={"tracked_days": 2},
        classification=HistoricalBudgetBalanceClassification.UNEXPLAINED_MISMATCH,
    )
    quality = DataQualityParity(
        start_date=_DATE_1,
        end_date=_DATE_3,
        total_days=3,
        recorded_days=2,
        missing_days=1,
        incomplete_days=0,
        coverage_ratio=Decimal("0.6666666667"),
        comparable_days=2,
        comparable_coverage_ratio=Decimal("1"),
        not_comparable_days=0,
        classification=DataQualityParityClassification.MATCH,
    )
    history = CanonicalHistorySummary(_DATE_1, _DATE_3, 3, 1, 1, 0)

    for contract in (moving, moving_range, budget, quality, history):
        assert contract.__dataclass_params__.frozen is True
        assert contract.__slots__
        assert "user_id" not in contract.__slots__

    assert isinstance(moving_range.results, tuple)
    assert isinstance(budget.legacy_counts, MappingProxyType)
    assert isinstance(budget.canonical_counts, MappingProxyType)
    with pytest.raises(TypeError):
        budget.legacy_counts["tracked_days"] = 4  # type: ignore[index]
    with pytest.raises(TypeError):
        moving_range.results[0] = moving  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        quality.total_days = 4  # type: ignore[misc]


def test_public_analytics_routes_do_not_import_internal_aggregate_module() -> None:
    import app.api.analytics as analytics_routes

    assert "aggregate_parity" not in inspect.getsource(analytics_routes)
def _synthetic_point(
    local_date: date,
    calories: Decimal | None,
    *,
    status: str = "complete",
) -> DailyPoint:
    values = {} if calories is None else {"dietary_energy_kcal": calories}
    return _build_daily_point(
        day=local_date,
        values=values,
        tracking_inputs=TrackingInputs(
            status=status,
            score=1 if status in {"complete", "probably_complete"} else 0,
            reasons=("synthetic",),
        ),
        active_energy_by_source={},
        active_energy_sources_by_day={},
        targets=[],
        override=None,
    )


def _stub_moving_average_inputs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    legacy_points: list[DailyPoint],
    canonical_points: dict[date, DailyPoint],
    not_comparable_dates: set[date] | None = None,
    parities: dict[date, DailyPointParity] | None = None,
) -> None:
    not_comparable_dates = not_comparable_dates or set()
    monkeypatch.setattr(
        aggregate_module,
        "daily_points",
        lambda db, user, start, end: list(legacy_points),
    )
    monkeypatch.setattr(
        aggregate_module,
        "_read_daily_point_range_inputs",
        lambda db, *, user_id, start, end: ({}, {}, {}, {}, {}, [], {}),
    )

    def build_canonical(
        db: Session,
        user_id: object,
        local_date: date,
        *,
        targets: list[object],
        active_energy_by_source: dict[tuple[date, str], Decimal],
        active_energy_sources_by_day: dict[date, set[str]],
        override: object,
    ) -> CanonicalDailyPointResult:
        if local_date in not_comparable_dates:
            return CanonicalDailyPointResult(
                state=CanonicalDailyPointResultState.NOT_COMPARABLE,
                reason=CanonicalDailyPointReason.NOT_PROJECTED,
            )
        return CanonicalDailyPointResult(
            state=CanonicalDailyPointResultState.READY,
            point=canonical_points[local_date],
        )

    monkeypatch.setattr(
        aggregate_module,
        "_build_canonical_daily_point_with_inputs",
        build_canonical,
    )
    monkeypatch.setattr(
        aggregate_module,
        "_compare_nutrition_day_with_legacy",
        lambda db, legacy_day, *, user_id: NutritionDayParity(
            local_date=legacy_day.local_date,
            projection_state=None,
            metrics=(),
            match_count=0,
            mismatch_count=0,
            expected_difference_count=0,
            comparable=True,
        ),
    )
    if parities is not None:
        original_compare = aggregate_module._compare_daily_point_results

        def compare_daily_point_results(**kwargs: object) -> DailyPointParity:
            local_date = kwargs["local_date"]
            if isinstance(local_date, date) and local_date in parities:
                return parities[local_date]
            return original_compare(**kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(
            aggregate_module,
            "_compare_daily_point_results",
            compare_daily_point_results,
        )


def _synthetic_parity(
    local_date: date,
    legacy: DailyPoint,
    canonical: DailyPoint,
    *,
    tracking_classification: DailyPointParityClassification = (
        DailyPointParityClassification.MATCH
    ),
    field_differences: tuple[DailyPointFieldDifference, ...] = (),
    field_classification: DailyPointParityClassification = DailyPointParityClassification.MATCH,
) -> DailyPointParity:
    tracking = DailyPointTrackingParity(
        local_date=local_date,
        legacy_status=legacy.tracking_status,
        canonical_status=canonical.tracking_status,
        legacy_score=legacy.tracking_score,
        canonical_score=canonical.tracking_score,
        legacy_reasons=tuple(legacy.tracking_reasons),
        canonical_reasons=tuple(canonical.tracking_reasons),
        comparable=True,
        classification=tracking_classification,
    )
    fields = DailyPointFieldParity(
        local_date=local_date,
        differences=field_differences,
        comparable=True,
        classification=field_classification,
    )
    nutrition = NutritionDayParity(
        local_date=local_date,
        projection_state=None,
        metrics=(),
        match_count=0,
        mismatch_count=0,
        expected_difference_count=0,
        comparable=True,
    )
    return DailyPointParity(
        local_date=local_date,
        tracking=tracking,
        fields=fields,
        nutrition=nutrition,
        comparable=True,
        classification=(
            field_classification
            if field_classification is not DailyPointParityClassification.MATCH
            else tracking_classification
        ),
    )


def test_compare_moving_average_range_uses_inclusive_calendar_windows_and_decimals(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = date(2026, 1, 10)
    dates = [target - timedelta(days=offset) for offset in range(27, -2, -1)]
    values = {
        local_date: (Decimal(local_date.day) / Decimal("10"))
        for local_date in dates
    }
    values[target - timedelta(days=2)] = None
    legacy_points = [
        _synthetic_point(local_date, values[local_date])
        for local_date in dates
    ]
    canonical_points = {
        point.date: point for point in legacy_points
    }
    _stub_moving_average_inputs(
        monkeypatch,
        legacy_points=legacy_points,
        canonical_points=canonical_points,
    )

    result = compare_moving_average_range(
        db,
        user.id,
        target,
        target,
    )

    assert tuple((item.local_date, item.window) for item in result.results) == (
        (target, 7),
        (target, 14),
        (target, 28),
    )
    for item in result.results:
        assert item.classification is MovingAverageParityClassification.MATCH
        assert item.legacy_value == item.canonical_value
        assert isinstance(item.legacy_value, Decimal)
    assert result.results[0].legacy_value == Decimal("0.6833333333333333333333333333")
    assert result.results[1].legacy_value == Decimal("1.269230769230769230769230769")
    assert result.results[2].legacy_value == Decimal("1.674074074074074074074074074")


def test_compare_moving_average_range_include_incomplete_is_immutable(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = date(2026, 1, 10)
    dates = [target - timedelta(days=offset) for offset in range(6, -1, -1)]
    legacy_points = [
        _synthetic_point(
            local_date,
            Decimal("100") if local_date < target else Decimal("200"),
            status="incomplete" if local_date < target else "complete",
        )
        for local_date in dates
    ]
    canonical_points = {point.date: point for point in legacy_points}
    original_statuses = tuple(point.tracking_status for point in legacy_points)
    _stub_moving_average_inputs(
        monkeypatch,
        legacy_points=legacy_points,
        canonical_points=canonical_points,
    )

    normal = compare_moving_average_range(db, user.id, target, target, windows=(7,))
    included = compare_moving_average_range(
        db,
        user.id,
        target,
        target,
        windows=(7,),
        include_incomplete=True,
    )

    assert normal.results[0].legacy_value == Decimal("200")
    assert included.results[0].legacy_value == Decimal("114.2857142857142857142857143")
    assert included.results[0].canonical_value == included.results[0].legacy_value
    assert tuple(point.tracking_status for point in legacy_points) == original_statuses


def test_compare_moving_average_range_does_not_match_equal_means_with_different_counts(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = date(2026, 1, 10)
    dates = [target - timedelta(days=offset) for offset in range(6, -1, -1)]
    legacy_points = [
        _synthetic_point(
            local_date,
            Decimal("100") if local_date in {target - timedelta(days=1), target} else None,
        )
        for local_date in dates
    ]
    canonical_points = {
        point.date: (
            _synthetic_point(point.date, Decimal("100"))
            if point.date == target
            else _synthetic_point(point.date, None)
        )
        for point in legacy_points
    }
    _stub_moving_average_inputs(
        monkeypatch,
        legacy_points=legacy_points,
        canonical_points=canonical_points,
    )

    result = compare_moving_average_range(db, user.id, target, target, windows=(7,))

    assert result.results[0].legacy_value == Decimal("100")
    assert result.results[0].canonical_value == Decimal("100")
    assert result.results[0].classification is MovingAverageParityClassification.UNEXPLAINED_MISMATCH

def test_compare_moving_average_range_handles_missing_and_non_comparable_windows(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = date(2026, 1, 10)
    dates = [target - timedelta(days=offset) for offset in range(6, -1, -1)]
    legacy_points = [_synthetic_point(local_date, None) for local_date in dates]
    canonical_points = {point.date: point for point in legacy_points}
    _stub_moving_average_inputs(
        monkeypatch,
        legacy_points=legacy_points,
        canonical_points=canonical_points,
        not_comparable_dates={target - timedelta(days=1)},
    )

    result = compare_moving_average_range(db, user.id, target, target, windows=(7,))
    assert result.results[0].legacy_value is None
    assert result.results[0].canonical_value is None
    assert result.results[0].classification is MovingAverageParityClassification.NOT_COMPARABLE

    monkeypatch.setattr(
        aggregate_module,
        "_build_canonical_daily_point_with_inputs",
        lambda *args, **kwargs: CanonicalDailyPointResult(
            state=CanonicalDailyPointResultState.READY,
            point=canonical_points[args[2]],
        ),
    )
    both_missing = compare_moving_average_range(db, user.id, target, target, windows=(7,))
    assert both_missing.results[0].classification is MovingAverageParityClassification.BOTH_MISSING

def test_compare_moving_average_range_explains_only_exact_window_nutrition_cause(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = date(2026, 1, 10)
    cause_date = target - timedelta(days=10)
    dates = [target - timedelta(days=offset) for offset in range(13, -1, -1)]
    legacy_points = [
        _synthetic_point(
            local_date,
            Decimal("100") if local_date != cause_date else None,
            status="no_data" if local_date == cause_date else "complete",
        )
        for local_date in dates
    ]
    canonical_points = {
        point.date: (
            _synthetic_point(point.date, Decimal("0"))
            if point.date == cause_date
            else point
        )
        for point in legacy_points
    }
    parity = _synthetic_parity(
        cause_date,
        legacy_points[dates.index(cause_date)],
        canonical_points[cause_date],
        tracking_classification=DailyPointParityClassification.EXPLICIT_ZERO_SEMANTIC_DIFFERENCE,
        field_differences=(
            DailyPointFieldDifference(
                field_name="calories_kcal",
                legacy_value=None,
                canonical_value=Decimal("0"),
                classification=DailyPointParityClassification.EXPLICIT_ZERO_SEMANTIC_DIFFERENCE,
            ),
        ),
        field_classification=DailyPointParityClassification.EXPLICIT_ZERO_SEMANTIC_DIFFERENCE,
    )
    _stub_moving_average_inputs(
        monkeypatch,
        legacy_points=legacy_points,
        canonical_points=canonical_points,
        parities={cause_date: parity},
    )

    result = compare_moving_average_range(
        db,
        user.id,
        target,
        target,
        windows=(7, 14),
    )

    assert result.results[0].classification is MovingAverageParityClassification.MATCH
    assert result.results[1].classification is (
        MovingAverageParityClassification.EXPECTED_NUTRITION_DIFFERENCE
    )
    assert result.results[1].explanation is not None


def test_compare_moving_average_range_explains_tracking_quality_difference(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = date(2026, 1, 10)
    cause_date = target - timedelta(days=1)
    dates = [target - timedelta(days=offset) for offset in range(6, -1, -1)]
    legacy_points = [
        _synthetic_point(
            local_date,
            Decimal("200") if local_date == target else Decimal("100"),
        )
        for local_date in dates
    ]
    canonical_points = {
        point.date: (
            _synthetic_point(cause_date, Decimal("100"), status="incomplete")
            if point.date == cause_date
            else point
        )
        for point in legacy_points
    }
    parity = _synthetic_parity(
        cause_date,
        legacy_points[dates.index(cause_date)],
        canonical_points[cause_date],
        tracking_classification=DailyPointParityClassification.CANONICAL_QUALITY_DIFFERENCE,
    )
    _stub_moving_average_inputs(
        monkeypatch,
        legacy_points=legacy_points,
        canonical_points=canonical_points,
        parities={cause_date: parity},
    )

    result = compare_moving_average_range(db, user.id, target, target, windows=(7,))

    assert result.results[0].classification is (
        MovingAverageParityClassification.EXPECTED_TRACKING_DIFFERENCE
    )
    assert result.results[0].explanation is not None


def test_compare_moving_average_range_marks_unproven_difference_unexplained(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = date(2026, 1, 10)
    dates = [target - timedelta(days=offset) for offset in range(6, -1, -1)]
    legacy_points = [_synthetic_point(local_date, Decimal("100")) for local_date in dates]
    canonical_points = {
        point.date: (
            _synthetic_point(point.date, Decimal("101"))
            if point.date == target
            else point
        )
        for point in legacy_points
    }
    _stub_moving_average_inputs(
        monkeypatch,
        legacy_points=legacy_points,
        canonical_points=canonical_points,
    )

    result = compare_moving_average_range(db, user.id, target, target, windows=(7,))

    assert result.results[0].classification is MovingAverageParityClassification.UNEXPLAINED_MISMATCH
    assert result.results[0].explanation is None


@pytest.mark.parametrize(
    ("start_date", "end_date"),
    (
        (_DATE_2, _DATE_1),
        (date(2024, 1, 1), date(2025, 1, 1)),
    ),
)
def test_compare_moving_average_range_rejects_invalid_date_bounds(
    db: Session, user: User, start_date: date, end_date: date
) -> None:
    with pytest.raises(ValueError):
        compare_moving_average_range(db, user.id, start_date, end_date)


@pytest.mark.parametrize("windows", ((1,), (7, 21), (True,), ()))
def test_compare_moving_average_range_rejects_unsupported_windows(
    db: Session, user: User, windows: tuple[object, ...]
) -> None:
    with pytest.raises(ValueError):
        compare_moving_average_range(
            db,
            user.id,
            _DATE_1,
            _DATE_1,
            windows=windows,  # type: ignore[arg-type]
        )


def test_compare_historical_budget_balance_matches_legacy_counts(
    db: Session, user: User
) -> None:
    policy = create_policy_with_rules(
        db,
        user.id,
        version=1,
        effective_from=datetime(2025, 1, 1, tzinfo=UTC),
        rules=(
            PriorityRuleSpec(
                data_area="nutrition",
                metric_key=None,
                provider_key="yazio",
                priority_rank=1,
            ),
        ),
    )
    _legacy_sample(db, user, _DATE_1, value=Decimal("1900"))
    _canonical_projection(db, user, policy, _DATE_1, calories=Decimal("1900"))

    result = compare_historical_budget_balance(db, user.id)

    assert result.legacy_counts == {
        "tracked_days": 1,
        "within_budget_days": 1,
        "over_budget_days": 0,
        "over_maintenance_days": 0,
        "unclassified_budget_days": 0,
    }
    assert result.canonical_counts == result.legacy_counts
    assert result.classification is HistoricalBudgetBalanceClassification.MATCH
    with pytest.raises(TypeError):
        result.canonical_counts["tracked_days"] = 99  # type: ignore[index]


def _budget_policy(db: Session, user: User):
    return create_policy_with_rules(
        db,
        user.id,
        version=1,
        effective_from=datetime(2025, 1, 1, tzinfo=UTC),
        rules=(
            PriorityRuleSpec(
                data_area="nutrition",
                metric_key=None,
                provider_key="yazio",
                priority_rank=1,
            ),
        ),
    )


def test_historical_budget_balance_counts_all_budget_classifications(
    db: Session, user: User
) -> None:
    policy = _budget_policy(db, user)
    db.add(
        NutritionTarget(
            user_id=user.id,
            valid_from=date(2025, 1, 1),
            calories_kcal=Decimal("2000"),
            maintenance_kcal=Decimal("2500"),
            protein_g=Decimal("120"),
        )
    )
    dates_and_values = (
        (date(2023, 12, 31), Decimal("1900")),
        (date(2025, 1, 1), Decimal("1900")),
        (date(2025, 1, 2), Decimal("2200")),
        (date(2025, 1, 3), Decimal("3000")),
    )
    for local_date, calories in dates_and_values:
        _legacy_sample(db, user, local_date, value=calories)
        _canonical_projection(db, user, policy, local_date, calories=calories)

    result = compare_historical_budget_balance(db, user.id, chunk_size=2)

    expected = {
        "tracked_days": 4,
        "within_budget_days": 1,
        "over_budget_days": 1,
        "over_maintenance_days": 1,
        "unclassified_budget_days": 1,
    }
    assert result.legacy_counts == expected
    assert result.canonical_counts == expected
    assert result.classification is HistoricalBudgetBalanceClassification.MATCH


def test_historical_budget_balance_explicit_zero_is_expected_nutrition_difference(
    db: Session, user: User
) -> None:
    policy = _budget_policy(db, user)
    local_date = _DATE_1
    _legacy_sample(db, user, local_date, value=Decimal("0"))
    _canonical_projection(
        db,
        user,
        policy,
        local_date,
        calories=Decimal("0"),
        calorie_presence=PresenceState.EXPLICIT_ZERO.value,
    )

    result = compare_historical_budget_balance(db, user.id)

    assert result.legacy_counts == {
        "tracked_days": 0,
        "within_budget_days": 0,
        "over_budget_days": 0,
        "over_maintenance_days": 0,
        "unclassified_budget_days": 0,
    }
    assert result.canonical_counts == {
        "tracked_days": 1,
        "within_budget_days": 1,
        "over_budget_days": 0,
        "over_maintenance_days": 0,
        "unclassified_budget_days": 0,
    }
    assert result.classification is (
        HistoricalBudgetBalanceClassification.EXPECTED_NUTRITION_DIFFERENCE
    )


def test_historical_budget_balance_incomplete_canonical_day_is_expected_tracking_difference(
    db: Session, user: User
) -> None:
    policy = _budget_policy(db, user)
    local_date = _DATE_1
    _legacy_sample(db, user, local_date, value=Decimal("1900"))
    _canonical_projection(
        db,
        user,
        policy,
        local_date,
        calories=None,
        calorie_coverage=CoverageState.PARTIAL.value,
        calorie_resolution=ResolutionState.UNRESOLVED.value,
    )

    result = compare_historical_budget_balance(db, user.id)

    assert result.legacy_counts["within_budget_days"] == 1
    assert result.canonical_counts == {
        "tracked_days": 1,
        "within_budget_days": 0,
        "over_budget_days": 0,
        "over_maintenance_days": 0,
        "unclassified_budget_days": 1,
    }
    assert result.classification is (
        HistoricalBudgetBalanceClassification.EXPECTED_TRACKING_DIFFERENCE
    )


def test_historical_budget_balance_incomplete_value_mismatch_is_unexplained(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = _budget_policy(db, user)
    local_date = _DATE_1
    _legacy_sample(db, user, local_date, value=Decimal("1900"))
    _canonical_projection(db, user, policy, local_date, calories=Decimal("2200"))
    original_builder = aggregate_module._build_canonical_daily_point_with_inputs

    def incomplete_builder(*args: object, **kwargs: object) -> CanonicalDailyPointResult:
        result = original_builder(*args, **kwargs)
        assert result.point is not None
        return CanonicalDailyPointResult(
            state=CanonicalDailyPointResultState.READY,
            point=result.point.model_copy(
                update={
                    "tracking_status": "incomplete",
                    "tracking_score": 0,
                    "tracking_reasons": ["synthetic incomplete quality"],
                }
            ),
        )

    monkeypatch.setattr(
        aggregate_module,
        "_build_canonical_daily_point_with_inputs",
        incomplete_builder,
    )
    result = compare_historical_budget_balance(db, user.id)

    assert result.classification is HistoricalBudgetBalanceClassification.UNEXPLAINED_MISMATCH



def test_historical_budget_balance_unexplained_calorie_mismatch_wins_over_nutrition_cause(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = _budget_policy(db, user)
    local_date = _DATE_1
    db.add(
        NutritionTarget(
            user_id=user.id,
            valid_from=local_date,
            calories_kcal=2000,
            maintenance_kcal=2500,
            protein_g=120,
        )
    )
    _legacy_sample(db, user, local_date, value=Decimal("1900"))
    _canonical_projection(db, user, policy, local_date, calories=Decimal("2200"))
    original_compare = aggregate_module._compare_daily_point_results

    def compare_daily_point_results(**kwargs: object) -> DailyPointParity:
        parity = original_compare(**kwargs)  # type: ignore[arg-type]
        if kwargs["local_date"] != local_date:
            return parity
        fields = DailyPointFieldParity(
            local_date=local_date,
            differences=(
                DailyPointFieldDifference(
                    field_name="protein_g",
                    legacy_value=Decimal("100"),
                    canonical_value=Decimal("1"),
                    classification=(
                        DailyPointParityClassification.NUTRITION_VALUE_SEMANTIC_DIFFERENCE
                    ),
                ),
                DailyPointFieldDifference(
                    field_name="calories_kcal",
                    legacy_value=Decimal("1900"),
                    canonical_value=Decimal("2200"),
                    classification=DailyPointParityClassification.UNEXPLAINED_MISMATCH,
                ),
            ),
            comparable=True,
            classification=DailyPointParityClassification.UNEXPLAINED_MISMATCH,
        )
        return DailyPointParity(
            local_date=parity.local_date,
            tracking=parity.tracking,
            fields=fields,
            nutrition=parity.nutrition,
            comparable=True,
            classification=DailyPointParityClassification.UNEXPLAINED_MISMATCH,
        )

    monkeypatch.setattr(
        aggregate_module,
        "_compare_daily_point_results",
        compare_daily_point_results,
    )
    result = compare_historical_budget_balance(db, user.id)

    assert result.classification is HistoricalBudgetBalanceClassification.UNEXPLAINED_MISMATCH

def test_historical_budget_balance_legacy_only_wins_over_expected_nutrition_cause(
    db: Session, user: User
) -> None:
    policy = _budget_policy(db, user)
    legacy_only_date = _DATE_0
    expected_nutrition_date = _DATE_1
    db.add(
        NutritionTarget(
            user_id=user.id,
            valid_from=legacy_only_date,
            calories_kcal=2000,
            maintenance_kcal=2500,
            protein_g=120,
        )
    )
    _legacy_sample(db, user, legacy_only_date, value=Decimal("1900"))
    _legacy_sample(db, user, expected_nutrition_date, value=Decimal("1900"))
    _legacy_sample(
        db,
        user,
        expected_nutrition_date,
        metric_type="protein_g",
        value=Decimal("100"),
        source_type="yazio",
    )
    _legacy_sample(
        db,
        user,
        expected_nutrition_date,
        metric_type="protein_g",
        value=Decimal("20"),
        source_type="google_health",
    )
    _canonical_projection(
        db,
        user,
        policy,
        expected_nutrition_date,
        calories=Decimal("1900"),
        protein=Decimal("100"),
    )

    result = compare_historical_budget_balance(db, user.id)

    assert result.legacy_counts == {
        "tracked_days": 2,
        "within_budget_days": 2,
        "over_budget_days": 0,
        "over_maintenance_days": 0,
        "unclassified_budget_days": 0,
    }
    assert result.canonical_counts == {
        "tracked_days": 1,
        "within_budget_days": 1,
        "over_budget_days": 0,
        "over_maintenance_days": 0,
        "unclassified_budget_days": 0,
    }
    assert result.classification is HistoricalBudgetBalanceClassification.LEGACY_ONLY_HISTORY


def test_historical_budget_balance_uses_historical_target_and_activity_source(
    db: Session, user: User
) -> None:
    policy = _budget_policy(db, user)
    local_date = _DATE_1
    db.add(
        NutritionTarget(
            user_id=user.id,
            valid_from=local_date,
            calories_kcal=1800,
            maintenance_kcal=2500,
            protein_g=120,
            activity_mode="full",
            activity_source_type="apple_health_xml",
        )
    )
    _legacy_sample(db, user, local_date, value=Decimal("2000"))
    _legacy_sample(
        db,
        user,
        local_date,
        metric_type=ACTIVE_ENERGY_METRIC,
        value=Decimal("300"),
        source_type="apple_health_xml",
    )
    _canonical_projection(db, user, policy, local_date, calories=Decimal("2000"))

    result = compare_historical_budget_balance(db, user.id)

    assert result.legacy_counts == {
        "tracked_days": 1,
        "within_budget_days": 1,
        "over_budget_days": 0,
        "over_maintenance_days": 0,
        "unclassified_budget_days": 0,
    }
    assert result.canonical_counts == result.legacy_counts
    assert result.classification is HistoricalBudgetBalanceClassification.MATCH


def test_historical_budget_balance_event_and_projection_only_history_is_safe(
    db: Session, user: User
) -> None:
    run = _run(db, user)
    observation = _observation(db, user, _DATE_1, run=run)
    _event(db, user, _DATE_1, observation)
    _projection_head(db, user, _DATE_2)

    result = compare_historical_budget_balance(db, user.id)

    assert result.legacy_counts == result.canonical_counts == {
        "tracked_days": 0,
        "within_budget_days": 0,
        "over_budget_days": 0,
        "over_maintenance_days": 0,
        "unclassified_budget_days": 0,
    }
    assert result.classification is HistoricalBudgetBalanceClassification.MATCH


def test_historical_budget_balance_reports_legacy_only_history(
    db: Session, user: User
) -> None:
    _legacy_sample(db, user, _DATE_1, value=Decimal("1900"))

    result = compare_historical_budget_balance(db, user.id)

    assert result.legacy_counts["tracked_days"] == 1
    assert result.canonical_counts["tracked_days"] == 0
    assert result.classification is HistoricalBudgetBalanceClassification.LEGACY_ONLY_HISTORY


def test_historical_budget_balance_isolates_users(
    db: Session, user: User
) -> None:
    other = User(username="historical-budget-other", password_hash="synthetic-password-hash")
    db.add(other)
    db.flush()
    _legacy_sample(db, other, _DATE_1, value=Decimal("1900"))
    policy = _budget_policy(db, user)
    _canonical_projection(db, user, policy, _DATE_2, calories=Decimal("1900"))

    result = compare_historical_budget_balance(db, user.id)

    assert result.legacy_counts["tracked_days"] == 0
    assert result.canonical_counts["tracked_days"] == 1
    assert result.classification is HistoricalBudgetBalanceClassification.EXPECTED_NUTRITION_DIFFERENCE


def test_historical_budget_balance_processes_more_than_one_chunk(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = _budget_policy(db, user)
    start = date(2026, 1, 1)
    for offset in range(501):
        local_date = start + timedelta(days=offset * 2)
        _canonical_projection(db, user, policy, local_date, calories=Decimal("1900"))

    seen_chunk_sizes: list[int] = []
    original_iterator = aggregate_module.iter_canonical_history_date_chunks

    def recording_iterator(*args: object, **kwargs: object):
        for chunk in original_iterator(*args, **kwargs):
            seen_chunk_sizes.append(len(chunk))
            yield chunk
    seen_input_ranges: list[tuple[date, date]] = []
    original_loader = aggregate_module._read_daily_point_range_inputs

    def recording_loader(db, *, user_id, start, end):
        seen_input_ranges.append((start, end))
        return original_loader(db, user_id=user_id, start=start, end=end)

    monkeypatch.setattr(
        aggregate_module,
        "iter_canonical_history_date_chunks",
        recording_iterator,
    )
    monkeypatch.setattr(
        aggregate_module,
        "_read_daily_point_range_inputs",
        recording_loader,
    )
    result = compare_historical_budget_balance(db, user.id)

    assert seen_chunk_sizes == [500, 1]
    assert len(seen_input_ranges) == 501
    assert all(start == end for start, end in seen_input_ranges)
    assert result.canonical_counts["tracked_days"] == 501
    assert result.canonical_counts["within_budget_days"] == 501


def test_historical_budget_balance_is_read_only(
    db: Session, user: User
) -> None:
    policy = _budget_policy(db, user)
    local_date = _DATE_1
    _legacy_sample(db, user, local_date, value=Decimal("1900"))
    _canonical_projection(db, user, policy, local_date, calories=Decimal("1900"))

    def snapshot() -> dict[str, tuple[object, ...]]:
        return {
            "projections": tuple(db.scalars(select(NutritionDailyProjection.id)).all()),
            "facts": tuple(db.scalars(select(NutritionDailyProjectionFact.id)).all()),
            "heads": tuple(
                db.execute(
                    select(
                        NutritionProjectionHead.user_id,
                        NutritionProjectionHead.local_date,
                        NutritionProjectionHead.current_projection_id,
                    )
                ).all()
            ),
            "samples": tuple(db.scalars(select(HealthSample.id)).all()),
        }

    before = snapshot()
    compare_historical_budget_balance(db, user.id)
    assert snapshot() == before


def test_nonpositive_overridden_noncomparable_date_does_not_escalate_mismatch(
    db: Session, user: User
) -> None:
    policy = _budget_policy(db, user)
    non_comparable_date = _DATE_1
    explicit_zero_date = _DATE_2
    db.add(
        TrackingOverride(
            user_id=user.id,
            local_date=non_comparable_date,
            status="complete",
        )
    )
    projection = NutritionDailyProjection(
        user_id=user.id,
        local_date=non_comparable_date,
        projection_version=1,
        projection_algorithm_version="nutrition-daily-v1",
        priority_policy_id=policy.policy_id,
        input_watermark="aggregate-parity-test",
        projection_status="ready",
    )
    db.add(projection)
    db.flush()
    db.add(
        NutritionProjectionHead(
            user_id=user.id,
            local_date=non_comparable_date,
            current_projection_id=projection.id,
        )
    )
    _legacy_sample(db, user, explicit_zero_date, value=Decimal("0"))
    _canonical_projection(
        db,
        user,
        policy,
        explicit_zero_date,
        calories=Decimal("0"),
        calorie_presence=PresenceState.EXPLICIT_ZERO.value,
    )
    db.flush()

    result = compare_historical_budget_balance(db, user.id)

    assert result.classification is (
        HistoricalBudgetBalanceClassification.EXPECTED_NUTRITION_DIFFERENCE
    )


def test_compare_data_quality_range_preserves_calendar_and_comparable_denominators(
    db: Session, user: User
) -> None:
    policy = _budget_policy(db, user)
    outside_date = _DATE_0
    _legacy_sample(db, user, outside_date, value=Decimal("1900"))
    _canonical_projection(db, user, policy, outside_date, calories=Decimal("1900"))

    _legacy_sample(db, user, _DATE_1, value=Decimal("1900"))
    _legacy_sample(
        db,
        user,
        _DATE_3,
        metric_type="protein_g",
        value=Decimal("10"),
    )
    _canonical_projection(db, user, policy, _DATE_1, calories=Decimal("1900"))
    _canonical_projection(db, user, policy, _DATE_3, calories=Decimal("1900"))

    result = compare_data_quality_range(db, user.id, _DATE_1, _DATE_3)

    assert result.start_date == _DATE_1
    assert result.total_days == 3
    assert result.recorded_days == 2
    assert result.incomplete_days == 1
    assert result.coverage_ratio == Decimal(2) / Decimal(3)
    assert result.comparable_days == 2
    assert result.comparable_coverage_ratio == Decimal(1)
    assert result.not_comparable_days == 1
    assert result.classification is DataQualityParityClassification.NOT_COMPARABLE


def test_compare_data_quality_range_classifies_explicit_zero_as_nutrition_difference(
    db: Session, user: User
) -> None:
    policy = _budget_policy(db, user)
    _legacy_sample(db, user, _DATE_1, value=Decimal("0"))
    projection = _canonical_projection(db, user, policy, _DATE_1, calories=Decimal("0"))
    for fact in db.scalars(
        select(NutritionDailyProjectionFact).where(
            NutritionDailyProjectionFact.projection_id == projection.id,
            NutritionDailyProjectionFact.metric_key != "dietary_energy_kcal",
        )
    ):
        fact.value = None
        fact.selected_provider_key = None
        fact.selected_granularity = None
        fact.presence_state = PresenceState.MISSING.value
        fact.coverage_state = CoverageState.UNKNOWN.value
        fact.resolution_state = ResolutionState.UNRESOLVED.value
    db.flush()

    result = compare_data_quality_range(db, user.id, _DATE_1, _DATE_1)

    assert result.recorded_days == 0
    assert result.missing_days == 1
    assert result.incomplete_days == 0
    assert result.comparable_days == 1
    assert result.not_comparable_days == 0
    assert result.classification is DataQualityParityClassification.EXPECTED_NUTRITION_DIFFERENCE


def test_compare_data_quality_range_ignores_nutrition_only_value_difference(
    db: Session, user: User
) -> None:
    policy = _budget_policy(db, user)
    _legacy_sample(db, user, _DATE_1, value=Decimal("1900"))
    _canonical_projection(db, user, policy, _DATE_1, calories=Decimal("2000"))

    result = compare_data_quality_range(db, user.id, _DATE_1, _DATE_1)

    assert result.recorded_days == 1
    assert result.missing_days == 0
    assert result.incomplete_days == 0
    assert result.comparable_days == 1
    assert result.not_comparable_days == 0
    assert result.classification is DataQualityParityClassification.MATCH


def test_canonical_history_summary_counts_only_canonical_data_days(
    db: Session, user: User
) -> None:
    policy = _budget_policy(db, user)
    _legacy_sample(db, user, _DATE_0, value=Decimal("1900"))
    _canonical_projection(db, user, policy, _DATE_1, calories=Decimal("1900"))
    _canonical_projection(db, user, policy, _DATE_2, calories=Decimal("0"))
    _canonical_projection(db, user, policy, _DATE_3, calories=None)
    no_data_projection = _canonical_projection(db, user, policy, _DATE_4, calories=None)
    for fact in db.scalars(
        select(NutritionDailyProjectionFact).where(
            NutritionDailyProjectionFact.projection_id == no_data_projection.id,
        )
    ):
        fact.value = None
        fact.selected_provider_key = None
        fact.selected_granularity = None
        fact.presence_state = PresenceState.MISSING.value
        fact.coverage_state = CoverageState.UNKNOWN.value
        fact.resolution_state = ResolutionState.UNRESOLVED.value
    not_ready_projection = NutritionDailyProjection(
        user_id=user.id,
        local_date=_DATE_5,
        projection_version=1,
        projection_algorithm_version="nutrition-daily-v1",
        priority_policy_id=policy.policy_id,
        input_watermark="aggregate-parity-test",
        projection_status="ready",
    )
    db.add(not_ready_projection)
    db.flush()
    db.add(
        NutritionProjectionHead(
            user_id=user.id,
            local_date=_DATE_5,
            current_projection_id=not_ready_projection.id,
        )
    )
    db.flush()

    result = canonical_history_summary(db, user.id)

    assert result.data_start_date == _DATE_1
    assert result.data_end_date == _DATE_3
    assert result.data_day_count == 3
    assert result.explicit_zero_days == 1
    assert result.incomplete_days == 1
    assert result.not_comparable_days == 1


def test_canonical_history_summary_is_keyset_chunked_and_user_scoped(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = _budget_policy(db, user)
    start = date(2026, 2, 1)
    for offset in range(501):
        _canonical_projection(
            db,
            user,
            policy,
            start + timedelta(days=offset * 2),
            calories=Decimal("1900"),
        )
    other = User(username="history-summary-other", password_hash="synthetic-password-hash")
    db.add(other)
    db.flush()
    other_policy = _budget_policy(db, other)
    _canonical_projection(db, other, other_policy, date(2030, 1, 1), calories=Decimal("1900"))

    seen_chunk_sizes: list[int] = []
    original_iterator = aggregate_module.iter_canonical_history_date_chunks

    def recording_iterator(*args: object, **kwargs: object):
        for chunk in original_iterator(*args, **kwargs):
            seen_chunk_sizes.append(len(chunk))
            yield chunk

    monkeypatch.setattr(
        aggregate_module,
        "iter_canonical_history_date_chunks",
        recording_iterator,
    )

    result = canonical_history_summary(db, user.id)

    assert seen_chunk_sizes == [500, 1]
    assert result.data_start_date == start
    assert result.data_end_date == start + timedelta(days=1000)
    assert result.data_day_count == 501
    assert result.explicit_zero_days == 0
    assert result.incomplete_days == 0
    assert result.not_comparable_days == 0


@pytest.mark.parametrize(
    ("start_date", "end_date"),
    (
        (_DATE_2, _DATE_1),
        (datetime(2026, 1, 1), _DATE_1),
        (_DATE_1, datetime(2026, 1, 2)),
        (_DATE_1, _DATE_1 + timedelta(days=366)),
    ),
)
def test_compare_data_quality_range_rejects_invalid_or_unbounded_ranges(
    db: Session, user: User, start_date: date, end_date: date
) -> None:
    with pytest.raises(ValueError):
        compare_data_quality_range(db, user.id, start_date, end_date)
