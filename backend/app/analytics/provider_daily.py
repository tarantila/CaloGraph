from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from decimal import Decimal
from typing import Final
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.activity import ACTIVE_ENERGY_METRIC
from app.analytics.service import TrackingInputs, _build_daily_point
from app.models import HealthSample, NutritionTarget, TrackingOverride
from app.nutrition.enums import CoverageState, PresenceState, ResolutionState
from app.nutrition.resolution import (
    DAILY_PROJECTION_METRICS,
    ProviderCandidate,
    iter_provider_period_chunks,
)
from app.schemas import DailyPoint

_PRIMARY_DAILY_POINT_METRICS: Final[tuple[tuple[str, str], ...]] = (
    ("dietary_energy_kcal", "dietary_energy_kcal"),
    ("protein_g", "protein_g"),
    ("carbohydrates_g", "carbs_g"),
    ("fat_g", "fat_g"),
)


class ProviderDailyReadError(RuntimeError):
    """A selected provider cannot produce a safe canonical daily response."""


def _tracking_inputs(day_candidates: Mapping[str, ProviderCandidate]) -> TrackingInputs:
    has_primary_evidence = any(
        getattr(candidate, "presence_state", None)
        in {PresenceState.SUPPLIED, PresenceState.EXPLICIT_ZERO}
        for candidate in day_candidates.values()
    )
    calorie = day_candidates["dietary_energy_kcal"]
    calorie_usable = (
        getattr(calorie, "value", None) is not None
        and getattr(calorie, "coverage_state", None) is CoverageState.COMPLETE
        and getattr(calorie, "resolution_state", None) is ResolutionState.RESOLVED
    )
    if not has_primary_evidence:
        return TrackingInputs("no_data", 0, ("Keine Ernährungsdaten vorhanden",))
    if calorie_usable:
        return TrackingInputs("complete", 1, ("Kalorienwert vorhanden",))
    return TrackingInputs("incomplete", 0, ("Ernährungsdaten vorhanden, aber kein Kalorienwert",))


def _provider_has_evidence(day_candidates: Mapping[str, ProviderCandidate]) -> bool:
    return any(
        getattr(candidate, "presence_state", None)
        in {PresenceState.SUPPLIED, PresenceState.EXPLICIT_ZERO}
        for candidate in day_candidates.values()
    )


def _candidate_values(day_candidates: Mapping[str, ProviderCandidate]) -> dict[str, Decimal]:
    for metric_key in DAILY_PROJECTION_METRICS:
        resolution_state = getattr(day_candidates[metric_key], "resolution_state", None)
        if resolution_state in {ResolutionState.CONFLICT, ResolutionState.DUPLICATE_CANDIDATE}:
            raise ProviderDailyReadError(
                f"canonical provider returned unsafe {resolution_state.value} for {metric_key}"
            )

    values: dict[str, Decimal] = {}
    for metric_key, daily_point_field in _PRIMARY_DAILY_POINT_METRICS:
        candidate = day_candidates[metric_key]
        if (
            getattr(candidate, "value_contributing", False)
            and getattr(candidate, "coverage_state", None) is CoverageState.COMPLETE
            and getattr(candidate, "resolution_state", None) is ResolutionState.RESOLVED
            and getattr(candidate, "presence_state", None)
            in {PresenceState.SUPPLIED, PresenceState.EXPLICIT_ZERO}
        ):
            value = getattr(candidate, "value", None)
            if value is not None:
                values[daily_point_field] = value
    return values


def read_provider_daily_points(
    db: Session,
    *,
    user_id: UUID,
    start: date,
    end: date,
    provider_key: str | None = None,
    source_instance_id: UUID | None = None,
    provider_sources: Sequence[tuple[str, UUID]] | None = None,
) -> list[DailyPoint]:
    """Serve DailyPoints from an ordered set of owned providers."""
    if start > end:
        raise ProviderDailyReadError("provider daily range is invalid")
    if provider_sources is None:
        if provider_key is None or source_instance_id is None:
            raise ProviderDailyReadError("provider source is incomplete")
        provider_sources = ((provider_key, source_instance_id),)
    else:
        provider_sources = tuple(provider_sources)
        if not provider_sources:
            raise ProviderDailyReadError("provider source list is empty")
    if len({provider for provider, _source in provider_sources}) != len(provider_sources):
        raise ProviderDailyReadError("provider source list contains duplicates")

    candidates_by_provider_date: dict[
        tuple[str, date], Mapping[str, ProviderCandidate]
    ] = {}
    try:
        for current_provider_key, current_source_instance_id in provider_sources:
            for _, _, chunk_candidates in iter_provider_period_chunks(
                db,
                provider_key=current_provider_key,
                user_id=user_id,
                source_instance_id=current_source_instance_id,
                start=start,
                end=end,
                metric_keys=DAILY_PROJECTION_METRICS,
            ):
                candidates_by_provider_date.update(
                    {
                        (current_provider_key, local_date): candidates
                        for local_date, candidates in chunk_candidates.items()
                    }
                )
    except (ValueError, KeyError) as exc:
        raise ProviderDailyReadError("canonical provider returned an invalid period") from exc

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
        )
    }
    active_rows = db.execute(
        select(
            HealthSample.local_date,
            HealthSample.source_type,
            func.sum(HealthSample.value),
        )
        .where(
            HealthSample.user_id == user_id,
            HealthSample.local_date >= start,
            HealthSample.local_date <= end,
            HealthSample.metric_type == ACTIVE_ENERGY_METRIC,
        )
        .group_by(HealthSample.local_date, HealthSample.source_type)
    ).all()
    active_energy_by_source: dict[tuple[date, str], Decimal] = {
        (local_date, source_type): total for local_date, source_type, total in active_rows
    }
    active_energy_sources_by_day: dict[date, set[str]] = defaultdict(set)
    for local_date, source_type, _total in active_rows:
        active_energy_sources_by_day[local_date].add(source_type)

    points: list[DailyPoint] = []
    for offset in range((end - start).days + 1):
        local_date = start + timedelta(days=offset)
        fallback_candidates: Mapping[str, ProviderCandidate] | None = None
        day_candidates: Mapping[str, ProviderCandidate] | None = None
        for current_provider_key, _source_instance_id in provider_sources:
            candidates = candidates_by_provider_date.get((current_provider_key, local_date))
            if candidates is None:
                continue
            if fallback_candidates is None:
                fallback_candidates = candidates
            if _provider_has_evidence(candidates):
                day_candidates = candidates
                break
        if day_candidates is None:
            day_candidates = fallback_candidates
        if day_candidates is None:
            raise ProviderDailyReadError("canonical provider returned no data for a requested day")
        try:
            values = _candidate_values(day_candidates)
            tracking_inputs = _tracking_inputs(day_candidates)
        except (KeyError, TypeError) as exc:
            raise ProviderDailyReadError("canonical provider returned an incomplete metric scope") from exc
        points.append(
            _build_daily_point(
                day=local_date,
                values=values,
                tracking_inputs=tracking_inputs,
                active_energy_by_source=active_energy_by_source,
                active_energy_sources_by_day=active_energy_sources_by_day,
                targets=targets,
                override=overrides.get(local_date),
            )
        )
    return points


__all__ = ["ProviderDailyReadError", "read_provider_daily_points"]
