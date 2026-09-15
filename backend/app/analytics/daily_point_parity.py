from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.activity import ACTIVE_ENERGY_METRIC
from app.analytics.nutrition_parity import (
    CANONICAL_PARITY_METRICS,
    MAX_PARITY_DAYS,
    NutritionDayParity,
    NutritionLegacyDay,
    NutritionMetricParity,
    NutritionParityClassification,
    _compare_nutrition_day_with_legacy,
    _empty_legacy_nutrition_day,
    _legacy_metric,
    compare_nutrition_day,
)
from app.analytics.nutrition_projection import (
    CanonicalNutritionDay,
    NutritionProjectionReadError,
    NutritionProjectionReadState,
    read_canonical_nutrition_day,
)
from app.analytics.service import (
    NUTRITION_METRICS,
    PRIMARY_NUTRITION_METRICS,
    TrackingInputs,
    _build_daily_point,
    _legacy_tracking_inputs,
    daily_points,
)
from app.models import HealthSample, NutritionTarget, TrackingOverride, User
from app.nutrition.enums import CoverageState, ResolutionState
from app.schemas import DailyPoint


class CanonicalDailyPointResultState(StrEnum):
    READY = "ready"
    NOT_COMPARABLE = "not_comparable"


CanonicalDailyPointState = CanonicalDailyPointResultState


class CanonicalDailyPointReason(StrEnum):
    NOT_PROJECTED = "not_projected"
    PROJECTION_NOT_READY = "projection_not_ready"


@dataclass(frozen=True, slots=True)
class CanonicalDailyPointResult:
    state: CanonicalDailyPointResultState
    point: DailyPoint | None = None
    reason: CanonicalDailyPointReason | None = None
    projection_id: UUID | None = None
    projection_ready: bool = False
    calorie_usable: bool = False

    def __post_init__(self) -> None:
        if self.state is CanonicalDailyPointResultState.READY:
            if self.point is None or self.reason is not None:
                raise ValueError("READY canonical daily point results require only a point")
        elif self.state is CanonicalDailyPointResultState.NOT_COMPARABLE:
            if self.point is not None or self.reason is None:
                raise ValueError(
                    "NOT_COMPARABLE canonical daily point results require only a reason"
                )
        else:
            raise ValueError("unknown canonical daily point result state")

    @property
    def daily_point(self) -> DailyPoint | None:
        return self.point


_PRIMARY_DAILY_POINT_FIELDS: tuple[tuple[str, str], ...] = (
    ("dietary_energy_kcal", "dietary_energy_kcal"),
    ("protein_g", "protein_g"),
    ("carbohydrates_g", "carbohydrates_g"),
    ("fat_g", "fat_g"),
)
# Legacy HealthSample.value is Numeric(20,6), while canonical facts are
# Numeric(24,12). Normalize only trailing-zero exponent differences so the
# existing DailyPoint wire contract stays exact without changing values.
_LEGACY_DAILY_POINT_DECIMAL_QUANTUM = Decimal("0.000001")


def _legacy_daily_point_value(value: Decimal) -> Decimal:
    """Match the Legacy DailyPoint wire exponent without changing its value."""
    normalized = value.quantize(_LEGACY_DAILY_POINT_DECIMAL_QUANTUM)
    return normalized if normalized == value else value


def _not_comparable(reason: CanonicalDailyPointReason) -> CanonicalDailyPointResult:
    return CanonicalDailyPointResult(
        state=CanonicalDailyPointResultState.NOT_COMPARABLE,
        reason=reason,
    )


def _canonical_tracking_inputs(has_primary_evidence: bool, calorie_usable: bool) -> TrackingInputs:
    if not has_primary_evidence:
        return TrackingInputs(
            status="no_data",
            score=0,
            reasons=("Keine Ernährungsdaten vorhanden",),
        )
    if calorie_usable:
        return TrackingInputs(
            status="complete",
            score=1,
            reasons=("Kalorienwert vorhanden",),
        )
    return TrackingInputs(
        status="incomplete",
        score=0,
        reasons=("Ernährungsdaten vorhanden, aber kein Kalorienwert",),
    )


def _build_canonical_daily_point_from_projection(
    *,
    projection_day: CanonicalNutritionDay,
    local_date: date,
    targets: list[NutritionTarget],
    active_energy_by_source: dict[tuple[date, str], Decimal],
    active_energy_sources_by_day: dict[date, set[str]],
    override: TrackingOverride | None,
) -> CanonicalDailyPointResult:
    if projection_day.state is NutritionProjectionReadState.NOT_PROJECTED:
        return _not_comparable(CanonicalDailyPointReason.NOT_PROJECTED)
    if projection_day.state is not NutritionProjectionReadState.READY:
        return _not_comparable(CanonicalDailyPointReason.PROJECTION_NOT_READY)

    try:
        facts_by_metric = {fact.metric_key: fact for fact in projection_day.facts}
        values = {
            daily_point_field: _legacy_daily_point_value(fact.value)
            for metric_key, daily_point_field in _PRIMARY_DAILY_POINT_FIELDS
            if (fact := facts_by_metric[metric_key]).value is not None
        }
    except KeyError, AttributeError, TypeError:
        return _not_comparable(CanonicalDailyPointReason.PROJECTION_NOT_READY)

    point = _build_daily_point(
        day=local_date,
        values=values,
        tracking_inputs=_canonical_tracking_inputs(
            projection_day.has_primary_evidence,
            projection_day.calorie_usable,
        ),
        active_energy_by_source=active_energy_by_source,
        active_energy_sources_by_day=active_energy_sources_by_day,
        targets=targets,
        override=override,
    )
    return CanonicalDailyPointResult(
        state=CanonicalDailyPointResultState.READY,
        point=point,
        projection_id=projection_day.projection_id,
        projection_ready=True,
        calorie_usable=projection_day.calorie_usable,
    )


def _build_canonical_daily_point_with_inputs(
    db: Session,
    user_id: UUID,
    local_date: date,
    *,
    targets: list[NutritionTarget],
    active_energy_by_source: dict[tuple[date, str], Decimal],
    active_energy_sources_by_day: dict[date, set[str]],
    override: TrackingOverride | None,
) -> CanonicalDailyPointResult:
    try:
        projection_day = read_canonical_nutrition_day(db, user_id, local_date)
    except NutritionProjectionReadError:
        return _not_comparable(CanonicalDailyPointReason.PROJECTION_NOT_READY)
    return _build_canonical_daily_point_from_projection(
        projection_day=projection_day,
        local_date=local_date,
        targets=targets,
        active_energy_by_source=active_energy_by_source,
        active_energy_sources_by_day=active_energy_sources_by_day,
        override=override,
    )


def _build_canonical_daily_point(
    db: Session,
    user_id: UUID,
    local_date: date,
) -> CanonicalDailyPointResult:
    try:
        projection_day = read_canonical_nutrition_day(db, user_id, local_date)
    except NutritionProjectionReadError:
        return _not_comparable(CanonicalDailyPointReason.PROJECTION_NOT_READY)

    if projection_day.state is not NutritionProjectionReadState.READY:
        return _build_canonical_daily_point_from_projection(
            projection_day=projection_day,
            local_date=local_date,
            targets=[],
            active_energy_by_source={},
            active_energy_sources_by_day={},
            override=None,
        )

    targets = list(
        db.scalars(
            select(NutritionTarget)
            .where(NutritionTarget.user_id == user_id)
            .order_by(NutritionTarget.valid_from)
        )
    )
    active_samples = list(
        db.scalars(
            select(HealthSample).where(
                HealthSample.user_id == user_id,
                HealthSample.local_date == local_date,
                HealthSample.metric_type == ACTIVE_ENERGY_METRIC,
            )
        )
    )
    active_energy_by_source: dict[tuple[date, str], Decimal] = {}
    active_energy_sources_by_day: dict[date, set[str]] = {}
    for sample in active_samples:
        key = (local_date, sample.source_type)
        active_energy_by_source[key] = active_energy_by_source.get(key, Decimal()) + sample.value
        active_energy_sources_by_day.setdefault(local_date, set()).add(sample.source_type)

    override = db.scalar(
        select(TrackingOverride).where(
            TrackingOverride.user_id == user_id,
            TrackingOverride.local_date == local_date,
        )
    )
    return _build_canonical_daily_point_from_projection(
        projection_day=projection_day,
        local_date=local_date,
        targets=targets,
        active_energy_by_source=active_energy_by_source,
        active_energy_sources_by_day=active_energy_sources_by_day,
        override=override,
    )


class DailyPointParityClassification(StrEnum):
    MATCH = "match"
    NOT_COMPARABLE = "not_comparable"
    EXPLICIT_ZERO_SEMANTIC_DIFFERENCE = "explicit_zero_semantic_difference"
    CANONICAL_QUALITY_DIFFERENCE = "canonical_quality_difference"
    NUTRITION_VALUE_SEMANTIC_DIFFERENCE = "nutrition_value_semantic_difference"
    UNEXPLAINED_MISMATCH = "unexplained_mismatch"


@dataclass(frozen=True, slots=True)
class DailyPointTrackingParity:
    """Immutable tracking status/score comparison for one local calendar day."""

    local_date: date
    legacy_status: str | None
    canonical_status: str | None
    legacy_score: int | None
    canonical_score: int | None
    legacy_reasons: tuple[str, ...]
    canonical_reasons: tuple[str, ...]
    comparable: bool
    classification: DailyPointParityClassification

    @property
    def date(self) -> date:
        return self.local_date

    @property
    def legacy_tracking_status(self) -> str | None:
        return self.legacy_status

    @property
    def canonical_tracking_status(self) -> str | None:
        return self.canonical_status

    @property
    def legacy_tracking_score(self) -> int | None:
        return self.legacy_score

    @property
    def canonical_tracking_score(self) -> int | None:
        return self.canonical_score


@dataclass(frozen=True, slots=True)
class DailyPointFieldDifference:
    """One immutable value difference between legacy and canonical DailyPoints."""

    field_name: str
    legacy_value: object
    canonical_value: object
    classification: DailyPointParityClassification

    @property
    def field(self) -> str:
        return self.field_name


@dataclass(frozen=True, slots=True)
class DailyPointFieldParity:
    """Immutable comparison of the consumer-visible DailyPoint fields."""

    local_date: date
    differences: tuple[DailyPointFieldDifference, ...]
    comparable: bool
    classification: DailyPointParityClassification

    @property
    def date(self) -> date:
        return self.local_date

    @property
    def field_differences(self) -> tuple[DailyPointFieldDifference, ...]:
        return self.differences


@dataclass(frozen=True, slots=True)
class DailyPointParity:
    """Immutable, identifier-free parity result for one local calendar day."""

    local_date: date
    tracking: DailyPointTrackingParity
    fields: DailyPointFieldParity
    nutrition: NutritionDayParity
    comparable: bool
    classification: DailyPointParityClassification

    @property
    def date(self) -> date:
        return self.local_date

    @property
    def tracking_parity(self) -> DailyPointTrackingParity:
        return self.tracking

    @property
    def field_parity(self) -> DailyPointFieldParity:
        return self.fields

    @property
    def nutrition_parity(self) -> NutritionDayParity:
        return self.nutrition

    @property
    def legacy_status(self) -> str | None:
        return self.tracking.legacy_status

    @property
    def canonical_status(self) -> str | None:
        return self.tracking.canonical_status

    @property
    def legacy_score(self) -> int | None:
        return self.tracking.legacy_score

    @property
    def canonical_score(self) -> int | None:
        return self.tracking.canonical_score

    @property
    def legacy_reasons(self) -> tuple[str, ...]:
        return self.tracking.legacy_reasons

    @property
    def canonical_reasons(self) -> tuple[str, ...]:
        return self.tracking.canonical_reasons

    @property
    def field_differences(self) -> tuple[DailyPointFieldDifference, ...]:
        return self.fields.differences


@dataclass(frozen=True, slots=True)
class DailyPointRangeParity:
    """Immutable aggregate of DailyPoint parity over an inclusive date range."""

    start: date
    end: date
    days: tuple[DailyPointParity, ...]
    days_compared: int
    days_not_comparable: int
    status_matches: int
    expected_tracking_differences: int
    unexplained_tracking_mismatches: int
    unexpected_target_activity_mismatches: int
    canonical_points: tuple[DailyPoint, ...] = ()
    canonical_projection_ids: tuple[UUID | None, ...] = ()
    canonical_projection_ready: tuple[bool, ...] = ()
    canonical_calorie_usable: tuple[bool, ...] = ()


    @property
    def day_results(self) -> tuple[DailyPointParity, ...]:
        return self.days

    @property
    def status_match_count(self) -> int:
        return self.status_matches

    @property
    def tracking_status_matches(self) -> int:
        return self.status_matches

    @property
    def expected_tracking_difference_count(self) -> int:
        return self.expected_tracking_differences

    @property
    def unexplained_tracking_mismatch_count(self) -> int:
        return self.unexplained_tracking_mismatches

    @property
    def unexpected_target_activity_mismatch_count(self) -> int:
        return self.unexpected_target_activity_mismatches

    @property
    def comparable_count(self) -> int:
        return self.days_compared

    @property
    def not_comparable_count(self) -> int:
        return self.days_not_comparable

    @property
    def tracking_status_match_count(self) -> int:
        return self.status_matches

    @property
    def expected_tracking_differences_count(self) -> int:
        return self.expected_tracking_differences

    @property
    def unexplained_tracking_mismatches_count(self) -> int:
        return self.unexplained_tracking_mismatches

    @property
    def unexpected_target_activity_mismatches_count(self) -> int:
        return self.unexpected_target_activity_mismatches


_TARGET_ACTIVITY_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "target_kcal",
        "maintenance_kcal",
        "activity_mode",
        "activity_source_type",
        "active_energy_kcal",
        "activity_credit_kcal",
        "activity_data_status",
    }
)


_DAILY_POINT_FIELD_TO_NUTRITION_METRIC: Final[dict[str, str]] = {
    "calories_kcal": "dietary_energy_kcal",
    "protein_g": "protein_g",
    "carbs_g": "carbohydrates_g",
    "fat_g": "fat_g",
}
_DAILY_POINT_FIELDS: Final[tuple[str, ...]] = (
    "calories_kcal",
    "target_kcal",
    "maintenance_kcal",
    "deviation_kcal",
    "activity_mode",
    "activity_source_type",
    "active_energy_kcal",
    "activity_credit_kcal",
    "activity_data_status",
    "effective_budget_kcal",
    "effective_maintenance_kcal",
    "effective_deviation_kcal",
    "protein_g",
    "carbs_g",
    "fat_g",
)
_CALORIE_DERIVED_FIELDS: Final[frozenset[str]] = frozenset(
    {"deviation_kcal", "effective_deviation_kcal"}
)
_CLASSIFICATION_PRIORITY: Final[dict[DailyPointParityClassification, int]] = {
    DailyPointParityClassification.MATCH: 0,
    DailyPointParityClassification.NUTRITION_VALUE_SEMANTIC_DIFFERENCE: 1,
    DailyPointParityClassification.CANONICAL_QUALITY_DIFFERENCE: 2,
    DailyPointParityClassification.EXPLICIT_ZERO_SEMANTIC_DIFFERENCE: 3,
    DailyPointParityClassification.UNEXPLAINED_MISMATCH: 4,
}


def _nutrition_metrics_by_key(
    nutrition: NutritionDayParity,
) -> dict[str, NutritionMetricParity]:
    return {metric.metric_key: metric for metric in nutrition.metrics}


def _explicit_zero_difference(
    *,
    field_name: str,
    legacy_value: object,
    canonical_value: object,
    nutrition_metrics: dict[str, NutritionMetricParity],
) -> bool:
    metric_key = _DAILY_POINT_FIELD_TO_NUTRITION_METRIC.get(field_name)
    if metric_key is None or legacy_value is not None or canonical_value != Decimal("0"):
        return False
    metric = nutrition_metrics.get(metric_key)
    return (
        metric is not None
        and metric.classification is NutritionParityClassification.MATCH
        and metric.legacy_present
        and metric.legacy_value == Decimal("0")
        and metric.projection_value == Decimal("0")
    )


def _nutrition_difference_classification(
    field_name: str,
    legacy_value: object,
    canonical_value: object,
    nutrition_metrics: dict[str, NutritionMetricParity],
) -> DailyPointParityClassification:
    if _explicit_zero_difference(
        field_name=field_name,
        legacy_value=legacy_value,
        canonical_value=canonical_value,
        nutrition_metrics=nutrition_metrics,
    ):
        return DailyPointParityClassification.EXPLICIT_ZERO_SEMANTIC_DIFFERENCE

    metric_key = _DAILY_POINT_FIELD_TO_NUTRITION_METRIC.get(field_name)
    metric = nutrition_metrics.get(metric_key) if metric_key is not None else None
    if metric is not None and (
        metric.classification is NutritionParityClassification.LEGACY_MULTI_SOURCE
    ):
        return DailyPointParityClassification.NUTRITION_VALUE_SEMANTIC_DIFFERENCE
    return DailyPointParityClassification.UNEXPLAINED_MISMATCH


def _canonical_quality_difference(
    nutrition_metrics: dict[str, NutritionMetricParity],
    legacy: DailyPoint,
    canonical: DailyPoint,
) -> bool:
    if (
        legacy.calories_kcal is None
        or canonical.calories_kcal is None
        or legacy.tracking_score != 1
        or canonical.tracking_score != 0
        or not (
            (legacy.tracking_status == "complete" and canonical.tracking_status == "incomplete")
            or legacy.tracking_status == canonical.tracking_status
        )
    ):
        return False
    calorie_metric = nutrition_metrics.get("dietary_energy_kcal")
    return bool(
        calorie_metric is not None
        and calorie_metric.classification
        in {
            NutritionParityClassification.MATCH,
            NutritionParityClassification.LEGACY_MULTI_SOURCE,
        }
        and (
            calorie_metric.projection_coverage_state is CoverageState.PARTIAL
            or calorie_metric.projection_resolution_state is ResolutionState.UNRESOLVED
        )
    )


def _calorie_difference_explains_derived_field(
    field_name: str,
    legacy: DailyPoint,
    canonical: DailyPoint,
    calorie_classification: DailyPointParityClassification,
) -> bool:
    if field_name not in _CALORIE_DERIVED_FIELDS:
        return False
    if calorie_classification not in {
        DailyPointParityClassification.EXPLICIT_ZERO_SEMANTIC_DIFFERENCE,
        DailyPointParityClassification.NUTRITION_VALUE_SEMANTIC_DIFFERENCE,
    }:
        return False
    if legacy.target_kcal != canonical.target_kcal:
        return False
    if field_name == "effective_deviation_kcal":
        return legacy.effective_budget_kcal == canonical.effective_budget_kcal
    return True


def _tracking_classification(
    *,
    legacy: DailyPoint,
    canonical: DailyPoint,
    nutrition_metrics: dict[str, NutritionMetricParity],
) -> DailyPointParityClassification:
    if (
        legacy.tracking_status == canonical.tracking_status
        and legacy.tracking_score == canonical.tracking_score
    ):
        return DailyPointParityClassification.MATCH
    explicit_zero = _explicit_zero_difference(
        field_name="calories_kcal",
        legacy_value=legacy.calories_kcal,
        canonical_value=canonical.calories_kcal,
        nutrition_metrics=nutrition_metrics,
    )
    if (
        explicit_zero
        and legacy.tracking_score == 0
        and canonical.tracking_score == 1
        and (
            (legacy.tracking_status == "no_data" and canonical.tracking_status == "complete")
            or legacy.tracking_status == canonical.tracking_status
        )
    ):
        return DailyPointParityClassification.EXPLICIT_ZERO_SEMANTIC_DIFFERENCE
    if _canonical_quality_difference(nutrition_metrics, legacy, canonical):
        return DailyPointParityClassification.CANONICAL_QUALITY_DIFFERENCE
    return DailyPointParityClassification.UNEXPLAINED_MISMATCH


def _highest_classification(
    classifications: tuple[DailyPointParityClassification, ...],
) -> DailyPointParityClassification:
    return max(
        classifications,
        key=lambda classification: _CLASSIFICATION_PRIORITY[classification],
        default=DailyPointParityClassification.MATCH,
    )


def _build_tracking_parity(
    local_date: date,
    legacy: DailyPoint,
    canonical: DailyPoint,
    nutrition_metrics: dict[str, NutritionMetricParity],
) -> DailyPointTrackingParity:
    return DailyPointTrackingParity(
        local_date=local_date,
        legacy_status=legacy.tracking_status,
        canonical_status=canonical.tracking_status,
        legacy_score=legacy.tracking_score,
        canonical_score=canonical.tracking_score,
        legacy_reasons=tuple(legacy.tracking_reasons),
        canonical_reasons=tuple(canonical.tracking_reasons),
        comparable=True,
        classification=_tracking_classification(
            legacy=legacy,
            canonical=canonical,
            nutrition_metrics=nutrition_metrics,
        ),
    )


def _build_field_parity(
    local_date: date,
    legacy: DailyPoint,
    canonical: DailyPoint,
    nutrition_metrics: dict[str, NutritionMetricParity],
) -> DailyPointFieldParity:
    calorie_classification = _nutrition_difference_classification(
        "calories_kcal",
        legacy.calories_kcal,
        canonical.calories_kcal,
        nutrition_metrics,
    )
    differences: list[DailyPointFieldDifference] = []
    for field_name in _DAILY_POINT_FIELDS:
        legacy_value = getattr(legacy, field_name)
        canonical_value = getattr(canonical, field_name)
        if legacy_value == canonical_value:
            continue
        if field_name in _DAILY_POINT_FIELD_TO_NUTRITION_METRIC:
            classification = _nutrition_difference_classification(
                field_name,
                legacy_value,
                canonical_value,
                nutrition_metrics,
            )
        elif _calorie_difference_explains_derived_field(
            field_name,
            legacy,
            canonical,
            calorie_classification,
        ):
            classification = calorie_classification
        else:
            classification = DailyPointParityClassification.UNEXPLAINED_MISMATCH
        differences.append(
            DailyPointFieldDifference(
                field_name=field_name,
                legacy_value=legacy_value,
                canonical_value=canonical_value,
                classification=classification,
            )
        )
    classifications = tuple(difference.classification for difference in differences)
    return DailyPointFieldParity(
        local_date=local_date,
        differences=tuple(differences),
        comparable=True,
        classification=_highest_classification(classifications),
    )


def _non_comparable_parity(
    local_date: date,
    legacy: DailyPoint,
    nutrition: NutritionDayParity,
) -> DailyPointParity:
    tracking = DailyPointTrackingParity(
        local_date=local_date,
        legacy_status=legacy.tracking_status,
        canonical_status=None,
        legacy_score=legacy.tracking_score,
        canonical_score=None,
        legacy_reasons=tuple(legacy.tracking_reasons),
        canonical_reasons=(),
        comparable=False,
        classification=DailyPointParityClassification.NOT_COMPARABLE,
    )
    fields = DailyPointFieldParity(
        local_date=local_date,
        differences=(),
        comparable=False,
        classification=DailyPointParityClassification.NOT_COMPARABLE,
    )
    return DailyPointParity(
        local_date=local_date,
        tracking=tracking,
        fields=fields,
        nutrition=nutrition,
        comparable=False,
        classification=DailyPointParityClassification.NOT_COMPARABLE,
    )


def _compare_daily_point_results(
    *,
    local_date: date,
    legacy: DailyPoint,
    nutrition: NutritionDayParity,
    canonical_result: CanonicalDailyPointResult,
) -> DailyPointParity:
    if (
        canonical_result.state is not CanonicalDailyPointResultState.READY
        or canonical_result.point is None
        or not nutrition.comparable
    ):
        return _non_comparable_parity(local_date, legacy, nutrition)

    canonical = canonical_result.point
    nutrition_metrics = _nutrition_metrics_by_key(nutrition)
    tracking = _build_tracking_parity(local_date, legacy, canonical, nutrition_metrics)
    fields = _build_field_parity(local_date, legacy, canonical, nutrition_metrics)
    classification = _highest_classification((tracking.classification, fields.classification))
    return DailyPointParity(
        local_date=local_date,
        tracking=tracking,
        fields=fields,
        nutrition=nutrition,
        comparable=True,
        classification=classification,
    )


def compare_daily_point(
    db: Session,
    *,
    user_id: UUID,
    local_date: date,
) -> DailyPointParity:
    """Compare one legacy DailyPoint with the D1A-backed canonical candidate."""
    user = db.get(User, user_id)
    if user is None:
        raise ValueError("user not found")
    legacy_points = daily_points(db, user, local_date, local_date)
    if len(legacy_points) != 1:
        raise ValueError("legacy daily point reader returned an invalid result")
    legacy = legacy_points[0]
    nutrition = compare_nutrition_day(db, user_id=user_id, local_date=local_date)
    canonical_result = _build_canonical_daily_point(db, user_id, local_date)
    return _compare_daily_point_results(
        local_date=local_date,
        legacy=legacy,
        nutrition=nutrition,
        canonical_result=canonical_result,
    )


def _validate_daily_point_range(
    start: date,
    end: date,
    max_days: int,
) -> None:
    if not isinstance(start, date) or isinstance(start, datetime):
        raise ValueError("start must be a date")
    if not isinstance(end, date) or isinstance(end, datetime):
        raise ValueError("end must be a date")
    if start > end:
        raise ValueError("start must not be after end")
    if type(max_days) is not int or max_days < 1 or max_days > MAX_PARITY_DAYS:
        raise ValueError(f"max_days must be between 1 and {MAX_PARITY_DAYS}")
    requested_days = (end - start).days + 1
    if requested_days > MAX_PARITY_DAYS:
        raise ValueError(f"date range must not exceed {MAX_PARITY_DAYS} days")
    if requested_days > max_days:
        raise ValueError("date range exceeds max_days")


def _read_daily_point_range_inputs(
    db: Session,
    *,
    user_id: UUID,
    start: date,
    end: date,
) -> tuple[
    dict[date, dict[str, Decimal]],
    dict[date, int],
    dict[tuple[date, str], Decimal],
    dict[date, set[str]],
    dict[date, NutritionLegacyDay],
    list[NutritionTarget],
    dict[date, TrackingOverride],
]:
    samples = db.scalars(
        select(HealthSample).where(
            HealthSample.user_id == user_id,
            HealthSample.local_date >= start,
            HealthSample.local_date <= end,
        )
    ).all()

    totals_by_date: defaultdict[date, defaultdict[str, Decimal]] = defaultdict(
        lambda: defaultdict(Decimal)
    )
    nutrition_counts_by_date: defaultdict[date, int] = defaultdict(int)
    active_energy_by_source: defaultdict[tuple[date, str], Decimal] = defaultdict(Decimal)
    active_energy_sources_by_day: defaultdict[date, set[str]] = defaultdict(set)
    nutrition_values_by_date: dict[date, dict[tuple[str, str], Decimal]] = {}
    for sample in samples:
        if sample.metric_type == ACTIVE_ENERGY_METRIC:
            active_energy_by_source[(sample.local_date, sample.source_type)] += sample.value
            active_energy_sources_by_day[sample.local_date].add(sample.source_type)
            continue

        totals_by_date[sample.local_date][sample.metric_type] += sample.value
        if sample.metric_type in NUTRITION_METRICS:
            nutrition_counts_by_date[sample.local_date] += 1
        if sample.metric_type in CANONICAL_PARITY_METRICS:
            values_by_source = nutrition_values_by_date.setdefault(sample.local_date, {})
            key = (sample.metric_type, sample.source_type)
            values_by_source[key] = values_by_source.get(key, Decimal()) + sample.value

    for day, values in totals_by_date.items():
        if any(values.get(metric, Decimal()) > 0 for metric in PRIMARY_NUTRITION_METRICS):
            continue
        for metric in PRIMARY_NUTRITION_METRICS:
            values.pop(metric, None)
        nutrition_counts_by_date[day] = 0

    legacy_nutrition_days = {
        local_date: NutritionLegacyDay(
            local_date=local_date,
            metrics=tuple(
                _legacy_metric(metric_key, values_by_source)
                for metric_key in CANONICAL_PARITY_METRICS
            ),
        )
        for local_date, values_by_source in nutrition_values_by_date.items()
    }
    targets = list(
        db.scalars(
            select(NutritionTarget)
            .where(NutritionTarget.user_id == user_id)
            .order_by(NutritionTarget.valid_from)
        )
    )
    overrides = {
        item.local_date: item
        for item in db.scalars(
            select(TrackingOverride).where(
                TrackingOverride.user_id == user_id,
                TrackingOverride.local_date >= start,
                TrackingOverride.local_date <= end,
            )
        ).all()
    }
    return (
        {local_date: dict(values) for local_date, values in totals_by_date.items()},
        dict(nutrition_counts_by_date),
        dict(active_energy_by_source),
        dict(active_energy_sources_by_day),
        legacy_nutrition_days,
        targets,
        overrides,
    )


def compare_daily_point_range(
    db: Session,
    *,
    user_id: UUID,
    start: date,
    end: date,
    max_days: int = MAX_PARITY_DAYS,
) -> DailyPointRangeParity:
    """Compare DailyPoint parity for every inclusive day in a bounded range."""
    _validate_daily_point_range(start, end, max_days)
    user = db.get(User, user_id)
    if user is None:
        raise ValueError("user not found")

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
        start=start,
        end=end,
    )
    requested_dates = tuple(
        start + timedelta(days=offset) for offset in range((end - start).days + 1)
    )
    days: list[DailyPointParity] = []
    canonical_points: list[DailyPoint] = []
    canonical_projection_ids: list[UUID | None] = []
    canonical_projection_ready: list[bool] = []
    canonical_calorie_usable: list[bool] = []
    for local_date in requested_dates:
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
        nutrition = _compare_nutrition_day_with_legacy(
            db,
            legacy_nutrition_days.get(local_date, _empty_legacy_nutrition_day(local_date)),
            user_id=user_id,
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
        if canonical_result.point is not None:
            canonical_points.append(canonical_result.point)
        canonical_projection_ids.append(canonical_result.projection_id)
        canonical_projection_ready.append(canonical_result.projection_ready)
        canonical_calorie_usable.append(canonical_result.calorie_usable)
        days.append(
            _compare_daily_point_results(
                local_date=local_date,
                legacy=legacy,
                nutrition=nutrition,
                canonical_result=canonical_result,
            )
        )

    day_results = tuple(days)
    return DailyPointRangeParity(
        start=start,
        end=end,
        days=day_results,
        days_compared=sum(day.comparable for day in day_results),
        days_not_comparable=sum(not day.comparable for day in day_results),
        status_matches=sum(
            day.comparable and day.tracking.legacy_status == day.tracking.canonical_status
            for day in day_results
        ),
        expected_tracking_differences=sum(
            day.tracking.classification
            in {
                DailyPointParityClassification.EXPLICIT_ZERO_SEMANTIC_DIFFERENCE,
                DailyPointParityClassification.CANONICAL_QUALITY_DIFFERENCE,
            }
            for day in day_results
            if day.tracking.comparable
        ),
        unexplained_tracking_mismatches=sum(
            day.tracking.classification is DailyPointParityClassification.UNEXPLAINED_MISMATCH
            for day in day_results
            if day.tracking.comparable
        ),
        unexpected_target_activity_mismatches=sum(
            any(
                difference.field_name in _TARGET_ACTIVITY_FIELDS
                and difference.classification is DailyPointParityClassification.UNEXPLAINED_MISMATCH
                for difference in day.fields.differences
            )
            for day in day_results
            if day.fields.comparable
        ),
        canonical_points=tuple(canonical_points),
        canonical_projection_ids=tuple(canonical_projection_ids),
        canonical_projection_ready=tuple(canonical_projection_ready),
        canonical_calorie_usable=tuple(canonical_calorie_usable),
    )


__all__ = [
    "MAX_PARITY_DAYS",
    "CanonicalDailyPointReason",
    "CanonicalDailyPointResult",
    "CanonicalDailyPointResultState",
    "CanonicalDailyPointState",
    "DailyPointFieldDifference",
    "DailyPointFieldParity",
    "DailyPointParity",
    "DailyPointParityClassification",
    "DailyPointRangeParity",
    "DailyPointTrackingParity",
    "_build_canonical_daily_point",
    "compare_daily_point",
    "compare_daily_point_range",
]
