from collections import defaultdict
from contextlib import suppress
from datetime import date, datetime, timedelta
from decimal import Decimal
from statistics import median
from typing import Any, Literal, cast
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.analytics.calendar_canonical import run_calendar_canonical_read
from app.analytics.daily_canonical import (
    DailyCanonicalState,
    check_daily_canonical_read_eligibility,
    run_daily_canonical_read,
)
from app.analytics.daily_shadow import run_daily_shadow
from app.analytics.micronutrient_metadata_shadow import run_micronutrient_metadata_shadow
from app.analytics.micronutrient_shadow import (
    MicronutrientShadowState,
    read_canonical_micronutrient_period,
    read_legacy_micronutrient_period,
    run_micronutrient_canonical_read,
    run_micronutrient_shadow,
)
from app.analytics.provider_daily import ProviderDailyReadError, read_provider_daily_points
from app.analytics.provider_selection import (
    NutritionProviderNotReady,
    NutritionProviderSelection,
    NutritionProviderUnavailable,
    resolve_nutrition_provider,
)
from app.analytics.scalar_selection import (
    ScalarProviderNotReady,
    ScalarProviderSelection,
    ScalarProviderUnavailable,
    resolve_scalar_provider,
)
from app.analytics.service import (
    PRIMARY_NUTRITION_METRICS,
    budget_balance,
    budget_balance_for_user,
    budget_classification,
    daily_points,
    moving_average,
    percentile,
    serialize_decimal,
)
from app.analytics.trends_canonical import run_trends_canonical_read
from app.analytics.weekdays_canonical import run_weekdays_canonical_read
from app.analytics.weekly_canonical import run_weekly_canonical_read
from app.auth.dependencies import current_user
from app.config import settings
from app.database import get_db
from app.models import HealthSample, ImportBatch, User, YazioConnection
from app.nutrition.resolution.discovery import discover_nutrition_provider_metadata
from app.nutrition.resolution.read_context import NutritionEvidenceIndex
from app.problem_types import (
    PROVIDER_SELECTION_NOT_READY,
    PROVIDER_SELECTION_UNAVAILABLE,
    ProblemHTTPException,
)
from app.provider_preferences import WEIGHT_DATA_AREA
from app.schemas import DailyPoint, MicronutrientResponse, WeightResponse
from app.services.achievements import unlock_achievement_keys
from app.weight import WEIGHT_METRIC, WEIGHT_PROVIDER_SOURCE_TYPES

router = APIRouter(tags=["Analytics"])
_DEFAULT_RESOLVE_NUTRITION_PROVIDER = resolve_nutrition_provider


def _range(
    start: date | None, end: date | None, timezone: str, default_days: int = 30
) -> tuple[date, date]:
    resolved_end = end or datetime.now(ZoneInfo(timezone)).date()
    try:
        resolved_start = start or (resolved_end - timedelta(days=default_days - 1))
    except OverflowError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=422, detail="Datumsbereich ist zu groß") from exc
    if resolved_start > resolved_end:
        from fastapi import HTTPException

        raise HTTPException(status_code=422, detail="Startdatum liegt nach dem Enddatum")
    if (resolved_end - resolved_start).days > 3660:
        from fastapi import HTTPException

        raise HTTPException(status_code=422, detail="Datumsbereich ist zu groß")
    return resolved_start, resolved_end

def _unlock_big_picture_if_requested(
    db: Session,
    user: User,
    period: Literal["all"] | None,
) -> None:
    if period != "all":
        return
    first, last = db.execute(
        select(func.min(HealthSample.local_date), func.max(HealthSample.local_date)).where(
            HealthSample.user_id == user.id,
            HealthSample.metric_type.in_(PRIMARY_NUTRITION_METRICS),
            HealthSample.value > 0,
        )
    ).one()
    if first is not None and last is not None and (last - first).days >= 364:
        unlock_achievement_keys(db, user.id, ("the_big_picture",))


def _complete_budget(days: list[DailyPoint], field: str) -> Decimal | None:
    budgets = [getattr(day, field) for day in days]
    if not budgets or any(budget is None for budget in budgets):
        return None
    return sum((budget for budget in budgets if budget is not None), Decimal())

def _historical_budget_balance(db: Session, user: User) -> dict[str, int]:
    return budget_balance_for_user(db, user)



def _preferred_nutrition_provider(
    db: Session, user_id: UUID
) -> NutritionProviderSelection | None:
    if (
        resolve_nutrition_provider is _DEFAULT_RESOLVE_NUTRITION_PROVIDER
        and not isinstance(db, Session)
    ):
        return None
    try:
        return resolve_nutrition_provider(db, user_id=user_id)
    except AttributeError:
        if not hasattr(db, "get"):
            return None
        raise
    except NutritionProviderUnavailable as exc:
        raise ProblemHTTPException(
            status_code=503,
            detail="Der konfigurierte Nutrition-Provider ist derzeit nicht verfügbar.",
            problem_type=PROVIDER_SELECTION_UNAVAILABLE,
        ) from exc
    except NutritionProviderNotReady as exc:
        raise ProblemHTTPException(
            status_code=503,
            detail="Der konfigurierte Nutrition-Provider ist noch nicht lesbar konfiguriert.",
            problem_type=PROVIDER_SELECTION_NOT_READY,
        ) from exc
def _preferred_scalar_provider(
    db: Session,
    *,
    user_id: UUID,
    data_area: str,
    source_types: dict[str, str],
) -> ScalarProviderSelection | None:
    try:
        return resolve_scalar_provider(
            db,
            user_id=user_id,
            data_area=data_area,
            source_types=source_types,
        )
    except ScalarProviderUnavailable as exc:
        raise ProblemHTTPException(
            status_code=503,
            detail="Der konfigurierte Datenprovider ist derzeit nicht verfügbar.",
            problem_type=PROVIDER_SELECTION_UNAVAILABLE,
        ) from exc
    except ScalarProviderNotReady as exc:
        raise ProblemHTTPException(
            status_code=503,
            detail="Der konfigurierte Datenprovider ist noch nicht lesbar konfiguriert.",
            problem_type=PROVIDER_SELECTION_NOT_READY,
        ) from exc



def _read_preferred_daily_points(
    db: Session,
    *,
    user_id: UUID,
    selection: NutritionProviderSelection,
    start: date,
    end: date,
) -> list[DailyPoint]:
    try:
        return read_provider_daily_points(
            db,
            user_id=user_id,
            provider_key=selection.provider_key,
            source_instance_id=selection.source_instance_id,
            provider_sources=(
                selection.provider_sources
                or ((selection.provider_key, selection.source_instance_id),)
            ),
            start=start,
            end=end,
        )
    except ProviderDailyReadError as exc:
        raise ProblemHTTPException(
            status_code=503,
            detail="Der konfigurierte Nutrition-Provider konnte nicht sicher gelesen werden.",
            problem_type=PROVIDER_SELECTION_NOT_READY,
        ) from exc


@router.get("/analytics/daily", response_model=list[DailyPoint])
def daily(
    start: date | None = None,
    end: date | None = None,
    source: str | None = None,
    tracking: str | None = None,
    weekday: int | None = Query(default=None, ge=0, le=6),
    period: Literal["all"] | None = Query(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[DailyPoint]:
    start, end = _range(start, end, user.timezone)
    selection = _preferred_nutrition_provider(db, user.id) if source is None else None

    if selection is None:
        _unlock_big_picture_if_requested(db, user, period)
        points = daily_points(db, user, start, end, source)
        # D4B owns canonical comparison for eligible requests.  Ineligible D4B
        # requests retain the existing D4A observation path when configured.
        if settings.analytics_daily_canonical_read_enabled:
            canonical_eligibility = check_daily_canonical_read_eligibility(
                enabled=True,
                source=source,
                tracking=tracking,
                weekday=weekday,
                start=start,
                end=end,
                period=period,
                max_days=31,
            )
            with suppress(Exception):
                canonical_outcome = run_daily_canonical_read(
                    user.id,
                    start,
                    end,
                    source,
                    tracking,
                    weekday,
                    period=period,
                    enabled=True,
                    max_days=31,
                    legacy_points=tuple(points),
                )
                if canonical_outcome.points is not None:
                    points = list(canonical_outcome.points)
            if canonical_eligibility.state is not DailyCanonicalState.MATCH:
                with suppress(Exception):
                    run_daily_shadow(
                        user.id,
                        start,
                        end,
                        source,
                        tracking,
                        weekday,
                        enabled=settings.analytics_daily_shadow_read_enabled,
                        max_days=settings.analytics_daily_shadow_max_days,
                    )
        else:
            # The shadow read is strictly observational and must never affect the Legacy request.

            with suppress(Exception):
                run_daily_shadow(
                    user.id,
                    start,
                    end,
                    source,
                    tracking,
                    weekday,
                    enabled=settings.analytics_daily_shadow_read_enabled,
                    max_days=settings.analytics_daily_shadow_max_days,
                )
    else:
        # Preference serving must not read Legacy nutrition history or trigger its achievement write hook.
        points = _read_preferred_daily_points(
            db,
            user_id=user.id,
            selection=selection,
            start=start,
            end=end,
        )

    if tracking:
        statuses = set(tracking.split(","))
        points = [point for point in points if point.tracking_status in statuses]
    if weekday is not None:
        points = [point for point in points if point.date.weekday() == weekday]
    return points
@router.get("/analytics/weight", response_model=WeightResponse)
def weight(
    start: date | None = None,
    end: date | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    start, end = _range(start, end, user.timezone, 3661)
    selection = _preferred_scalar_provider(
        db,
        user_id=user.id,
        data_area=WEIGHT_DATA_AREA,
        source_types=WEIGHT_PROVIDER_SOURCE_TYPES,
    )
    if selection is None:
        return {
            "start_date": start,
            "end_date": end,
            "selected_provider": None,
            "points": [],
        }
    provider_sources = selection.provider_sources or ((selection.provider_key, selection.source_type),)
    source_identifier: str | None = None
    if any(provider_key == "yazio" for provider_key, _ in provider_sources):
        connection = db.scalar(select(YazioConnection).where(YazioConnection.user_id == user.id))
        if connection is None:
            raise ProblemHTTPException(
                status_code=503,
                detail="Der konfigurierte YAZIO-Provider ist nicht mehr verfügbar.",
                problem_type=PROVIDER_SELECTION_NOT_READY,
            )
        source_identifier = connection.source_identifier

    provider_by_source_type = {
        source_type: provider_key for provider_key, source_type in provider_sources
    }
    provider_filters = []
    for provider_key, source_type in provider_sources:
        source_filter = [
            HealthSample.source_type == source_type,
        ]
        if provider_key == "yazio":
            source_filter.append(HealthSample.source_identifier == source_identifier)
        provider_filters.append(and_(*source_filter))
    samples = list(
        db.scalars(
            select(HealthSample)
            .where(
                HealthSample.user_id == user.id,
                HealthSample.metric_type == WEIGHT_METRIC,
                HealthSample.local_date >= start,
                HealthSample.local_date <= end,
                or_(*provider_filters),
            )
            .order_by(HealthSample.local_date, HealthSample.start_at, HealthSample.id)
        )
    )
    latest_by_provider_day: dict[tuple[str, date], HealthSample] = {}
    for sample in samples:
        sample_provider_key = provider_by_source_type.get(sample.source_type)
        if sample_provider_key is None:
            continue
        key = (sample_provider_key, sample.local_date)
        previous = latest_by_provider_day.get(key)
        if previous is None or (sample.start_at, sample.id) > (previous.start_at, previous.id):
            latest_by_provider_day[key] = sample

    latest_by_day: dict[date, HealthSample] = {}
    for provider_key, _ in provider_sources:
        for (sample_provider, sample_day), sample in latest_by_provider_day.items():
            if sample_provider == provider_key and sample_day not in latest_by_day:
                latest_by_day[sample_day] = sample
    return {
        "start_date": start,
        "end_date": end,
        "selected_provider": {"provider_key": selection.provider_key},
        "points": [
            {"date": day, "weight_kg": float(sample.value)}
            for day, sample in sorted(latest_by_day.items())
        ],
    }

@router.get(
    "/analytics/micronutrients",
    response_model=MicronutrientResponse,
    response_model_exclude_unset=True,
)
def micronutrients(
    start: date | None = None,
    end: date | None = None,
    source: str | None = Query(default="yazio_export_v1", max_length=64),
    period: Literal["all"] | None = Query(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    request: Request = cast(Request, None),
) -> dict[str, Any]:
    start, end = _range(start, end, user.timezone, 30)
    explicit_legacy_source = request is None or "source" in request.query_params
    selection = None
    if not explicit_legacy_source:
        try:
            selection = resolve_nutrition_provider(db, user_id=user.id)
        except NutritionProviderUnavailable as exc:
            raise ProblemHTTPException(
                status_code=503,
                detail="Der konfigurierte Nutrition-Provider ist derzeit nicht verfügbar.",
                problem_type=PROVIDER_SELECTION_UNAVAILABLE,
            ) from exc
        except NutritionProviderNotReady as exc:
            raise ProblemHTTPException(
                status_code=503,
                detail="Der konfigurierte Nutrition-Provider ist noch nicht lesbar konfiguriert.",
                problem_type=PROVIDER_SELECTION_NOT_READY,
            ) from exc
        if selection is not None and period == "all":
            raise ProblemHTTPException(
                status_code=422,
                detail="Der konfigurierte Nutrition-Provider unterstützt den Gesamtzeitraum nicht.",
                problem_type=PROVIDER_SELECTION_NOT_READY,
            )
    legacy = None
    canonical_read_context: NutritionEvidenceIndex | None = None
    if selection is None:
        # Explicit source requests and empty unavailable registries retain Legacy compatibility.
        _unlock_big_picture_if_requested(db, user, period)
        legacy = read_legacy_micronutrient_period(
            db,
            user_id=user.id,
            start=start,
            end=end,
            source=source,
        )
        response = legacy.to_public(start=start, end=end, source=source)
    else:
        provider_sources = (
            selection.provider_sources
            if selection.provider_sources is not None
            else ((selection.provider_key, selection.source_instance_id),)
        )
        if len(provider_sources) == 1:
            canonical_read_context = NutritionEvidenceIndex(
                user_id=user.id,
                provider_key=selection.provider_key,
                source_instance_id=selection.source_instance_id,
                start=start,
                end=end,
            )
        canonical = read_canonical_micronutrient_period(
            db,
            user_id=user.id,
            provider_key=selection.provider_key,
            source_instance_id=selection.source_instance_id,
            provider_sources=provider_sources,
            start=start,
            end=end,
            read_context=canonical_read_context,
        )
        response = canonical.to_public(start=start, end=end, source=None)
    if selection is None and settings.analytics_micronutrients_canonical_read_enabled:
        assert legacy is not None
        with suppress(Exception):
            evaluation = run_micronutrient_canonical_read(
                user.id,
                start,
                end,
                source,
                period,
                legacy,
                enabled=True,
                max_days=settings.analytics_micronutrients_shadow_max_days,
            )
            if (
                evaluation.state is MicronutrientShadowState.MATCH
                and evaluation.canonical is not None
            ):
                response = {**response, **evaluation.canonical.to_public_value_scope()}
    elif selection is None:
        assert legacy is not None
        with suppress(Exception):
            run_micronutrient_shadow(
                user.id,
                start,
                end,
                source,
                period,
                legacy,
                enabled=settings.analytics_micronutrients_shadow_read_enabled,
                max_days=settings.analytics_micronutrients_shadow_max_days,
            )
    provider_metadata = None
    if selection is not None or settings.analytics_micronutrients_public_provider_metadata_enabled:
        with suppress(Exception):
            provider_metadata = discover_nutrition_provider_metadata(
                db,
                user_id=user.id,
                start=start,
                read_context=canonical_read_context,
                end=end,
                provider_key=(
                    selection.provider_key
                    if selection is not None
                    and not settings.analytics_micronutrients_public_provider_metadata_enabled
                    else None
                ),
            )
    if selection is not None:
        selected_latest_evidence = (
            next(
                (
                    provider.latest_evidence_observed_at
                    for provider in provider_metadata.providers
                    if provider.provider_key == selection.provider_key
                ),
                None,
            )
            if provider_metadata is not None
            else None
        )
        response = {
            **response,
            "selected_provider": {
                "provider_key": selection.provider_key,
                "latest_evidence_observed_at": (
                    selected_latest_evidence.isoformat() if selected_latest_evidence else None
                ),
            },
        }
    if settings.analytics_micronutrients_public_provider_metadata_enabled and provider_metadata is not None:
        response = {
            **response,
            "providers": [
                {
                    "provider_key": provider.provider_key,
                    "latest_evidence_observed_at": (
                        provider.latest_evidence_observed_at.isoformat()
                    ),
                }
                for provider in provider_metadata.providers
            ],
        }
    if legacy is not None:
        with suppress(Exception):
            run_micronutrient_metadata_shadow(
                user.id,
                start,
                end,
                source,
                period,
                legacy,
                enabled=settings.analytics_micronutrients_metadata_shadow_enabled,
                max_days=settings.analytics_micronutrients_shadow_max_days,
            )
    return response


@router.get("/dashboard/summary")
def summary(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    today = datetime.now(ZoneInfo(user.timezone)).date()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    selection = _preferred_nutrition_provider(db, user.id)
    if selection is None:
        points = daily_points(db, user, week_start - timedelta(days=7), week_end)
    else:
        points = _read_preferred_daily_points(
            db,
            user_id=user.id,
            selection=selection,
            start=week_start - timedelta(days=7),
            end=week_end,
        )
    points_through_today = [point for point in points if point.date <= today]
    today_point = points_through_today[-1]
    current_week = [point for point in points if week_start <= point.date <= today]
    full_week = [point for point in points if week_start <= point.date <= week_end]
    consumed = sum([point.calories_kcal or Decimal() for point in current_week], Decimal())
    budget = _complete_budget(full_week, "target_kcal")
    effective_budget = _complete_budget(full_week, "effective_budget_kcal")
    activity_credit = sum(
        (point.activity_credit_kcal for point in full_week),
        Decimal(),
    )
    protein_values = [
        point.protein_g for point in points_through_today[-7:] if point.protein_g is not None
    ]
    # Import-/Coverage-Metadaten bleiben bewusst HealthSample-basiert; sie sind
    # keine Provenance-Aussage über die Canonical-Nutrition-Werte oben.
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
            "activity_credit_kcal": serialize_decimal(activity_credit),
            "effective_budget_kcal": serialize_decimal(effective_budget),
            "effective_deviation_kcal": serialize_decimal(
                consumed - effective_budget if effective_budget is not None else None
            ),
            "effective_remaining_kcal": serialize_decimal(
                effective_budget - consumed if effective_budget is not None else None
            ),
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
    selection = _preferred_nutrition_provider(db, user.id)
    if selection is None:
        points = daily_points(db, user, start, end)
        requested_days = (end - start).days + 1
        if (
            settings.analytics_weekly_canonical_read_enabled
            and requested_days <= 31
        ):
            with suppress(Exception):
                canonical_outcome = run_weekly_canonical_read(
                    user.id,
                    start,
                    end,
                    legacy_points=tuple(points),
                )
                if canonical_outcome.points is not None:
                    points = list(canonical_outcome.points)
    else:
        points = _read_preferred_daily_points(
            db,
            user_id=user.id,
            selection=selection,
            start=start,
            end=end,
        )
    grouped: dict[date, list[DailyPoint]] = defaultdict(list)
    for point in points:
        week_start = point.date - timedelta(days=(point.date.weekday() - user.week_starts_on) % 7)
        grouped[week_start].append(point)
    weeks = []
    for week_start, days in sorted(grouped.items()):
        consumed = sum([day.calories_kcal or Decimal() for day in days], Decimal())
        budget = _complete_budget(days, "target_kcal")
        effective_budget = _complete_budget(days, "effective_budget_kcal")
        activity_credit = sum((day.activity_credit_kcal for day in days), Decimal())
        present = [day.calories_kcal for day in days if day.calories_kcal is not None]
        cumulative = []
        running_consumed = Decimal()
        running_budget: Decimal | None = Decimal()
        running_effective_budget: Decimal | None = Decimal()
        for day in days:
            running_consumed += day.calories_kcal or Decimal()
            if running_budget is not None:
                running_budget = (
                    running_budget + day.target_kcal if day.target_kcal is not None else None
                )
            if running_effective_budget is not None:
                running_effective_budget = (
                    running_effective_budget + day.effective_budget_kcal
                    if day.effective_budget_kcal is not None
                    else None
                )
            cumulative.append(
                {
                    "date": day.date.isoformat(),
                    "consumed_kcal": serialize_decimal(running_consumed),
                    "budget_kcal": serialize_decimal(running_budget),
                    "effective_budget_kcal": serialize_decimal(running_effective_budget),
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
                "activity_credit_kcal": serialize_decimal(activity_credit),
                "effective_budget_kcal": serialize_decimal(effective_budget),
                "effective_deviation_kcal": serialize_decimal(
                    consumed - effective_budget if effective_budget is not None else None
                ),
                "effective_remaining_kcal": serialize_decimal(
                    effective_budget - consumed if effective_budget is not None else None
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
    period: Literal["all"] | None = Query(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    start, end = _range(start, end, user.timezone, 180)
    selection = _preferred_nutrition_provider(db, user.id)
    if selection is None:
        _unlock_big_picture_if_requested(db, user, period)
        points = daily_points(db, user, start, end)
        if (
            settings.analytics_weekdays_canonical_read_enabled
            and period != "all"
            and (end - start).days + 1 <= 31
        ):
            with suppress(Exception):
                canonical_outcome = run_weekdays_canonical_read(
                    user.id,
                    start,
                    end,
                    legacy_points=tuple(points),
                )
                if canonical_outcome.points is not None:
                    points = list(canonical_outcome.points)
    else:
        # Preference serving must not read Legacy nutrition history or trigger its achievement write hook.
        points = _read_preferred_daily_points(
            db,
            user_id=user.id,
            selection=selection,
            start=start,
            end=end,
        )
    groups: dict[int, list[DailyPoint]] = defaultdict(list)
    for point in points:
        groups[point.date.weekday()].append(point)
    labels = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    output = []
    for weekday in range(7):
        available = [point for point in groups[weekday] if point.calories_kcal is not None]
        values = [point.calories_kcal for point in available if point.calories_kcal is not None]
        deviations = [
            point.deviation_kcal for point in available if point.deviation_kcal is not None
        ]
        effective_deviations = [
            point.effective_deviation_kcal
            for point in available
            if point.effective_deviation_kcal is not None
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
                "mean_effective_deviation_kcal": serialize_decimal(
                    sum(effective_deviations, Decimal()) / len(effective_deviations)
                    if effective_deviations
                    else None
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
    period: Literal["all"] | None = Query(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    start, end = _range(start, end, user.timezone, 90)
    selection = _preferred_nutrition_provider(db, user.id)
    if selection is None:
        _unlock_big_picture_if_requested(db, user, period)
        points = daily_points(db, user, start, end)
        requested_days = (end - start).days + 1
        if (
            settings.analytics_trends_canonical_read_enabled
            and period != "all"
            and requested_days <= 31
        ):
            with suppress(Exception):
                canonical_outcome = run_trends_canonical_read(
                    user.id,
                    start,
                    end,
                    legacy_points=tuple(points),
                )
                if canonical_outcome.points is not None:
                    points = list(canonical_outcome.points)
        historical_budget_balance = _historical_budget_balance(db, user)
    else:
        if period == "all":
            raise ProblemHTTPException(
                status_code=422,
                detail="Der konfigurierte Nutrition-Provider unterstützt den Gesamtzeitraum nicht.",
                problem_type=PROVIDER_SELECTION_NOT_READY,
            )
        points = _read_preferred_daily_points(
            db,
            user_id=user.id,
            selection=selection,
            start=start,
            end=end,
        )
        historical_budget_balance = budget_balance(points)
    if include_incomplete:
        points = [point.model_copy() for point in points]
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
    return {
        "points": output,
        "incomplete_included": include_incomplete,
        "budget_balance": historical_budget_balance,
    }


@router.get("/analytics/calendar")
def calendar(
    start: date | None = None,
    end: date | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    start, end = _range(start, end, user.timezone, 31)
    selection = _preferred_nutrition_provider(db, user.id)
    if selection is None:
        points = daily_points(db, user, start, end)
        if settings.analytics_calendar_canonical_read_enabled:
            with suppress(Exception):
                canonical_outcome = run_calendar_canonical_read(
                    user.id,
                    start,
                    end,
                    legacy_points=points,
                )
                if canonical_outcome.points is not None:
                    points = list(canonical_outcome.points)
    else:
        points = _read_preferred_daily_points(
            db,
            user_id=user.id,
            selection=selection,
            start=start,
            end=end,
        )
    output = []
    for point in points:
        classification = budget_classification(point)
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
