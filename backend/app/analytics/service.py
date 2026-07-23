from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

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


def daily_points(
    db: Session, user: User, start: date, end: date, source: str | None = None
) -> list[DailyPoint]:
    query = select(HealthSample).where(
        HealthSample.user_id == user.id,
        HealthSample.local_date >= start,
        HealthSample.local_date <= end,
    )
    if source:
        query = query.where(HealthSample.source_type == source)
    samples = list(db.scalars(query))
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
    nutrition_counts: dict[date, int] = defaultdict(int)
    for sample in samples:
        totals[sample.local_date][sample.metric_type] += sample.value
        if sample.metric_type in NUTRITION_METRICS:
            nutrition_counts[sample.local_date] += 1

    for day, values in totals.items():
        if any(values.get(metric, Decimal()) > 0 for metric in PRIMARY_NUTRITION_METRICS):
            continue
        for metric in PRIMARY_NUTRITION_METRICS:
            values.pop(metric, None)
        nutrition_counts[day] = 0

    points: list[DailyPoint] = []
    for day in daterange(start, end):
        values = totals.get(day, {})
        calories = values.get("dietary_energy_kcal")
        target = _target_for(targets, day)
        target_kcal = target.calories_kcal if target else None
        status, score, reasons = tracking_status(
            calories=calories,
            nutrition_count=nutrition_counts.get(day, 0),
        )
        if day in overrides:
            status = overrides[day].status
            reasons = ["Manuell festgelegt"]
        points.append(
            DailyPoint(
                date=day,
                calories_kcal=calories,
                target_kcal=target_kcal,
                deviation_kcal=(calories - target_kcal)
                if calories is not None and target_kcal
                else None,
                protein_g=values.get("protein_g"),
                carbs_g=values.get("carbohydrates_g"),
                fat_g=values.get("fat_g"),
                tracking_status=status,
                tracking_score=score,
                tracking_reasons=reasons,
            )
        )
    return points


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
