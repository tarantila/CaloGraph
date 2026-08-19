from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.activity import ACTIVE_ENERGY_METRIC
from app.models import (
    HealthSample,
    NutritionTarget,
    TrackingOverride,
    User,
)
from app.schemas import DailyPoint

NUTRITION_METRICS = {
    "dietary_energy_kcal",
    "protein_g",
    "carbohydrates_g",
    "fat_g",
    "saturated_fat_g",
    "fiber_g",
    "sugar_g",
    "sodium_mg",
}

PRIMARY_NUTRITION_METRICS = {
    "dietary_energy_kcal",
    "protein_g",
    "carbohydrates_g",
    "fat_g",
}


def daterange(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _target_for(targets: list[NutritionTarget], day: date) -> NutritionTarget | None:
    return next(
        (
            target
            for target in reversed(targets)
            if target.valid_from <= day and (target.valid_to is None or day < target.valid_to)
        ),
        None,
    )


def _build_daily_point(
    *,
    day: date,
    values: dict[str, Decimal],
    nutrition_count: int,
    active_energy_by_source: dict[tuple[date, str], Decimal],
    active_energy_sources_by_day: dict[date, set[str]],
    targets: list[NutritionTarget],
    override: TrackingOverride | None,
) -> DailyPoint:
    calories = values.get("dietary_energy_kcal")
    target = _target_for(targets, day)
    target_kcal = target.calories_kcal if target else None
    maintenance_kcal = target.maintenance_kcal if target else None
    activity_mode = target.activity_mode if target else None
    activity_source_type = target.activity_source_type if target else None
    active_energy_kcal: Decimal | None = None
    activity_credit_kcal = Decimal()
    if activity_mode == "full" and activity_source_type is not None:
        active_energy_kcal = active_energy_by_source.get((day, activity_source_type))
        if active_energy_kcal is None:
            activity_data_status = "missing"
        else:
            activity_credit_kcal = active_energy_kcal
            activity_data_status = "credited"
    elif active_energy_sources_by_day.get(day):
        activity_data_status = "disabled_with_data"
    else:
        activity_data_status = "disabled"
    effective_budget_kcal = (
        target_kcal + activity_credit_kcal if target_kcal is not None else None
    )
    effective_maintenance_kcal = (
        maintenance_kcal + activity_credit_kcal
        if maintenance_kcal is not None
        else None
    )
    status, score, reasons = tracking_status(
        calories=calories,
        nutrition_count=nutrition_count,
    )
    if override is not None:
        status = override.status
        reasons = ["Manuell festgelegt"]
    return DailyPoint(
        date=day,
        calories_kcal=calories,
        target_kcal=target_kcal,
        maintenance_kcal=maintenance_kcal,
        deviation_kcal=(
            calories - target_kcal
            if calories is not None and target_kcal is not None
            else None
        ),
        activity_mode=activity_mode,
        activity_source_type=activity_source_type,
        active_energy_kcal=active_energy_kcal,
        activity_credit_kcal=activity_credit_kcal,
        activity_data_status=activity_data_status,
        effective_budget_kcal=effective_budget_kcal,
        effective_maintenance_kcal=effective_maintenance_kcal,
        effective_deviation_kcal=(
            calories - effective_budget_kcal
            if calories is not None and effective_budget_kcal is not None
            else None
        ),
        protein_g=values.get("protein_g"),
        carbs_g=values.get("carbohydrates_g"),
        fat_g=values.get("fat_g"),
        tracking_status=status,
        tracking_score=score,
        tracking_reasons=reasons,
    )


def daily_points(
    db: Session, user: User, start: date, end: date, source: str | None = None
) -> list[DailyPoint]:
    samples = list(
        db.scalars(
            select(HealthSample).where(
                HealthSample.user_id == user.id,
                HealthSample.local_date >= start,
                HealthSample.local_date <= end,
            )
        )
    )
    targets = list(
        db.scalars(
            select(NutritionTarget)
            .where(NutritionTarget.user_id == user.id)
            .order_by(NutritionTarget.valid_from)
        )
    )
    overrides = {
        item.local_date: item
        for item in db.scalars(
            select(TrackingOverride).where(
                TrackingOverride.user_id == user.id,
                TrackingOverride.local_date >= start,
                TrackingOverride.local_date <= end,
            )
        )
    }
    totals: dict[date, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    active_energy_by_source: dict[tuple[date, str], Decimal] = defaultdict(Decimal)
    active_energy_sources_by_day: dict[date, set[str]] = defaultdict(set)
    nutrition_counts: dict[date, int] = defaultdict(int)
    for sample in samples:
        if sample.metric_type == ACTIVE_ENERGY_METRIC:
            active_energy_by_source[(sample.local_date, sample.source_type)] += sample.value
            active_energy_sources_by_day[sample.local_date].add(sample.source_type)
            continue
        if source is not None and sample.source_type != source:
            continue
        totals[sample.local_date][sample.metric_type] += sample.value
        if sample.metric_type in NUTRITION_METRICS:
            nutrition_counts[sample.local_date] += 1

    for day, values in totals.items():
        if any(values.get(metric, Decimal()) > 0 for metric in PRIMARY_NUTRITION_METRICS):
            continue
        for metric in PRIMARY_NUTRITION_METRICS:
            values.pop(metric, None)
        nutrition_counts[day] = 0

    return [
        _build_daily_point(
            day=day,
            values=totals.get(day, {}),
            nutrition_count=nutrition_counts.get(day, 0),
            active_energy_by_source=active_energy_by_source,
            active_energy_sources_by_day=active_energy_sources_by_day,
            targets=targets,
            override=overrides.get(day),
        )
        for day in daterange(start, end)
    ]


_BUDGET_BALANCE_CHUNK_SIZE = 500


def _budget_balance_chunk(
    db: Session,
    user: User,
    tracked_dates: list[date],
    targets: list[NutritionTarget],
) -> dict[str, int]:
    nutrition_rows = db.execute(
        select(
            HealthSample.local_date,
            HealthSample.metric_type,
            func.sum(HealthSample.value),
        )
        .where(
            HealthSample.user_id == user.id,
            HealthSample.local_date.in_(tracked_dates),
            HealthSample.metric_type.in_(NUTRITION_METRICS),
        )
        .group_by(HealthSample.local_date, HealthSample.metric_type)
    ).all()
    activity_rows = db.execute(
        select(
            HealthSample.local_date,
            HealthSample.source_type,
            func.sum(HealthSample.value),
        )
        .where(
            HealthSample.user_id == user.id,
            HealthSample.local_date.in_(tracked_dates),
            HealthSample.metric_type == ACTIVE_ENERGY_METRIC,
        )
        .group_by(HealthSample.local_date, HealthSample.source_type)
    ).all()
    overrides = {
        item.local_date: item
        for item in db.scalars(
            select(TrackingOverride).where(
                TrackingOverride.user_id == user.id,
                TrackingOverride.local_date.in_(tracked_dates),
            )
        )
    }
    totals: dict[date, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for day, metric_type, total in nutrition_rows:
        totals[day][metric_type] = total
    active_energy_by_source = {
        (day, source_type): total for day, source_type, total in activity_rows
    }
    active_energy_sources_by_day: dict[date, set[str]] = defaultdict(set)
    for day, source_type, _total in activity_rows:
        active_energy_sources_by_day[day].add(source_type)
    points = [
        _build_daily_point(
            day=day,
            values=totals[day],
            nutrition_count=1,
            active_energy_by_source=active_energy_by_source,
            active_energy_sources_by_day=active_energy_sources_by_day,
            targets=targets,
            override=overrides.get(day),
        )
        for day in tracked_dates
    ]
    return budget_balance(points)


def budget_balance_for_user(
    db: Session,
    user: User,
    chunk_size: int = _BUDGET_BALANCE_CHUNK_SIZE,
) -> dict[str, int]:
    targets = list(
        db.scalars(
            select(NutritionTarget)
            .where(NutritionTarget.user_id == user.id)
            .order_by(NutritionTarget.valid_from)
        )
    )
    total = budget_balance([])
    last_date: date | None = None
    while True:
        statement = select(HealthSample.local_date).where(
            HealthSample.user_id == user.id,
            HealthSample.metric_type.in_(PRIMARY_NUTRITION_METRICS),
            HealthSample.value > 0,
        )
        if last_date is not None:
            statement = statement.where(HealthSample.local_date > last_date)
        statement = (
            statement.distinct()
            .order_by(HealthSample.local_date)
            .limit(chunk_size)
        )
        tracked_dates = list(db.scalars(statement))
        if not tracked_dates:
            return total
        chunk = _budget_balance_chunk(db, user, tracked_dates, targets)
        for key in total:
            total[key] += chunk[key]
        last_date = tracked_dates[-1]


def tracking_status(
    *,
    calories: Decimal | None,
    nutrition_count: int,
) -> tuple[str, int, list[str]]:
    if nutrition_count == 0:
        return "no_data", 0, ["Keine Ernährungsdaten vorhanden"]
    if calories is not None:
        return "complete", 1, ["Kalorienwert vorhanden"]
    return "incomplete", 0, ["Ernährungsdaten vorhanden, aber kein Kalorienwert"]

def budget_classification(point: DailyPoint) -> str:
    if point.calories_kcal is None:
        return "probably_incomplete" if point.tracking_status in {"probably_incomplete", "incomplete"} else "no_data"
    if point.effective_budget_kcal is None:
        return "no_target"
    # The effective daily budget is the primary threshold.
    if point.calories_kcal <= point.effective_budget_kcal:
        return "under_budget"
    if (
        point.effective_maintenance_kcal is not None
        and point.calories_kcal > point.effective_maintenance_kcal
    ):
        return "above_maintenance"
    return "over_budget"


def budget_balance(points: list[DailyPoint]) -> dict[str, int]:
    tracked_days = sum(point.tracking_status != "no_data" for point in points)
    counts = {
        "within_budget_days": 0,
        "over_budget_days": 0,
        "over_maintenance_days": 0,
    }
    for point in points:
        classification = budget_classification(point)
        if classification == "under_budget":
            counts["within_budget_days"] += 1
        elif classification == "over_budget":
            counts["over_budget_days"] += 1
        elif classification == "above_maintenance":
            counts["over_maintenance_days"] += 1
    return {
        "tracked_days": tracked_days,
        **counts,
        "unclassified_budget_days": tracked_days - sum(counts.values()),
    }


def moving_average(points: list[DailyPoint], window: int, index: int) -> Decimal | None:
    start = points[index].date - timedelta(days=window - 1)
    eligible = [
        point.calories_kcal
        for point in points[: index + 1]
        if point.date >= start
        and point.calories_kcal is not None
        and point.tracking_status in {"complete", "probably_complete"}
    ]
    return sum(eligible, Decimal()) / len(eligible) if eligible else None


def percentile(values: list[Decimal], fraction: Decimal) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (Decimal(1) - weight) + ordered[upper] * weight


def serialize_decimal(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def point_dict(point: DailyPoint) -> dict[str, Any]:
    return point.model_dump(mode="json")
