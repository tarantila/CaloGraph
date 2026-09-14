from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.activity import ACTIVE_ENERGY_METRIC
from app.analytics.nutrition_projection import (
    NutritionProjectionReadState,
    read_canonical_nutrition_day,
)
from app.analytics.service import (
    TrackingInputs,
    _build_daily_point,
)
from app.models import HealthSample, NutritionTarget, TrackingOverride
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


def _build_canonical_daily_point(
    db: Session,
    user_id: UUID,
    local_date: date,
) -> CanonicalDailyPointResult:
    try:
        projection_day = read_canonical_nutrition_day(db, user_id, local_date)
    except Exception:
        return _not_comparable(CanonicalDailyPointReason.PROJECTION_NOT_READY)

    if projection_day.state is NutritionProjectionReadState.NOT_PROJECTED:
        return _not_comparable(CanonicalDailyPointReason.NOT_PROJECTED)
    if projection_day.state is not NutritionProjectionReadState.READY:
        return _not_comparable(CanonicalDailyPointReason.PROJECTION_NOT_READY)

    try:
        facts_by_metric = {fact.metric_key: fact for fact in projection_day.facts}
        values = {
            daily_point_field: fact.value
            for metric_key, daily_point_field in _PRIMARY_DAILY_POINT_FIELDS
            if (fact := facts_by_metric[metric_key]).value is not None
        }
    except (KeyError, AttributeError, TypeError):
        return _not_comparable(CanonicalDailyPointReason.PROJECTION_NOT_READY)

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
    )


__all__ = [
    "CanonicalDailyPointReason",
    "CanonicalDailyPointResult",
    "CanonicalDailyPointResultState",
    "CanonicalDailyPointState",
    "_build_canonical_daily_point",
]
