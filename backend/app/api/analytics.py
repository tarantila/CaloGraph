from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.service import (
    daily_points,
    moving_average,
    percentile,
    serialize_decimal,
    weight_moving_average,
)
from app.auth.dependencies import current_user
from app.database import get_db
from app.models import ImportBatch, User
from app.schemas import DailyPoint

router = APIRouter(tags=["Analytics"])


def _range(
    start: date | None, end: date | None, timezone: str, default_days: int = 30
) -> tuple[date, date]:
    resolved_end = end or datetime.now(ZoneInfo(timezone)).date()
    resolved_start = start or (resolved_end - timedelta(days=default_days - 1))
    if resolved_start > resolved_end:
        from fastapi import HTTPException

        raise HTTPException(status_code=422, detail="Startdatum liegt nach dem Enddatum")
    if (resolved_end - resolved_start).days > 3660:
        from fastapi import HTTPException

        raise HTTPException(status_code=422, detail="Datumsbereich ist zu groß")
    return resolved_start, resolved_end


@router.get("/analytics/daily", response_model=list[DailyPoint])
def daily(
    start: date | None = None,
    end: date | None = None,
    source: str | None = None,
    tracking: str | None = None,
    weekday: int | None = Query(default=None, ge=0, le=6),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[DailyPoint]:
    start, end = _range(start, end, user.timezone)
    points = daily_points(db, user, start, end, source)
    if tracking:
        statuses = set(tracking.split(","))
        points = [point for point in points if point.tracking_status in statuses]
    if weekday is not None:
        points = [point for point in points if point.date.weekday() == weekday]
    return points


@router.get("/dashboard/summary")
def summary(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    today = datetime.now(ZoneInfo(user.timezone)).date()
    week_start = today - timedelta(days=(today.weekday() - user.week_starts_on) % 7)
    points = daily_points(db, user, week_start - timedelta(days=7), today)
    today_point = points[-1]
    current_week = [point for point in points if point.date >= week_start]
    previous_week = [point for point in points if point.date < week_start]
    consumed = sum([point.calories_kcal or Decimal() for point in current_week], Decimal())
    budget = sum([point.target_kcal or Decimal() for point in current_week], Decimal())
    protein_values = [point.protein_g for point in points[-7:] if point.protein_g is not None]
    weights = [point.weight_kg for point in points if point.weight_kg is not None]
    previous_weights = [point.weight_kg for point in previous_week if point.weight_kg is not None]
    last_import = db.scalar(
        select(ImportBatch)
        .where(ImportBatch.user_id == user.id, ImportBatch.status.like("completed%"))
        .order_by(ImportBatch.finished_at.desc())
        .limit(1)
    )
    return {
        "today": today_point.model_dump(mode="json"),
        "week": {
            "consumed_kcal": serialize_decimal(consumed),
            "budget_kcal": serialize_decimal(budget),
            "deviation_kcal": serialize_decimal(consumed - budget),
            "remaining_kcal": serialize_decimal(budget - consumed),
        },
        "protein_7d_average_g": serialize_decimal(
            sum(protein_values, Decimal()) / len(protein_values) if protein_values else None
        ),
        "current_weight_kg": serialize_decimal(weights[-1] if weights else None),
        "weight_change_kg": serialize_decimal(
            weights[-1] - previous_weights[-1] if weights and previous_weights else None
        ),
        "last_import_at": last_import.finished_at.isoformat()
        if last_import and last_import.finished_at
        else None,
    }


@router.get("/analytics/weekly")
def weekly(
    start: date | None = None,
    end: date | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    start, end = _range(start, end, user.timezone, 90)
    points = daily_points(db, user, start, end)
    grouped: dict[date, list[DailyPoint]] = defaultdict(list)
    for point in points:
        week_start = point.date - timedelta(days=(point.date.weekday() - user.week_starts_on) % 7)
        grouped[week_start].append(point)
    weeks = []
    for week_start, days in sorted(grouped.items()):
        consumed = sum([day.calories_kcal or Decimal() for day in days], Decimal())
        budget = sum([day.target_kcal or Decimal() for day in days], Decimal())
        present = [day.calories_kcal for day in days if day.calories_kcal is not None]
        cumulative = []
        running_consumed = running_budget = Decimal()
        for day in days:
            running_consumed += day.calories_kcal or Decimal()
            running_budget += day.target_kcal or Decimal()
            cumulative.append(
                {
                    "date": day.date.isoformat(),
                    "consumed_kcal": serialize_decimal(running_consumed),
                    "budget_kcal": serialize_decimal(running_budget),
                }
            )
        weeks.append(
            {
                "week_start": week_start.isoformat(),
                "consumed_kcal": serialize_decimal(consumed),
                "budget_kcal": serialize_decimal(budget),
                "deviation_kcal": serialize_decimal(consumed - budget),
                "remaining_kcal": serialize_decimal(budget - consumed),
                "mean_kcal": serialize_decimal(
                    sum(present, Decimal()) / len(present) if present else None
                ),
                "median_kcal": serialize_decimal(
                    Decimal(str(median(present))) if present else None
                ),
                "days": [day.model_dump(mode="json") for day in days],
                "cumulative": cumulative,
            }
        )
    return {"weeks": weeks}


@router.get("/analytics/weekdays")
def weekdays(
    start: date | None = None,
    end: date | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    start, end = _range(start, end, user.timezone, 180)
    groups: dict[int, list[DailyPoint]] = defaultdict(list)
    for point in daily_points(db, user, start, end):
        groups[point.date.weekday()].append(point)
    labels = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    output = []
    for weekday in range(7):
        available = [point for point in groups[weekday] if point.calories_kcal is not None]
        values = [point.calories_kcal for point in available if point.calories_kcal is not None]
        deviations = [
            point.deviation_kcal for point in available if point.deviation_kcal is not None
        ]
        proteins = [point.protein_g for point in available if point.protein_g is not None]
        incomplete = sum(
            point.tracking_status in {"probably_incomplete", "incomplete"} for point in available
        )
        output.append(
            {
                "weekday": weekday,
                "label": labels[weekday],
                "count": len(available),
                "mean_kcal": serialize_decimal(
                    sum(values, Decimal()) / len(values) if values else None
                ),
                "median_kcal": serialize_decimal(Decimal(str(median(values))) if values else None),
                "p25_kcal": serialize_decimal(percentile(values, Decimal("0.25"))),
                "p75_kcal": serialize_decimal(percentile(values, Decimal("0.75"))),
                "mean_deviation_kcal": serialize_decimal(
                    sum(deviations, Decimal()) / len(deviations) if deviations else None
                ),
                "mean_protein_g": serialize_decimal(
                    sum(proteins, Decimal()) / len(proteins) if proteins else None
                ),
                "incomplete_share": incomplete / len(available) if available else None,
            }
        )
    return {"weekdays": output}


@router.get("/analytics/trends")
def trends(
    start: date | None = None,
    end: date | None = None,
    include_incomplete: bool = Query(False),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    start, end = _range(start, end, user.timezone, 90)
    points = daily_points(db, user, start, end)
    output = []
    for index, point in enumerate(points):
        item = point.model_dump(mode="json")
        if include_incomplete:
            eligible = points
            original_statuses = [candidate.tracking_status for candidate in eligible]
            for candidate in eligible:
                if candidate.calories_kcal is not None:
                    candidate.tracking_status = "complete"
            item.update(
                {
                    "average_7d": serialize_decimal(moving_average(points, 7, index)),
                    "average_14d": serialize_decimal(moving_average(points, 14, index)),
                    "average_28d": serialize_decimal(moving_average(points, 28, index)),
                    "weight_average_7d": serialize_decimal(weight_moving_average(points, 7, index)),
                }
            )
            for candidate, original in zip(eligible, original_statuses, strict=True):
                candidate.tracking_status = original
        else:
            item.update(
                {
                    "average_7d": serialize_decimal(moving_average(points, 7, index)),
                    "average_14d": serialize_decimal(moving_average(points, 14, index)),
                    "average_28d": serialize_decimal(moving_average(points, 28, index)),
                    "weight_average_7d": serialize_decimal(weight_moving_average(points, 7, index)),
                }
            )
        output.append(item)
    return {"points": output, "incomplete_included": include_incomplete}


@router.get("/analytics/calendar")
def calendar(
    start: date | None = None,
    end: date | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    start, end = _range(start, end, user.timezone, 31)
    output = []
    for point in daily_points(db, user, start, end):
        classification = "no_data"
        if point.tracking_status in {"probably_incomplete", "incomplete"}:
            classification = "probably_incomplete"
        elif point.deviation_kcal is not None and point.target_kcal:
            ratio = point.deviation_kcal / point.target_kcal
            if ratio < Decimal("-0.15"):
                classification = "well_below"
            elif ratio < Decimal("-0.05"):
                classification = "slightly_below"
            elif ratio <= Decimal("0.05"):
                classification = "on_target"
            elif ratio <= Decimal("0.15"):
                classification = "slightly_above"
            else:
                classification = "well_above"
        output.append({**point.model_dump(mode="json"), "classification": classification})
    return {"days": output}


@router.get("/analytics/data-quality")
def data_quality(
    start: date | None = None,
    end: date | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    start, end = _range(start, end, user.timezone, 90)
    points = daily_points(db, user, start, end)
    imports = list(
        db.scalars(
            select(ImportBatch)
            .where(ImportBatch.user_id == user.id)
            .order_by(ImportBatch.started_at.desc())
            .limit(20)
        )
    )
    return {
        "missing_days": [
            point.date.isoformat() for point in points if point.tracking_status == "no_data"
        ],
        "incomplete_days": [
            point.model_dump(mode="json")
            for point in points
            if point.tracking_status in {"probably_incomplete", "incomplete"}
        ],
        "unknown_types": sorted({item for batch in imports for item in batch.unknown_types}),
        "failed_records": sum(batch.failed for batch in imports),
        "imports": [
            {
                "id": str(batch.id),
                "status": batch.status,
                "source_type": batch.source_type,
                "started_at": batch.started_at.isoformat(),
                "failed": batch.failed,
            }
            for batch in imports
        ],
    }
