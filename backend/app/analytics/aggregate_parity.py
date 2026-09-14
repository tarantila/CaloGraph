from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Final
from uuid import UUID

from sqlalchemy import select, union
from sqlalchemy.orm import Session

from app.analytics.daily_point_parity import (
    CanonicalDailyPointResult,
    CanonicalDailyPointResultState,
    DailyPointParity,
    DailyPointParityClassification,
    _build_canonical_daily_point_with_inputs,
    _compare_daily_point_results,
    _read_daily_point_range_inputs,
)
from app.analytics.nutrition_parity import (
    MAX_PARITY_DAYS,
    _compare_nutrition_day_with_legacy,
    _empty_legacy_nutrition_day,
)
from app.analytics.service import (
    PRIMARY_NUTRITION_METRICS,
    _build_daily_point,
    _legacy_tracking_inputs,
    budget_balance,
    budget_balance_for_user,
    budget_classification,
    daily_points,
    moving_average,
)
from app.models import HealthSample, User
from app.nutrition.models import (
    NutritionConsumptionEvent,
    NutritionProjectionHead,
    NutritionSourceObservation,
)
from app.schemas import DailyPoint

CANONICAL_HISTORY_CHUNK_SIZE: Final[int] = 500


class MovingAverageParityClassification(StrEnum):
    MATCH = "match"
    BOTH_MISSING = "both_missing"
    EXPECTED_TRACKING_DIFFERENCE = "expected_tracking_difference"
    EXPECTED_NUTRITION_DIFFERENCE = "expected_nutrition_difference"
    UNEXPLAINED_MISMATCH = "unexplained_mismatch"
    NOT_COMPARABLE = "not_comparable"


class HistoricalBudgetBalanceClassification(StrEnum):
    MATCH = "match"
    EXPECTED_TRACKING_DIFFERENCE = "expected_tracking_difference"
    EXPECTED_NUTRITION_DIFFERENCE = "expected_nutrition_difference"
    LEGACY_ONLY_HISTORY = "legacy_only_history"
    UNEXPLAINED_MISMATCH = "unexplained_mismatch"


class DataQualityParityClassification(StrEnum):
    MATCH = "match"
    EXPECTED_TRACKING_DIFFERENCE = "expected_tracking_difference"
    EXPECTED_NUTRITION_DIFFERENCE = "expected_nutrition_difference"
    UNEXPLAINED_MISMATCH = "unexplained_mismatch"
    NOT_COMPARABLE = "not_comparable"


@dataclass(frozen=True, slots=True)
class MovingAverageParity:
    local_date: date
    window: int
    legacy_value: Decimal | None
    canonical_value: Decimal | None
    classification: MovingAverageParityClassification
    explanation: str | None = None


@dataclass(frozen=True, slots=True)
class MovingAverageRangeParity:
    start_date: date
    end_date: date
    results: tuple[MovingAverageParity, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "results", tuple(self.results))


@dataclass(frozen=True, slots=True)
class HistoricalBudgetBalanceParity:
    legacy_counts: Mapping[str, int]
    canonical_counts: Mapping[str, int]
    classification: HistoricalBudgetBalanceClassification

    def __post_init__(self) -> None:
        object.__setattr__(self, "legacy_counts", MappingProxyType(dict(self.legacy_counts)))
        object.__setattr__(self, "canonical_counts", MappingProxyType(dict(self.canonical_counts)))


@dataclass(frozen=True, slots=True)
class DataQualityParity:
    start_date: date
    end_date: date
    total_days: int
    recorded_days: int
    missing_days: int
    incomplete_days: int
    coverage_ratio: Decimal | None
    comparable_days: int
    comparable_coverage_ratio: Decimal | None
    not_comparable_days: int
    classification: DataQualityParityClassification


@dataclass(frozen=True, slots=True)
class CanonicalHistorySummary:
    data_start_date: date | None
    data_end_date: date | None
    data_day_count: int
    explicit_zero_days: int
    incomplete_days: int
    not_comparable_days: int


def _validate_date_bound(value: date | None, name: str) -> None:
    if value is not None and (not isinstance(value, date) or isinstance(value, datetime)):
        raise ValueError(f"{name} must be a date")


def _date_predicates(
    column: Any,
    *,
    start_date: date | None,
    end_date: date | None,
) -> list[Any]:
    predicates = [column.is_not(None)]
    if start_date is not None:
        predicates.append(column >= start_date)
    if end_date is not None:
        predicates.append(column <= end_date)
    return predicates


def iter_canonical_history_date_chunks(
    db: Session,
    user_id: UUID,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    chunk_size: int = CANONICAL_HISTORY_CHUNK_SIZE,
) -> Iterator[tuple[date, ...]]:
    """Yield the user's canonical nutrition history dates in bounded keyset pages.

    Dates are sourced from observations, consumption events, and projection heads.
    The SQL ``UNION`` removes duplicates before each page is ordered and limited;
    ``last_date`` then advances the keyset without loading the full history.
    """
    if type(chunk_size) is not int or not 1 <= chunk_size <= CANONICAL_HISTORY_CHUNK_SIZE:
        raise ValueError(f"chunk_size must be between 1 and {CANONICAL_HISTORY_CHUNK_SIZE}")
    _validate_date_bound(start_date, "start_date")
    _validate_date_bound(end_date, "end_date")
    if start_date is not None and end_date is not None and start_date > end_date:
        raise ValueError("start_date must not be after end_date")

    def _iterate() -> Iterator[tuple[date, ...]]:
        observation_dates = select(
            NutritionSourceObservation.local_date.label("local_date")
        ).where(
            NutritionSourceObservation.user_id == user_id,
            *_date_predicates(
                NutritionSourceObservation.local_date,
                start_date=start_date,
                end_date=end_date,
            ),
        )
        event_dates = select(NutritionConsumptionEvent.local_date.label("local_date")).where(
            NutritionConsumptionEvent.user_id == user_id,
            *_date_predicates(
                NutritionConsumptionEvent.local_date,
                start_date=start_date,
                end_date=end_date,
            ),
        )
        projection_head_dates = select(
            NutritionProjectionHead.local_date.label("local_date")
        ).where(
            NutritionProjectionHead.user_id == user_id,
            *_date_predicates(
                NutritionProjectionHead.local_date,
                start_date=start_date,
                end_date=end_date,
            ),
        )
        canonical_dates = union(
            observation_dates,
            event_dates,
            projection_head_dates,
        ).cte("canonical_history_dates")

        last_date: date | None = None
        while True:
            statement = select(canonical_dates.c.local_date).select_from(canonical_dates)
            if last_date is not None:
                statement = statement.where(canonical_dates.c.local_date > last_date)
            statement = statement.order_by(canonical_dates.c.local_date).limit(chunk_size)
            chunk = tuple(db.scalars(statement).all())
            if not chunk:
                return
            yield chunk
            last_date = chunk[-1]


    return _iterate()
_MOVING_AVERAGE_WINDOWS: Final[frozenset[int]] = frozenset({7, 14, 28})
_ELIGIBLE_TRACKING_STATUSES: Final[frozenset[str]] = frozenset(
    {"complete", "probably_complete"}
)


def _validate_moving_average_range(
    start_date: date,
    end_date: date,
    windows: tuple[int, ...],
) -> tuple[int, ...]:
    _validate_date_bound(start_date, "start_date")
    _validate_date_bound(end_date, "end_date")
    if not isinstance(start_date, date) or isinstance(start_date, datetime):
        raise ValueError("start_date must be a date")
    if not isinstance(end_date, date) or isinstance(end_date, datetime):
        raise ValueError("end_date must be a date")
    if start_date > end_date:
        raise ValueError("start_date must not be after end_date")
    if (end_date - start_date).days + 1 > MAX_PARITY_DAYS:
        raise ValueError(f"date range must not exceed {MAX_PARITY_DAYS} days")
    try:
        validated_windows = tuple(windows)
    except TypeError as exc:
        raise ValueError("windows must contain only 7, 14, and 28") from exc
    if not validated_windows:
        raise ValueError("windows must not be empty")
    if any(
        type(window) is not int or window not in _MOVING_AVERAGE_WINDOWS
        for window in validated_windows
    ):
        raise ValueError("windows must contain only 7, 14, and 28")
    return validated_windows


def _calendar_dates(start_date: date, end_date: date) -> tuple[date, ...]:
    return tuple(
        start_date + timedelta(days=offset)
        for offset in range((end_date - start_date).days + 1)
    )


def _include_incomplete_points(points: list[DailyPoint]) -> list[DailyPoint]:
    """Make the legacy trends ``include_incomplete`` behavior read-only."""
    return [
        point.model_copy(update={"tracking_status": "complete"})
        if point.calories_kcal is not None
        else point
        for point in points
    ]


def _point_is_average_eligible(point: DailyPoint, include_incomplete: bool) -> bool:
    if point.calories_kcal is None:
        return False
    return include_incomplete or point.tracking_status in _ELIGIBLE_TRACKING_STATUSES


def _window_sum_count(
    points_by_date: dict[date, DailyPoint],
    window_dates: tuple[date, ...],
    *,
    include_incomplete: bool,
    calories_from: dict[date, Decimal | None] | None = None,
    status_from: dict[date, str] | None = None,
) -> tuple[Decimal, int]:
    total = Decimal()
    count = 0
    for local_date in window_dates:
        point = points_by_date[local_date]
        calories = (
            calories_from[local_date]
            if calories_from is not None
            else point.calories_kcal
        )
        if calories is None:
            continue
        if status_from is None:
            eligible = include_incomplete or point.tracking_status in _ELIGIBLE_TRACKING_STATUSES
        else:
            eligible = include_incomplete or status_from[local_date] in _ELIGIBLE_TRACKING_STATUSES
        if eligible:
            total += calories
            count += 1
    return total, count


def _window_average(
    points_by_date: dict[date, DailyPoint],
    window_dates: tuple[date, ...],
    *,
    include_incomplete: bool,
    calories_from: dict[date, Decimal | None] | None = None,
    status_from: dict[date, str] | None = None,
) -> Decimal | None:
    total, count = _window_sum_count(
        points_by_date,
        window_dates,
        include_incomplete=include_incomplete,
        calories_from=calories_from,
        status_from=status_from,
    )
    return total / count if count else None


def _daily_nutrition_cause(parity: DailyPointParity) -> bool:
    return any(
        difference.field_name == "calories_kcal"
        and difference.classification
        in {
            DailyPointParityClassification.EXPLICIT_ZERO_SEMANTIC_DIFFERENCE,
            DailyPointParityClassification.NUTRITION_VALUE_SEMANTIC_DIFFERENCE,
        }
        for difference in parity.field_differences
    )

def _daily_tracking_cause(parity: DailyPointParity) -> bool:
    return (
        parity.tracking.classification
        is DailyPointParityClassification.CANONICAL_QUALITY_DIFFERENCE
    )


def _cause_accounts_for_window(
    *,
    cause_dates: set[date],
    legacy_points_by_date: dict[date, DailyPoint],
    canonical_points_by_date: dict[date, DailyPoint],
    window_dates: tuple[date, ...],
    include_incomplete: bool,
    legacy_value: Decimal | None,
    canonical_value: Decimal | None,
    legacy_sum: Decimal,
    legacy_count: int,
    canonical_sum: Decimal,
    canonical_count: int,
    nutrition_cause: bool,
) -> bool:
    if not cause_dates:
        return False
    relevant_causes = cause_dates.intersection(window_dates)
    if not relevant_causes:
        return False

    for local_date in window_dates:
        legacy = legacy_points_by_date[local_date]
        canonical = canonical_points_by_date[local_date]
        if legacy.calories_kcal != canonical.calories_kcal and (
            nutrition_cause is False or local_date not in relevant_causes
        ):
            return False
        legacy_eligible = _point_is_average_eligible(legacy, include_incomplete)
        canonical_eligible = _point_is_average_eligible(canonical, include_incomplete)
        if legacy_eligible != canonical_eligible and local_date not in relevant_causes:
            return False

    if nutrition_cause:
        adjusted_sum, adjusted_count = _window_sum_count(
            canonical_points_by_date,
            window_dates,
            include_incomplete=include_incomplete,
            calories_from={
                local_date: legacy_points_by_date[local_date].calories_kcal
                if local_date in relevant_causes
                else canonical_points_by_date[local_date].calories_kcal
                for local_date in window_dates
            },
        )
    else:
        adjusted_sum, adjusted_count = _window_sum_count(
            canonical_points_by_date,
            window_dates,
            include_incomplete=include_incomplete,
            status_from={
                local_date: legacy_points_by_date[local_date].tracking_status
                if local_date in relevant_causes
                else canonical_points_by_date[local_date].tracking_status
                for local_date in window_dates
            },
        )
    actual_difference = (
        canonical_value != legacy_value
        or canonical_sum != legacy_sum
        or canonical_count != legacy_count
    )
    return (
        actual_difference
        and adjusted_sum == legacy_sum
        and adjusted_count == legacy_count
    )


def compare_moving_average_range(
    db: Session,
    user_id: UUID,
    start_date: date,
    end_date: date,
    *,
    windows: tuple[int, ...] = (7, 14, 28),
    include_incomplete: bool = False,
) -> MovingAverageRangeParity:
    """Compare legacy and canonical moving averages over an inclusive range."""
    windows = _validate_moving_average_range(start_date, end_date, windows)
    user = db.get(User, user_id)
    if user is None:
        raise ValueError("user not found")

    max_window = max(windows)
    expanded_start = start_date - timedelta(days=max_window - 1)
    expanded_dates = _calendar_dates(expanded_start, end_date)
    legacy_points = daily_points(db, user, expanded_start, end_date)
    legacy_points_by_date = {point.date: point for point in legacy_points}
    legacy_index_by_date = {point.date: index for index, point in enumerate(legacy_points)}
    legacy_average_points = (
        _include_incomplete_points(legacy_points) if include_incomplete else legacy_points
    )

    (
        totals_by_date,
        nutrition_counts_by_date,
        active_energy_by_source,
        active_energy_sources_by_day,
        legacy_nutrition_days,
        targets,
        overrides,
    ) = _read_daily_point_range_inputs(
        db,
        user_id=user_id,
        start=expanded_start,
        end=end_date,
    )

    canonical_points_by_date: dict[date, DailyPoint] = {}
    canonical_parity_by_date: dict[date, DailyPointParity] = {}
    canonical_comparable_dates: set[date] = set()
    for local_date in expanded_dates:
        canonical_result: CanonicalDailyPointResult = _build_canonical_daily_point_with_inputs(
            db,
            user_id,
            local_date,
            targets=targets,
            active_energy_by_source=active_energy_by_source,
            active_energy_sources_by_day=active_energy_sources_by_day,
            override=overrides.get(local_date),
        )
        if (
            canonical_result.state is not CanonicalDailyPointResultState.READY
            or canonical_result.point is None
        ):
            continue
        nutrition = _compare_nutrition_day_with_legacy(
            db,
            legacy_nutrition_days.get(
                local_date,
                _empty_legacy_nutrition_day(local_date),
            ),
            user_id=user_id,
        )
        parity = _compare_daily_point_results(
            local_date=local_date,
            legacy=legacy_points_by_date[local_date],
            nutrition=nutrition,
            canonical_result=canonical_result,
        )
        if not parity.comparable:
            continue
        canonical_points_by_date[local_date] = canonical_result.point
        canonical_parity_by_date[local_date] = parity
        canonical_comparable_dates.add(local_date)

    canonical_points = [
        canonical_points_by_date[local_date]
        for local_date in expanded_dates
        if local_date in canonical_comparable_dates
    ]
    canonical_index_by_date = {point.date: index for index, point in enumerate(canonical_points)}
    canonical_average_points = (
        _include_incomplete_points(canonical_points)
        if include_incomplete
        else canonical_points
    )

    results: list[MovingAverageParity] = []
    for local_date in _calendar_dates(start_date, end_date):
        for window in windows:
            window_dates = _calendar_dates(
                local_date - timedelta(days=window - 1),
                local_date,
            )
            legacy_value = moving_average(
                legacy_average_points,
                window,
                legacy_index_by_date[local_date],
            )
            if not set(window_dates).issubset(canonical_comparable_dates):
                results.append(
                    MovingAverageParity(
                        local_date=local_date,
                        window=window,
                        legacy_value=legacy_value,
                        canonical_value=None,
                        classification=MovingAverageParityClassification.NOT_COMPARABLE,
                        explanation=(
                            "canonical daily point is not comparable in the exact "
                            "moving-average window"
                        ),
                    )
                )
                continue

            canonical_value = moving_average(
                canonical_average_points,
                window,
                canonical_index_by_date[local_date],
            )
            legacy_sum, legacy_count = _window_sum_count(
                legacy_points_by_date,
                window_dates,
                include_incomplete=include_incomplete,
            )
            canonical_sum, canonical_count = _window_sum_count(
                canonical_points_by_date,
                window_dates,
                include_incomplete=include_incomplete,
            )
            if (
                legacy_value == canonical_value
                and legacy_sum == canonical_sum
                and legacy_count == canonical_count
            ):
                classification = (
                    MovingAverageParityClassification.BOTH_MISSING
                    if legacy_value is None
                    else MovingAverageParityClassification.MATCH
                )
                results.append(
                    MovingAverageParity(
                        local_date=local_date,
                        window=window,
                        legacy_value=legacy_value,
                        canonical_value=canonical_value,
                        classification=classification,
                    )
                )
                continue

            nutrition_dates = {
                candidate_date
                for candidate_date in window_dates
                if _daily_nutrition_cause(canonical_parity_by_date[candidate_date])
            }
            tracking_dates = {
                candidate_date
                for candidate_date in window_dates
                if _daily_tracking_cause(canonical_parity_by_date[candidate_date])
            }
            if _cause_accounts_for_window(
                cause_dates=nutrition_dates,
                legacy_points_by_date=legacy_points_by_date,
                canonical_points_by_date=canonical_points_by_date,
                window_dates=window_dates,
                include_incomplete=include_incomplete,
                legacy_value=legacy_value,
                canonical_value=canonical_value,
                legacy_sum=legacy_sum,
                legacy_count=legacy_count,
                canonical_sum=canonical_sum,
                canonical_count=canonical_count,
                nutrition_cause=True,
            ):
                classification = MovingAverageParityClassification.EXPECTED_NUTRITION_DIFFERENCE
                explanation = (
                    "canonical nutrition value semantics explain the exact "
                    "moving-average window"
                )
            elif _cause_accounts_for_window(
                cause_dates=tracking_dates,
                legacy_points_by_date=legacy_points_by_date,
                canonical_points_by_date=canonical_points_by_date,
                window_dates=window_dates,
                include_incomplete=include_incomplete,
                legacy_value=legacy_value,
                canonical_value=canonical_value,
                legacy_sum=legacy_sum,
                legacy_count=legacy_count,
                canonical_sum=canonical_sum,
                canonical_count=canonical_count,
                nutrition_cause=False,
            ):
                classification = MovingAverageParityClassification.EXPECTED_TRACKING_DIFFERENCE
                explanation = (
                    "canonical tracking eligibility explains the exact "
                    "moving-average window"
                )
            else:
                classification = MovingAverageParityClassification.UNEXPLAINED_MISMATCH
                explanation = None
            results.append(
                MovingAverageParity(
                    local_date=local_date,
                    window=window,
                    legacy_value=legacy_value,
                    canonical_value=canonical_value,
                    classification=classification,
                    explanation=explanation,
                )
            )

    return MovingAverageRangeParity(
        start_date=start_date,
        end_date=end_date,
        results=tuple(results),
    )


def _has_legacy_only_budget_history(db: Session, user_id: UUID) -> bool:
    """Return whether a positive legacy budget candidate has no canonical date."""
    observation_date = select(NutritionSourceObservation.id).where(
        NutritionSourceObservation.user_id == user_id,
        NutritionSourceObservation.local_date == HealthSample.local_date,
    ).exists()
    event_date = select(NutritionConsumptionEvent.id).where(
        NutritionConsumptionEvent.user_id == user_id,
        NutritionConsumptionEvent.local_date == HealthSample.local_date,
    ).exists()
    projection_date = select(NutritionProjectionHead.local_date).where(
        NutritionProjectionHead.user_id == user_id,
        NutritionProjectionHead.local_date == HealthSample.local_date,
    ).exists()
    statement = (
        select(HealthSample.local_date)
        .where(
            HealthSample.user_id == user_id,
            HealthSample.metric_type.in_(PRIMARY_NUTRITION_METRICS),
            HealthSample.value > 0,
            ~(observation_date | event_date | projection_date),
        )
        .limit(1)
    )
    return db.scalar(statement) is not None


def _add_budget_point_counts(counts: dict[str, int], point: DailyPoint) -> None:
    tracked = point.tracking_status != "no_data"
    if tracked:
        counts["tracked_days"] += 1
    classification = budget_classification(point)
    if classification == "under_budget":
        counts["within_budget_days"] += 1
    elif classification == "over_budget":
        counts["over_budget_days"] += 1
    elif classification == "above_maintenance":
        counts["over_maintenance_days"] += 1


def _finalize_budget_point_counts(counts: dict[str, int]) -> dict[str, int]:
    return {
        **counts,
        "unclassified_budget_days": counts["tracked_days"]
        - (
            counts["within_budget_days"]
            + counts["over_budget_days"]
            + counts["over_maintenance_days"]
        ),
    }


def compare_historical_budget_balance(
    db: Session,
    user_id: UUID,
    *,
    chunk_size: int = CANONICAL_HISTORY_CHUNK_SIZE,
) -> HistoricalBudgetBalanceParity:
    """Compare all historical budget classifications without writing to the database."""
    user = db.get(User, user_id)
    if user is None:
        raise ValueError("user not found")

    history_chunks = iter_canonical_history_date_chunks(
        db,
        user_id,
        chunk_size=chunk_size,
    )
    legacy_counts = dict(budget_balance_for_user(db, user))
    canonical_counts = dict(budget_balance([]))
    has_tracking_cause = False
    has_nutrition_cause = False
    has_unexplained_cause = False
    has_non_comparable_legacy_candidate = False

    for date_chunk in history_chunks:
        (
            totals_by_date,
            nutrition_counts_by_date,
            active_energy_by_source,
            active_energy_sources_by_day,
            legacy_nutrition_days,
            targets,
            overrides,
        ) = _read_daily_point_range_inputs(
            db,
            user_id=user_id,
            start=date_chunk[0],
            end=date_chunk[-1],
        )
        for local_date in date_chunk:
            legacy = _build_daily_point(
                day=local_date,
                values=totals_by_date.get(local_date, {}),
                tracking_inputs=_legacy_tracking_inputs(
                    calories=totals_by_date.get(local_date, {}).get("dietary_energy_kcal"),
                    nutrition_count=nutrition_counts_by_date.get(local_date, 0),
                ),
                active_energy_by_source=active_energy_by_source,
                active_energy_sources_by_day=active_energy_sources_by_day,
                targets=targets,
                override=overrides.get(local_date),
            )
            canonical_result = _build_canonical_daily_point_with_inputs(
                db,
                user_id,
                local_date,
                targets=targets,
                active_energy_by_source=active_energy_by_source,
                active_energy_sources_by_day=active_energy_sources_by_day,
                override=overrides.get(local_date),
            )
            if (
                canonical_result.state is not CanonicalDailyPointResultState.READY
                or canonical_result.point is None
            ):
                has_non_comparable_legacy_candidate |= legacy.tracking_status != "no_data"
                continue

            _add_budget_point_counts(canonical_counts, canonical_result.point)
            nutrition = _compare_nutrition_day_with_legacy(
                db,
                legacy_nutrition_days.get(
                    local_date,
                    _empty_legacy_nutrition_day(local_date),
                ),
                user_id=user_id,
            )
            parity = _compare_daily_point_results(
                local_date=local_date,
                legacy=legacy,
                nutrition=nutrition,
                canonical_result=canonical_result,
            )
            if not parity.comparable:
                continue

            if parity.classification is DailyPointParityClassification.UNEXPLAINED_MISMATCH:
                if (
                    canonical_result.point.tracking_status == "incomplete"
                    and legacy.tracking_status == "complete"
                ):
                    has_tracking_cause = True
                else:
                    projection_only = any(
                        metric.classification.value == "projection_only"
                        for metric in parity.nutrition.metrics
                    )
                    if projection_only and legacy.tracking_status == "no_data":
                        has_nutrition_cause = True
                    else:
                        has_unexplained_cause = True
            elif parity.classification is DailyPointParityClassification.CANONICAL_QUALITY_DIFFERENCE:
                has_tracking_cause = True

            if any(
                difference.field_name
                in {
                    "target_kcal",
                    "maintenance_kcal",
                    "activity_mode",
                    "activity_source_type",
                    "active_energy_kcal",
                    "activity_credit_kcal",
                    "activity_data_status",
                }
                and difference.classification is DailyPointParityClassification.UNEXPLAINED_MISMATCH
                for difference in parity.fields.differences
            ):
                has_unexplained_cause = True

    canonical_counts = _finalize_budget_point_counts(canonical_counts)
    if legacy_counts == canonical_counts:
        classification = HistoricalBudgetBalanceClassification.MATCH
    elif has_unexplained_cause or has_non_comparable_legacy_candidate:
        classification = HistoricalBudgetBalanceClassification.UNEXPLAINED_MISMATCH
    elif has_nutrition_cause:
        classification = HistoricalBudgetBalanceClassification.EXPECTED_NUTRITION_DIFFERENCE
    elif has_tracking_cause:
        classification = HistoricalBudgetBalanceClassification.EXPECTED_TRACKING_DIFFERENCE
    elif _has_legacy_only_budget_history(db, user_id):
        classification = HistoricalBudgetBalanceClassification.LEGACY_ONLY_HISTORY
    elif canonical_counts["tracked_days"] != legacy_counts["tracked_days"]:
        classification = HistoricalBudgetBalanceClassification.EXPECTED_TRACKING_DIFFERENCE
    else:
        classification = HistoricalBudgetBalanceClassification.UNEXPLAINED_MISMATCH
    return HistoricalBudgetBalanceParity(
        legacy_counts=legacy_counts,
        canonical_counts=canonical_counts,
        classification=classification,
    )


__all__ = [
    "CANONICAL_HISTORY_CHUNK_SIZE",
    "CanonicalHistorySummary",
    "DataQualityParity",
    "DataQualityParityClassification",
    "HistoricalBudgetBalanceClassification",
    "HistoricalBudgetBalanceParity",
    "MovingAverageParity",
    "MovingAverageParityClassification",
    "MovingAverageRangeParity",
    "compare_historical_budget_balance",
    "compare_moving_average_range",
    "iter_canonical_history_date_chunks",
]
