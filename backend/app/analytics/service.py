from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    HealthSample,
    NutritionTarget,
    TrackingOverride,
    TrackingQualitySettings,
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
    "water_ml",
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


def _settings(db: Session, user: User) -> TrackingQualitySettings:
    found = db.get(TrackingQualitySettings, user.id)
    if found:
        return found
    found = TrackingQualitySettings(user_id=user.id)
    db.add(found)
    db.flush()
    return found


def daily_points(
    db: Session, user: User, start: date, end: date, source: str | None = None
) -> list[DailyPoint]:
    extended_start = start - timedelta(days=28)
    query = select(HealthSample).where(
        HealthSample.user_id == user.id,
        HealthSample.local_date >= extended_start,
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
    quality = _settings(db, user)
    totals: dict[date, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    time_buckets: dict[date, set[int]] = defaultdict(set)
    nutrition_counts: dict[date, int] = defaultdict(int)
    latest_weight: dict[date, tuple[datetime | None, Decimal | None]] = {}
    for sample in samples:
        if sample.metric_type == "weight_kg":
            previous = latest_weight.get(sample.local_date)
            if previous is None or previous[0] is None or sample.start_at > previous[0]:
                latest_weight[sample.local_date] = (sample.start_at, sample.value)
            continue
        totals[sample.local_date][sample.metric_type] += sample.value
        if sample.metric_type in NUTRITION_METRICS:
            nutrition_counts[sample.local_date] += 1
            time_buckets[sample.local_date].add(
                sample.start_at.astimezone(ZoneInfo(user.timezone)).hour
            )

    historical_calories = {day: values.get("dietary_energy_kcal") for day, values in totals.items()}
    points: list[DailyPoint] = []
    for day in daterange(start, end):
        values = totals.get(day, {})
        calories = values.get("dietary_energy_kcal")
        target = _target_for(targets, day)
        target_kcal = target.calories_kcal if target else None
        preceding = [
            amount
            for historic_day, amount in historical_calories.items()
            if day - timedelta(days=28) <= historic_day < day and amount is not None
        ]
        personal_median = Decimal(str(median(preceding))) if preceding else None
        status, score, reasons = tracking_status(
            calories=calories,
            target=target_kcal,
            values=values,
            buckets=len(time_buckets.get(day, set())),
            nutrition_count=nutrition_counts.get(day, 0),
            personal_median=personal_median,
            quality=quality,
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
                active_energy_kcal=values.get("active_energy_kcal"),
                steps=values.get("steps"),
                weight_kg=latest_weight.get(day, (None, None))[1],
                tracking_status=status,
                tracking_score=score,
                tracking_reasons=reasons,
            )
        )
    return points


def tracking_status(
    *,
    calories: Decimal | None,
    target: Decimal | None,
    values: dict[str, Decimal],
    buckets: int,
    nutrition_count: int,
    personal_median: Decimal | None,
    quality: TrackingQualitySettings,
) -> tuple[str, int, list[str]]:
    if nutrition_count == 0:
        return "no_data", 0, ["Keine Ernährungsdaten vorhanden"]
    score = 0
    reasons: list[str] = []
    if calories is not None and target:
        ratio = calories / target
        if ratio >= quality.calories_full_ratio:
            score += 2
        elif ratio >= quality.calories_partial_ratio:
            score += 1
        else:
            reasons.append("Kalorien deutlich unter dem Tagesziel")
    macro_count = sum(metric in values for metric in ("protein_g", "carbohydrates_g", "fat_g"))
    if macro_count == 3:
        score += 2
    elif macro_count == 2:
        score += 1
    else:
        reasons.append("Makronährstoffe nur teilweise vorhanden")
    if buckets >= 3:
        score += 2
    elif buckets == 2:
        score += 1
    else:
        reasons.append("Wenige zeitlich verteilte Einträge")
    if calories is not None and personal_median:
        median_ratio = calories / personal_median
        if median_ratio >= quality.median_full_ratio:
            score += 2
        elif median_ratio >= quality.median_partial_ratio:
            score += 1
        else:
            reasons.append("Deutlich unter dem persönlichen 28-Tage-Median")
    else:
        reasons.append("Noch keine ausreichende persönliche Vergleichsbasis")
    if score >= quality.complete_score:
        status = "complete"
    elif score >= quality.probably_complete_score:
        status = "probably_complete"
    elif score >= quality.probably_incomplete_score:
        status = "probably_incomplete"
    else:
        status = "incomplete"
    if not reasons:
        reasons.append("Zielquote, Makros und zeitliche Verteilung sind plausibel")
    return status, score, reasons


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


def weight_moving_average(points: list[DailyPoint], window: int, index: int) -> Decimal | None:
    start = points[index].date - timedelta(days=window - 1)
    values = [
        point.weight_kg
        for point in points[: index + 1]
        if point.date >= start and point.weight_kg is not None
    ]
    return sum(values, Decimal()) / len(values) if values else None


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
