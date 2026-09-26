from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal, cast
from uuid import UUID

from sqlalchemy.orm import Session

from app.analytics.provider_selection import NutritionProviderSelection
from app.micronutrients import MICRONUTRIENT_BY_METRIC, MICRONUTRIENTS
from app.nutrition.enums import CoverageState, PresenceState, ResolutionState
from app.nutrition.resolution import (
    DAILY_PROJECTION_METRICS,
    MAX_PROVIDER_PERIOD_DAYS,
    ProviderCandidate,
    resolve_provider_period,
)
from app.nutrition.resolution.providers import ProviderNotAvailableError
from app.schemas import (
    AnalysisMicronutrientContributorResponse,
    AnalysisMicronutrientDayResponse,
    AnalysisMicronutrientMetricResponse,
    AnalysisMicronutrientResponse,
    AnalysisSelectedProviderResponse,
)


class AnalysisMicronutrientReadError(RuntimeError):
    """A canonical micronutrient period cannot be returned safely."""


def _usable(candidate: ProviderCandidate) -> Decimal | None:
    if candidate.resolution_state in {
        ResolutionState.CONFLICT,
        ResolutionState.DUPLICATE_CANDIDATE,
    }:
        raise AnalysisMicronutrientReadError("unsafe canonical nutrient candidate")
    if (
        candidate.coverage_state is CoverageState.COMPLETE
        and candidate.resolution_state is ResolutionState.RESOLVED
        and candidate.presence_state in {PresenceState.SUPPLIED, PresenceState.EXPLICIT_ZERO}
    ):
        return candidate.value
    return None


def _has_primary_evidence(day: Mapping[str, ProviderCandidate]) -> bool:
    for metric_key in DAILY_PROJECTION_METRICS:
        candidate = day[metric_key]
        if candidate.resolution_state in {
            ResolutionState.CONFLICT,
            ResolutionState.DUPLICATE_CANDIDATE,
        }:
            raise AnalysisMicronutrientReadError("unsafe canonical primary candidate")
        if (
            candidate.coverage_state is not CoverageState.COMPLETE
            or candidate.resolution_state is not ResolutionState.RESOLVED
        ):
            return False
    return any(_usable(day[key]) is not None for key in DAILY_PROJECTION_METRICS)


def read_analysis_micronutrients(
    db: Session,
    *,
    user_id: UUID,
    selection: NutritionProviderSelection,
    start: date,
    end: date,
    metric_key: str | None,
) -> AnalysisMicronutrientResponse:
    if start > end or (end - start).days >= MAX_PROVIDER_PERIOD_DAYS:
        raise AnalysisMicronutrientReadError("canonical range exceeds one chunk")
    if metric_key is not None and metric_key not in MICRONUTRIENT_BY_METRIC:
        raise AnalysisMicronutrientReadError("noncatalog metric")

    dates = tuple(start + timedelta(days=offset) for offset in range((end - start).days + 1))
    definitions = (MICRONUTRIENT_BY_METRIC[metric_key],) if metric_key else MICRONUTRIENTS
    metric_keys = (*DAILY_PROJECTION_METRICS, *(item.metric_type for item in definitions))
    sources = selection.provider_sources or ((selection.provider_key, selection.source_instance_id),)
    if not sources or len({provider_key for provider_key, _ in sources}) != len(sources):
        raise AnalysisMicronutrientReadError("invalid canonical provider source chain")

    selected: dict[date, tuple[str, Mapping[str, ProviderCandidate]]] = {}
    first: dict[date, tuple[str, Mapping[str, ProviderCandidate]]] = {}
    for provider_key, source_instance_id in sources:
        if len(selected) == len(dates):
            break
        remaining = tuple(day for day in dates if day not in selected)
        try:
            matrix = resolve_provider_period(
                db,
                provider_key=provider_key,
                user_id=user_id,
                source_instance_id=source_instance_id,
                start=remaining[0],
                end=remaining[-1],
                metric_keys=metric_keys,
            )
        except (ProviderNotAvailableError, ValueError, KeyError, TypeError) as exc:
            raise AnalysisMicronutrientReadError("canonical provider period is unreadable") from exc

        for current in remaining:
            candidates = matrix[current]
            first.setdefault(current, (provider_key, candidates))
            for key in metric_keys:
                _usable(candidates[key])
            if _has_primary_evidence(candidates):
                selected[current] = (provider_key, candidates)

    winning: list[tuple[str, Mapping[str, ProviderCandidate]]] = []
    for current in dates:
        winner = selected.get(current) or first.get(current)
        if winner is None:
            raise AnalysisMicronutrientReadError("canonical provider returned no requested date")
        winning.append(winner)

    values_by_metric: dict[str, list[Decimal | None]] = {
        item.metric_type: [_usable(candidates[item.metric_type]) for _, candidates in winning]
        for item in definitions
    }
    metrics: list[AnalysisMicronutrientMetricResponse] = []
    for item in definitions:
        values = values_by_metric[item.metric_type]
        present = [value for value in values if value is not None]
        contributor_days = {provider_key: 0 for provider_key, _ in sources}
        for (provider_key, _), value in zip(winning, values, strict=True):
            if value is not None:
                contributor_days[provider_key] += 1
        metrics.append(
            AnalysisMicronutrientMetricResponse(
                metric_key=item.metric_type,
                label=item.label,
                category=cast(Literal["vitamin", "mineral"], item.category),
                unit=cast(Literal["mg", "ug"], item.unit),
                eu_nrv=item.eu_nrv,
                total=sum(present, Decimal()) if present else None,
                days_with_data=len(present),
                contributors=[
                    AnalysisMicronutrientContributorResponse(
                        provider_key=provider_key, days_with_data=count
                    )
                    for provider_key, count in contributor_days.items()
                    if count > 0
                ],
            )
        )

    return AnalysisMicronutrientResponse(
        start_date=start,
        end_date=end,
        selected_provider=AnalysisSelectedProviderResponse(provider_key=selection.provider_key),
        period_days=len(dates),
        metrics=metrics,
        series=(
            [
                AnalysisMicronutrientDayResponse(
                    date=current, provider_key=winner[0], value=value
                )
                for current, winner, value in zip(
                    dates, winning, values_by_metric[metric_key], strict=True
                )
            ]
            if metric_key is not None
            else []
        ),
    )
