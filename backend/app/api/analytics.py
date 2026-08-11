from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analytics.service import (
    PRIMARY_NUTRITION_METRICS,
    daily_points,
    moving_average,
    percentile,
    serialize_decimal,
)
from app.auth.dependencies import current_user
from app.database import get_db
from app.micronutrients import MICRONUTRIENT_METRIC_TYPES, MICRONUTRIENTS
from app.models import HealthSample, ImportBatch, User
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


def _complete_budget(days: list[DailyPoint]) -> Decimal | None:
    targets = [day.target_kcal for day in days]
    if not targets or any(target is None for target in targets):
        return None
    return sum((target for target in targets if target is not None), Decimal())


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


@router.get("/analytics/micronutrients")
def micronutrients(
    start: date | None = None,
    end: date | None = None,
    source: str | None = Query(default="yazio_export_v1", max_length=64),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    start, end = _range(start, end, user.timezone, 30)

    all_source_rows = db.execute(
        select(HealthSample.source_type, func.max(HealthSample.updated_at))
        .where(
            HealthSample.user_id == user.id,
            HealthSample.local_date >= start,
            HealthSample.local_date <= end,
            HealthSample.metric_type.in_(MICRONUTRIENT_METRIC_TYPES),
        )
        .group_by(HealthSample.source_type)
        .order_by(HealthSample.source_type)
    ).all()

    sample_query = select(HealthSample).where(
        HealthSample.user_id == user.id,
        HealthSample.local_date >= start,
        HealthSample.local_date <= end,
        HealthSample.metric_type.in_(MICRONUTRIENT_METRIC_TYPES),
    )
    nutrition_day_query = (
        select(HealthSample.local_date)
        .where(
            HealthSample.user_id == user.id,
            HealthSample.local_date >= start,
            HealthSample.local_date <= end,
            HealthSample.metric_type.in_(PRIMARY_NUTRITION_METRICS),
            HealthSample.value > 0,
        )
        .distinct()
    )
    if source:
        sample_query = sample_query.where(HealthSample.source_type == source)
        nutrition_day_query = nutrition_day_query.where(HealthSample.source_type == source)

    samples = list(db.scalars(sample_query))
    recorded_dates = set(db.scalars(nutrition_day_query))
    if not recorded_dates:
        recorded_dates = {sample.local_date for sample in samples}

    totals: dict[str, Decimal] = defaultdict(Decimal)
    days_with_value: dict[str, set[date]] = defaultdict(set)
    for sample in samples:
        totals[sample.metric_type] += sample.value
        days_with_value[sample.metric_type].add(sample.local_date)

    recorded_days = len(recorded_dates)
    output = []
    for definition in MICRONUTRIENTS:
        total = totals.get(definition.metric_type)
        available_days = len(days_with_value.get(definition.metric_type, set()))
        average = total / recorded_days if total is not None and recorded_days else None
        coverage_ratio = available_days / recorded_days if recorded_days else 0.0
        reference_percent = (
            average / definition.eu_nrv * Decimal("100")
            if average is not None and definition.eu_nrv
            else None
        )
        if average is None:
            status = "no_data"
        elif coverage_ratio < 0.7:
            status = "insufficient_data"
        elif reference_percent is not None and reference_percent < Decimal("80"):
            status = "below_orientation"
        else:
            status = "covered"
        output.append(
            {
                "id": definition.yazio_id,
                "metric_type": definition.metric_type,
                "label": definition.label,
                "category": definition.category,
                "unit": definition.unit,
                "eu_nrv": serialize_decimal(definition.eu_nrv),
                "total": serialize_decimal(total),
                "average_daily": serialize_decimal(average),
                "days_with_value": available_days,
                "coverage_ratio": coverage_ratio,
                "percent_of_nrv": serialize_decimal(reference_percent),
                "status": status,
            }
        )

    filtered_updated_at = max(
        (sample.updated_at for sample in samples),
        default=None,
    )
    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "source": source,
        "recorded_days": recorded_days,
        "last_updated_at": filtered_updated_at.isoformat() if filtered_updated_at else None,
        "available_sources": [
            {
                "source_type": source_type,
                "last_updated_at": updated_at.isoformat() if updated_at else None,
            }
            for source_type, updated_at in all_source_rows
        ],
        "nutrients": output,
        "definition": {
            "reference": "EU-NRV für Erwachsene, Verordnung (EU) Nr. 1169/2011, Anhang XIII",
            "average": "Summe im Zeitraum geteilt durch Tage mit Ernährungseinträgen derselben Quelle",
            "coverage_threshold": 0.7,
            "orientation_threshold_percent": 80,
        },
    }


@router.get("/dashboard/summary")
def summary(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    today = datetime.now(ZoneInfo(user.timezone)).date()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    points = daily_points(db, user, week_start - timedelta(days=7), week_end)
    points_through_today = [point for point in points if point.date <= today]
    today_point = points_through_today[-1]
    current_week = [point for point in points if week_start <= point.date <= today]
    full_week = [point for point in points if week_start <= point.date <= week_end]
    consumed = sum([point.calories_kcal or Decimal() for point in current_week], Decimal())
    budget = _complete_budget(full_week)
    protein_values = [
        point.protein_g for point in points_through_today[-7:] if point.protein_g is not None
    ]
    last_import = db.scalar(
        select(ImportBatch)
        .where(ImportBatch.user_id == user.id, ImportBatch.status.like("completed%"))
        .order_by(ImportBatch.finished_at.desc())
        .limit(1)
    )
    first_data_date, last_data_date, recorded_days = db.execute(
        select(
            func.min(HealthSample.local_date),
            func.max(HealthSample.local_date),
            func.count(func.distinct(HealthSample.local_date)),
        ).where(
            HealthSample.user_id == user.id,
            HealthSample.metric_type.in_(PRIMARY_NUTRITION_METRICS),
            HealthSample.value > 0,
        )
    ).one()
    return {
        "today": today_point.model_dump(mode="json"),
        "week": {
            "consumed_kcal": serialize_decimal(consumed),
            "budget_kcal": serialize_decimal(budget),
            "deviation_kcal": serialize_decimal(consumed - budget if budget is not None else None),
            "remaining_kcal": serialize_decimal(budget - consumed if budget is not None else None),
        },
        "protein_7d_average_g": serialize_decimal(
            sum(protein_values, Decimal()) / len(protein_values) if protein_values else None
        ),
        "last_import_at": last_import.finished_at.isoformat()
        if last_import and last_import.finished_at
        else None,
        "data_start_date": first_data_date.isoformat() if first_data_date else None,
        "data_end_date": last_data_date.isoformat() if last_data_date else None,
        "data_day_count": int(recorded_days or 0),
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
        budget = _complete_budget(days)
        present = [day.calories_kcal for day in days if day.calories_kcal is not None]
        cumulative = []
        running_consumed = Decimal()
        running_budget: Decimal | None = Decimal()
        for day in days:
            running_consumed += day.calories_kcal or Decimal()
            if running_budget is not None:
                running_budget = (
                    running_budget + day.target_kcal if day.target_kcal is not None else None
                )
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
                "deviation_kcal": serialize_decimal(
                    consumed - budget if budget is not None else None
                ),
                "remaining_kcal": serialize_decimal(
                    budget - consumed if budget is not None else None
                ),
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
        if point.calories_kcal is not None:
            if point.target_kcal is None:
                classification = "no_target"
            # The calorie budget remains primary even when maintenance is lower.
            elif point.calories_kcal <= point.target_kcal:
                classification = "under_budget"
            elif (
                point.maintenance_kcal is not None and point.calories_kcal > point.maintenance_kcal
            ):
                classification = "above_maintenance"
            else:
                classification = "over_budget"
        elif point.tracking_status in {"probably_incomplete", "incomplete"}:
            classification = "probably_incomplete"
        output.append({**point.model_dump(mode="json"), "classification": classification})
    return {"days": output}


@router.get("/analytics/data-quality")
def data_quality(
    start: date | None = None,
    end: date | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    requested_start = start
    start, end = _range(start, end, user.timezone, 90)
    first_data_date = db.scalar(
        select(func.min(HealthSample.local_date)).where(
            HealthSample.user_id == user.id,
            HealthSample.metric_type.in_(PRIMARY_NUTRITION_METRICS),
            HealthSample.value > 0,
        )
    )
    if requested_start is None and first_data_date is not None and first_data_date > start:
        start = first_data_date
    points = daily_points(db, user, start, end)
    imports = list(
        db.scalars(
            select(ImportBatch)
            .where(ImportBatch.user_id == user.id)
            .order_by(ImportBatch.started_at.desc())
            .limit(20)
        )
    )
    recorded_days = sum(point.tracking_status != "no_data" for point in points)
    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "total_days": len(points),
        "recorded_days": recorded_days,
        "coverage_ratio": recorded_days / len(points) if points else 0,
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
                "client_identifier": batch.client_identifier,
                "started_at": batch.started_at.isoformat(),
                "finished_at": batch.finished_at.isoformat() if batch.finished_at else None,
                "received": batch.received,
                "inserted": batch.inserted,
                "updated": batch.updated,
                "skipped": batch.skipped,
                "failed": batch.failed,
                "unknown_types": batch.unknown_types,
                "error_message": batch.error_message,
            }
            for batch in imports
        ],
    }
