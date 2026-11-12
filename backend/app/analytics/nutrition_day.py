"""Shared whole-day eligibility and value rules for canonical nutrition readers."""

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from app.analytics.service import PRIMARY_NUTRITION_METRICS
from app.nutrition.enums import CoverageState, PresenceState, ResolutionState
from app.nutrition.resolution import (
    DAILY_PROJECTION_METRICS,
    ProviderCandidate,
    resolve_provider_period,
)


def usable_nutrition_value(candidate: ProviderCandidate) -> Decimal | None:
    if candidate.resolution_state in {
        ResolutionState.CONFLICT,
        ResolutionState.DUPLICATE_CANDIDATE,
    }:
        raise ValueError("unsafe canonical nutrition candidate")
    if (
        candidate.coverage_state is CoverageState.COMPLETE
        and candidate.resolution_state is ResolutionState.RESOLVED
        and candidate.presence_state in {PresenceState.SUPPLIED, PresenceState.EXPLICIT_ZERO}
    ):
        return candidate.value
    return None


def has_valid_nutrition_day(candidates: Mapping[str, ProviderCandidate]) -> bool:
    """The first valid provider in priority order owns every metric of the day.

    All four core candidates must be complete and resolved, with at least one
    supplied core value (including zero). Secondary omissions never disqualify
    that provider. Unsafe evidence still fails closed before any fallback.
    """
    values = {key: usable_nutrition_value(candidate) for key, candidate in candidates.items()}
    return all(
        candidates[key].coverage_state is CoverageState.COMPLETE
        and candidates[key].resolution_state is ResolutionState.RESOLVED
        for key in PRIMARY_NUTRITION_METRICS
    ) and any(values[key] is not None for key in PRIMARY_NUTRITION_METRICS)


def read_nutrition_day_source(
    db: Session,
    *,
    user_id: UUID,
    provider_sources: Sequence[tuple[str, UUID]],
    local_date: date,
) -> tuple[str, UUID] | None:
    """Resolve the whole-day source for a single-day source/detail view."""
    for provider_key, source_instance_id in provider_sources:
        matrix = resolve_provider_period(
            db, user_id=user_id, provider_key=provider_key,
            source_instance_id=source_instance_id, start=local_date, end=local_date,
            metric_keys=DAILY_PROJECTION_METRICS,
        )
        if has_valid_nutrition_day(matrix[local_date]):
            return provider_key, source_instance_id
    return None
