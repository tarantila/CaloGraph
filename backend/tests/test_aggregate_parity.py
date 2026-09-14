from __future__ import annotations

import inspect
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import MappingProxyType
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import User
from app.nutrition.models import (
    NutritionConsumptionEvent,
    NutritionDailyProjection,
    NutritionIngestionRun,
    NutritionProjectionHead,
    NutritionSourceObservation,
)
import app.analytics.aggregate_parity as aggregate_module

from app.source_priority.application import create_policy_with_rules
from app.source_priority.contracts import PriorityRuleSpec
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
from app.schemas import DailyPoint


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
    with pytest.raises(Exception):
        moving_range.results += (moving,)  # type: ignore[misc]
    with pytest.raises(Exception):
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
