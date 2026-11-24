"""Build a bounded, whole-history account context from canonical readers.

This module performs database reads only. It has no provider client, HTTP, or
model dependency; callers must close the read transaction before dispatch.
"""

from collections import defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.activity import ACTIVE_ENERGY_METRIC, ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS
from app.analytics.nutrition_analysis import AnalysisNutritionReadError, read_analysis_nutrition
from app.analytics.nutrition_micronutrients import (
    AnalysisMicronutrientReadError,
    read_analysis_micronutrients,
)
from app.analytics.provider_selection import (
    NutritionProviderSelectionError,
    resolve_nutrition_provider,
)
from app.analytics.scalar_selection import ScalarProviderSelectionError, resolve_scalar_provider
from app.analytics.verification import read_activity_verification
from app.analytics.weight_history import read_weight_history, weight_history_bounds
from app.expert.context_contracts import (
    DailyNutritionDetail,
    ExpertContext,
    ExpertHealthContext,
    ExpertProfileContext,
    HistoryAggregate,
    MetricAggregate,
    MicronutrientCoverage,
    ProviderCoverage,
    ScalarDailyPoint,
    ScalarHistoryAggregate,
    TargetPeriod,
)
from app.expert.contracts import NUTRITION_METRICS, NutritionValues
from app.expert.preferences_contracts import ExpertPreferencesView
from app.micronutrients import MICRONUTRIENTS
from app.models import (
    GoogleHealthConnection,
    HealthSample,
    NutritionTarget,
    User,
    UserProfile,
    WithingsConnection,
    YazioConnection,
)
from app.nutrition.models import NutritionConsumptionEvent, NutritionSourceObservation
from app.provider_preferences import ACTIVITY_ENERGY_DATA_AREA
from app.services.analysis_demo import demo_source_instance_id

MAX_CONTEXT_HISTORY_DAYS = 36_525
READ_CHUNK_DAYS = 365
RECENT_DETAIL_DAYS = 31
MONTHLY_HISTORY_YEARS = 10


@dataclass(frozen=True, slots=True)
class _Target:
    valid_from: date
    valid_to: date | None
    calories_kcal: Decimal
    maintenance_kcal: Decimal | None
    protein_g: Decimal
    carbs_g: Decimal | None
    fat_g: Decimal | None
    fiber_g: Decimal | None


class ExpertContextProblem(RuntimeError):
    """A requested account context cannot be represented completely and safely."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ContextBuilder:
    def __init__(self, db: Session, owner: User) -> None:
        self._db = db
        self._owner = owner

    def build(self, preferences: ExpertPreferencesView) -> ExpertContext:
        try:
            return self._build(preferences)
        except ExpertContextProblem:
            raise
        except Exception:
            raise ExpertContextProblem("context_unavailable") from None

    def _build(self, preferences: ExpertPreferencesView) -> ExpertContext:
        if not self._owner.is_active:
            raise ExpertContextProblem("account_inactive") from None
        try:
            today = datetime.now(UTC).astimezone(ZoneInfo(self._owner.timezone)).date()
        except (KeyError, ValueError):
            raise ExpertContextProblem("account_timezone_invalid") from None

        enabled = set(preferences.data_domains)
        coverage_starts: list[date] = []
        nutrition_status: str = "disabled"
        provider_coverage: tuple[ProviderCoverage, ...] = ()
        aggregates: tuple[HistoryAggregate, ...] = ()
        daily: tuple[DailyNutritionDetail, ...] = ()
        micronutrients: tuple[MicronutrientCoverage, ...] = ()
        targets_status = "disabled"
        targets: tuple[TargetPeriod, ...] = ()
        target_rows: list[_Target] = []
        if "targets" in enabled:
            target_start, targets_status, targets, target_rows = self._target_history(today)
            if target_start is not None:
                coverage_starts.append(target_start)

        if "nutrition" in enabled:
            nutrition_start, nutrition_status, provider_coverage, aggregates, daily, micronutrients = (
                self._nutrition_history(today, preferences, target_rows)
            )
            if nutrition_start is not None:
                coverage_starts.append(nutrition_start)

        weight_status = "disabled"
        weight_points: tuple[tuple[date, Decimal], ...] = ()
        weight_daily: tuple[ScalarDailyPoint, ...] = ()
        weight_aggregates: tuple[ScalarHistoryAggregate, ...] = ()
        if "weight" in enabled:
            weight_start, weight_points, weight_providers = self._weight_history(today)
            weight_status = "available" if weight_points else "no_records"
            if weight_start is not None:
                coverage_starts.append(weight_start)
                weight_map = {day: value for day, value in weight_points}
                weight_daily = _recent_scalar(weight_map, today, domain="weight", provider_by_day=weight_providers)
                weight_aggregates = _aggregate_scalar_history(
                    weight_map, weight_start, today, domain="weight", provider_by_day=weight_providers,
                )

        activity_status = "disabled"
        activity_daily: tuple[tuple[date, Decimal | None], ...] = ()
        activity_detail: tuple[ScalarDailyPoint, ...] = ()
        activity_aggregates: tuple[ScalarHistoryAggregate, ...] = ()
        if "activity" in enabled:
            activity_start, activity_daily, activity_providers = self._activity_history(today)
            activity_status = "available" if any(value is not None for _, value in activity_daily) else "no_records"
            if activity_start is not None:
                coverage_starts.append(activity_start)
                activity_map = {day: value for day, value in activity_daily}
                activity_detail = _recent_scalar(activity_map, today, domain="activity", provider_by_day=activity_providers)
                activity_aggregates = _aggregate_scalar_history(
                    activity_map, activity_start, today, domain="activity", provider_by_day=activity_providers,
                )

        profile = None
        health_context = None
        if "profile" in enabled or "health_context" in enabled:
            profile_row = self._profile(
                include_profile="profile" in enabled,
                include_health_context="health_context" in enabled,
            )
            if profile_row is not None and "profile" in enabled:
                birth = cast(date | None, profile_row.get("birth_date"))
                age = (
                    today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
                    if birth is not None and birth <= today else None
                )
                profile = ExpertProfileContext(
                    evidence_id="profile:current",
                    gender=profile_row.get("gender"),
                    age_years=age,
                    height_cm=profile_row.get("height_cm"),
                    diet_type=profile_row.get("diet_type"),
                )
            if profile_row is not None and "health_context" in enabled:
                health_context = ExpertHealthContext(
                    evidence_id="health_context:current",
                    notes=profile_row.get("health_notes"),
                    intolerances=profile_row.get("intolerances"),
                )

        return ExpertContext(
            timezone=self._owner.timezone,
            coverage_start=min(coverage_starts) if coverage_starts else today,
            coverage_end=today,
            coverage_complete=True,
            older_history_resolution="year",
            recent_detail_start=today - timedelta(days=RECENT_DETAIL_DAYS - 1),
            daily_nutrition=daily,
            aggregates=aggregates,
            micronutrients=micronutrients,
            nutrition_status=nutrition_status,
            provider_coverage=provider_coverage,
            targets_status=targets_status,
            weight_status=weight_status,
            activity_status=activity_status,
            weight_daily=weight_daily,
            weight_aggregates=weight_aggregates,
            activity_daily=activity_detail,
            activity_aggregates=activity_aggregates,
            target_periods=targets,
            profile=profile,
            health_context=health_context,
            limitations=(
                "Historical nutrition is grouped by calendar year before the most recent ten years."
                " Meal names are included only for the recent 31-day detail when enabled.",
                "Target comparisons use base targets without historical activity credits. They are not effective budget or energy-deficit calculations.",
                "Activity follows the currently selected canonical provider only; other provider histories are not included. Scalar provider changes are annotated and may explain apparent trends.",
            ),
        )

    def _nutrition_history(
        self, today: date, preferences: ExpertPreferencesView, target_rows: list[_Target],
    ) -> tuple[
        date | None, str, tuple[ProviderCoverage, ...], tuple[HistoryAggregate, ...],
        tuple[DailyNutritionDetail, ...], tuple[MicronutrientCoverage, ...],
    ]:
        try:
            selection = resolve_nutrition_provider(
                self._db, user_id=self._owner.id, include_analysis_demo=True,
            )
        except NutritionProviderSelectionError:
            raise ExpertContextProblem("nutrition_provider_unavailable") from None
        if selection is None:
            return None, "no_records", (), (), (), ()

        sources = selection.provider_sources or ((selection.provider_key, selection.source_instance_id),)
        bounds: list[tuple[date | None, date | None]] = []
        for table in (NutritionConsumptionEvent, NutritionSourceObservation):
            bounds_row = self._db.execute(
                select(func.min(table.local_date), func.max(table.local_date)).where(
                    or_(*[
                        and_(
                            table.user_id == self._owner.id,
                            table.provider_key == provider_key,
                            table.source_instance_id == source_instance,
                            table.local_date.is_not(None),
                            table.local_date <= today,
                        )
                        for provider_key, source_instance in sources
                    ])
                )
            ).one()
            bounds.append((bounds_row[0], bounds_row[1]))
        candidates = [bound for pair in bounds for bound in pair if bound is not None]
        if not candidates:
            return None, "no_records", (), (), (), ()
        start, candidate_end = min(candidates), max(candidates)
        if candidate_end > today:
            raise ExpertContextProblem("nutrition_history_incomplete")
        self._check_span(start, today)
        # The selected source candidates determine the complete start boundary;
        # the context period ends at account-local today even when recent days are empty.
        raw_days: dict[date, tuple[str | None, dict[str, Decimal | None], tuple[str, ...]]] = {}
        nutrient_totals: dict[str, Decimal] = defaultdict(Decimal)
        nutrient_counts: dict[str, int] = defaultdict(int)
        nutrient_units = {item.metric_type: item.unit for item in MICRONUTRIENTS}
        for chunk_start, chunk_end in _chunks(start, today):
            try:
                canonical = read_analysis_nutrition(
                    self._db, user_id=self._owner.id, selection=selection,
                    start=chunk_start, end=chunk_end, include_meals=False,
                )
                expected = (chunk_end - chunk_start).days + 1
                if len(canonical.days) != expected:
                    raise AnalysisNutritionReadError("incomplete canonical period")
                for row in canonical.days:
                    raw_days[row.date] = (
                        row.provider_key,
                        {metric: getattr(row, metric) for metric in NUTRITION_METRICS},
                        (),
                    )
                micronutrient_result = read_analysis_micronutrients(
                    self._db, user_id=self._owner.id, selection=selection,
                    start=chunk_start, end=chunk_end, metric_key=None,
                )
                if len(micronutrient_result.metrics) != len(MICRONUTRIENTS):
                    raise AnalysisMicronutrientReadError("incomplete canonical nutrient period")
                for metric_result in micronutrient_result.metrics:
                    metric = metric_result.metric_key
                    if metric_result.total is not None:
                        nutrient_totals[metric] += metric_result.total
                    nutrient_counts[metric] += metric_result.days_with_data
            except (AnalysisNutritionReadError, AnalysisMicronutrientReadError, ValueError, TypeError, KeyError):
                raise ExpertContextProblem("nutrition_history_incomplete") from None

        recent_start = today - timedelta(days=RECENT_DETAIL_DAYS - 1)
        meal_names: dict[date, tuple[str, ...]] = {}
        if preferences.include_meal_names:
            try:
                recent = read_analysis_nutrition(
                    self._db, user_id=self._owner.id, selection=selection,
                    start=recent_start, end=today, include_meals=True,
                )
                if len(recent.days) != RECENT_DETAIL_DAYS:
                    raise AnalysisNutritionReadError("incomplete recent canonical period")
                for row in recent.days:
                    names = tuple(meal.display_name for meal in row.meals)
                    meal_names[row.date] = names
                    raw_days[row.date] = (
                        row.provider_key,
                        {metric: getattr(row, metric) for metric in NUTRITION_METRICS},
                        names,
                    )
            except (AnalysisNutritionReadError, ValueError, TypeError, KeyError):
                raise ExpertContextProblem("nutrition_recent_detail_incomplete") from None

        return (
            start,
            "available",
            _provider_coverage(raw_days),
            _aggregate_history(raw_days, start, today, target_rows),
            self._recent_detail(raw_days, today, target_rows=target_rows),
            tuple(
                MicronutrientCoverage(
                    evidence_id=f"nutrition:micronutrient:{metric}:all-available",
                    metric=metric, unit=nutrient_units[metric],
                    total=nutrient_totals.get(metric) if nutrient_counts.get(metric, 0) else None,
                    days_with_data=nutrient_counts.get(metric, 0),
                )
                for metric in nutrient_units
            ),
        )

    def _target_history(
        self, today: date,
    ) -> tuple[date | None, str, tuple[TargetPeriod, ...], list[_Target]]:
        # Activity configuration columns are deliberately not selected: this
        # reader only needs nutrition targets and never derives effective budgets.
        rows = list(self._db.execute(select(
            NutritionTarget.valid_from,
            NutritionTarget.valid_to,
            NutritionTarget.calories_kcal,
            NutritionTarget.maintenance_kcal,
            NutritionTarget.protein_g,
            NutritionTarget.carbs_g,
            NutritionTarget.fat_g,
            NutritionTarget.fiber_g,
        ).where(
            NutritionTarget.user_id == self._owner.id,
            NutritionTarget.valid_from <= today,
        ).order_by(NutritionTarget.valid_from)))
        if not rows:
            return None, "no_records", (), []
        periods = tuple(
            TargetPeriod(
                evidence_id=(
                    f"targets:{row.valid_from.isoformat()}:{row.valid_to.isoformat() if row.valid_to else 'open'}"
                ),
                valid_from=row.valid_from,
                valid_to=row.valid_to,
                calories_kcal=row.calories_kcal,
                maintenance_kcal=row.maintenance_kcal,
                protein_g=row.protein_g,
                carbs_g=row.carbs_g,
                fat_g=row.fat_g,
                fiber_g=row.fiber_g,
            )
            for row in rows
        )
        targets = [_Target(*row) for row in rows]
        return rows[0].valid_from, "available", periods, targets

    def _weight_history(self, today: date) -> tuple[date | None, tuple[tuple[date, Decimal], ...], dict[date, str]]:
        try:
            bounds = weight_history_bounds(self._db, user_id=self._owner.id)
            if bounds is None:
                return None, (), {}
            start, latest = bounds
            latest = min(latest, today)
            self._check_span(start, today)
            history = read_weight_history(self._db, user_id=self._owner.id, start=start, end=today)
            return start, tuple((day, value) for day, value in history.points if day <= latest), dict(history.providers)
        except ScalarProviderSelectionError:
            raise ExpertContextProblem("weight_provider_unavailable") from None
        except (ValueError, TypeError, KeyError, RuntimeError):
            raise ExpertContextProblem("weight_history_unavailable") from None

    def _activity_history(self, today: date) -> tuple[date | None, tuple[tuple[date, Decimal | None], ...], dict[date, str]]:
        try:
            selection = resolve_scalar_provider(
                self._db, user_id=self._owner.id, data_area=ACTIVITY_ENERGY_DATA_AREA,
                source_types=ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS,
            )
        except ScalarProviderSelectionError:
            raise ExpertContextProblem("activity_provider_unavailable") from None
        if selection is None:
            return None, (), {}
        try:
            filters, source_identifier = _activity_candidate_filters(self._db, self._owner.id, selection.provider_key, selection.source_type)
            bounds = self._db.execute(select(
                func.min(HealthSample.local_date), func.max(HealthSample.local_date),
            ).where(
                HealthSample.user_id == self._owner.id,
                HealthSample.metric_type == ACTIVE_ENERGY_METRIC,
                HealthSample.local_date <= today,
                *filters,
            )).one()
            if bounds[0] is None:
                return None, (), {}
            start = bounds[0]
            self._check_span(start, today)
            values: list[tuple[date, Decimal | None]] = []
            for chunk_start, chunk_end in _chunks(start, today):
                response = read_activity_verification(
                    self._db, user_id=self._owner.id, start=chunk_start,
                    end=chunk_end, view="canonical", canonical_source_identifier=source_identifier,
                )
                if len(response.days) != (chunk_end - chunk_start).days + 1:
                    raise ExpertContextProblem("activity_history_incomplete")
                for row in response.days:
                    record = row.canonical
                    if record is None or record.status == "unavailable":
                        raise ExpertContextProblem("activity_history_unavailable")
                    value = record.active_energy_kcal
                    values.append((row.date, value))
            return start, tuple(values), {day: selection.provider_key for day, value in values if value is not None}
        except ExpertContextProblem:
            raise
        except (ValueError, TypeError, KeyError, RuntimeError):
            raise ExpertContextProblem("activity_history_unavailable") from None

    def _profile(
        self, *, include_profile: bool, include_health_context: bool,
    ) -> dict[str, object] | None:
        cols: list[Any] = []
        names: list[str] = []
        if include_profile:
            cols.extend((UserProfile.gender, UserProfile.birth_date, UserProfile.height_cm, UserProfile.diet_type))
            names.extend(("gender", "birth_date", "height_cm", "diet_type"))
        if include_health_context:
            cols.extend((UserProfile.health_notes, UserProfile.intolerances))
            names.extend(("health_notes", "intolerances"))
        row = self._db.execute(select(*cols).where(UserProfile.user_id == self._owner.id)).one_or_none()
        if row is None:
            return None
        return dict(zip(names, row, strict=True))

    def _recent_detail(
        self, rows: dict[date, tuple[str | None, dict[str, Decimal | None], tuple[str, ...]]],
        today: date, *, target_rows: list[_Target],
    ) -> tuple[DailyNutritionDetail, ...]:
        start = today - timedelta(days=RECENT_DETAIL_DAYS - 1)
        result = []
        for offset in range(RECENT_DETAIL_DAYS):
            day = start + timedelta(days=offset)
            provider, metrics, names = rows.get(day, (None, {key: None for key in NUTRITION_METRICS}, ()))
            target = _target_for(target_rows, day)
            target_calories = target.calories_kcal if target is not None else None
            calories = metrics["dietary_energy_kcal"]
            result.append(DailyNutritionDetail(
                evidence_id=f"nutrition:day:{day.isoformat()}",
                date=day,
                provider_key=provider,
                values=NutritionValues(**metrics),
                meal_names=names,
                target_calories_kcal=target_calories,
                calories_vs_target_kcal=(
                    calories - target_calories
                    if calories is not None and target_calories is not None else None
                ),
            ))
        return tuple(result)

    @staticmethod
    def _check_span(start: date, end: date) -> None:
        if start > end or (end - start).days > MAX_CONTEXT_HISTORY_DAYS:
            raise ExpertContextProblem("history_exceeds_supported_span")


def _chunks(start: date, end: date) -> Iterator[tuple[date, date]]:
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=READ_CHUNK_DAYS - 1), end)
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


def _period_start(day: date, granularity: str) -> date:
    return date(day.year, day.month, 1) if granularity == "month" else date(day.year, 1, 1)


def _period_end(start: date, granularity: str) -> date:
    if granularity == "month":
        return (date(start.year + (start.month == 12), 1 if start.month == 12 else start.month + 1, 1)
                - timedelta(days=1))
    return date(start.year, 12, 31)


def _aggregate_history(
    rows: dict[date, tuple[str | None, dict[str, Decimal | None], tuple[str, ...]]],
    coverage_start: date, today: date, target_rows: list[_Target],
) -> tuple[HistoryAggregate, ...]:
    result = []
    for granularity, start, end in _summary_ranges(coverage_start, today):
        keys = [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
        metrics = []
        for metric in NUTRITION_METRICS:
            values = [rows.get(day, (None, {}, ()))[1].get(metric) for day in keys]
            present = [value for value in values if value is not None]
            total = sum(present, Decimal()) if present else None
            metrics.append(MetricAggregate(
                metric=metric, count=len(present), total=total,
                mean=total / len(present) if total is not None else None,
            ))
        nutrition_days = [day for day in keys if any(
            value is not None for value in rows.get(day, (None, {}, ()))[1].values()
        )]
        provider_counts: dict[str, int] = defaultdict(int)
        for day in keys:
            provider = rows.get(day, (None, {}, ()))[0]
            if provider is not None:
                provider_counts[provider] += 1
        target_values: list[Decimal] = []
        calorie_differences: list[Decimal] = []
        for day in keys:
            target = _target_for(target_rows, day)
            calories = rows.get(day, (None, {}, ()))[1].get("dietary_energy_kcal")
            if target is not None:
                target_values.append(target.calories_kcal)
                if calories is not None:
                    calorie_differences.append(calories - target.calories_kcal)
        result.append(HistoryAggregate(
            evidence_id=(f"nutrition:{granularity}:{start.isoformat()}:{end.isoformat()}"),
            granularity=granularity, start=start, end=end,
            calendar_days=len(keys), nutrition_days_with_data=len(nutrition_days),
            metrics=tuple(metrics),
            providers=tuple(ProviderCoverage(provider_key=key, days_with_data=count)
                            for key, count in sorted(provider_counts.items())),
            target_days=len(target_values),
            target_calories_kcal=sum(target_values, Decimal()) if target_values else None,
            calories_vs_target_kcal=(
                sum(calorie_differences, Decimal()) if calorie_differences else None
            ),
        ))
    return tuple(result)


def _summary_ranges(start: date, today: date) -> tuple[tuple[str, date, date], ...]:
    monthly_start = max(start, date(today.year - MONTHLY_HISTORY_YEARS, today.month, 1))
    groups: list[tuple[str, date, date]] = []
    cursor = start
    while cursor <= today:
        granularity = "month" if cursor >= monthly_start else "year"
        period_start = _period_start(cursor, granularity)
        period_end = min(_period_end(period_start, granularity), today)
        if granularity == "year" and period_end >= monthly_start:
            period_end = monthly_start - timedelta(days=1)
        groups.append((granularity, max(period_start, start), period_end))
        cursor = period_end + timedelta(days=1)
    return tuple(groups)


def _recent_scalar(
    values: Mapping[date, Decimal | None], today: date, *, domain: str, provider_by_day: Mapping[date, str] | None = None,
) -> tuple[ScalarDailyPoint, ...]:
    start = today - timedelta(days=RECENT_DETAIL_DAYS - 1)
    return tuple(
        ScalarDailyPoint(
            evidence_id=f"{domain}:day:{(start + timedelta(days=offset)).isoformat()}",
            date=start + timedelta(days=offset), value=values.get(start + timedelta(days=offset)),
            provider_key=(provider_by_day or {}).get(start + timedelta(days=offset)),
        )
        for offset in range(RECENT_DETAIL_DAYS)
    )


def _aggregate_scalar_history(
    values: Mapping[date, Decimal | None], start: date, today: date, *, domain: str, provider_by_day: Mapping[date, str] | None = None,
) -> tuple[ScalarHistoryAggregate, ...]:
    result = []
    for granularity, period_start, period_end in _summary_ranges(start, today):
        current = [values.get(period_start + timedelta(days=offset))
                   for offset in range((period_end - period_start).days + 1)]
        present = [value for value in current if value is not None]
        providers: dict[str, int] = defaultdict(int)
        for offset, value in enumerate(current):
            provider = (provider_by_day or {}).get(period_start + timedelta(days=offset))
            if value is not None and provider is not None:
                providers[provider] += 1
        result.append(ScalarHistoryAggregate(
            evidence_id=f"{domain}:{granularity}:{period_start.isoformat()}:{period_end.isoformat()}",
            granularity=granularity, start=period_start, end=period_end,
            measurement_count=len(present),
            providers=tuple(ProviderCoverage(provider_key=provider, days_with_data=count) for provider, count in sorted(providers.items())),
            total=sum(present, Decimal()) if present else None,
            mean=(sum(present, Decimal()) / len(present)) if present else None,
            minimum=min(present) if present else None,
            maximum=max(present) if present else None,
        ))
    return tuple(result)


def _provider_coverage(
    rows: dict[date, tuple[str | None, dict[str, Decimal | None], tuple[str, ...]]],
) -> tuple[ProviderCoverage, ...]:
    counts: dict[str, int] = defaultdict(int)
    for provider, values, _ in rows.values():
        if provider is not None and any(value is not None for value in values.values()):
            counts[provider] += 1
    return tuple(ProviderCoverage(provider_key=key, days_with_data=count) for key, count in sorted(counts.items()))


def _target_for(targets: list[_Target], day: date) -> _Target | None:
    return next((target for target in reversed(targets)
                 if target.valid_from <= day and (target.valid_to is None or day < target.valid_to)), None)


def _activity_candidate_filters(
    db: Session, user_id: UUID, provider_key: str, source_type: str,
) -> tuple[list[ColumnElement[bool]], str | None]:
    source_identifier: str | None = None
    filters: list[ColumnElement[bool]] = [HealthSample.source_type == source_type]
    if provider_key == "yazio":
        yazio_connection = db.scalar(select(YazioConnection).where(YazioConnection.user_id == user_id))
        if yazio_connection is None:
            raise RuntimeError("selected activity source is unavailable")
        source_identifier = yazio_connection.source_identifier
        filters.append(HealthSample.source_identifier == source_identifier)
    elif provider_key == "google_health":
        google_source_id = db.scalar(select(GoogleHealthConnection.id).where(
            GoogleHealthConnection.user_id == user_id,
            GoogleHealthConnection.state == "active",
        ))
        if google_source_id is None:
            raise RuntimeError("selected activity source is unavailable")
        source_identifier = str(google_source_id)
        filters.append(HealthSample.source_identifier == source_identifier)
    elif provider_key == "withings":
        withings_source_id = db.scalar(select(WithingsConnection.id).where(
            WithingsConnection.user_id == user_id,
            WithingsConnection.state == "active",
        ))
        if withings_source_id is None:
            raise RuntimeError("selected activity source is unavailable")
        source_identifier = str(withings_source_id)
        filters.append(HealthSample.source_identifier == source_identifier)
    elif provider_key == "analysis_demo":
        source_identifier = str(demo_source_instance_id(user_id))
        filters.append(HealthSample.source_identifier == source_identifier)
    return filters, source_identifier
