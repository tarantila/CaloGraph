"""Bounded provider-scoped period reads for canonical nutrition serving."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date, timedelta
from types import MappingProxyType
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from .contracts import ProviderCandidate
from .metrics import CANONICAL_NUTRITION_METRICS
from .providers import ProviderNotAvailableError

_MAX_PERIOD_DAYS = 31


class NutritionPeriodResolver(Protocol):
    provider_key: str

    def resolve_period(
        self,
        db: Session,
        *,
        user_id: UUID,
        source_instance_id: UUID,
        start: date,
        end: date,
        metric_keys: Sequence[str],
    ) -> Mapping[date, Mapping[str, ProviderCandidate]]: ...


PeriodCandidates = Mapping[date, Mapping[str, ProviderCandidate]]
class _FunctionPeriodResolver:
    def __init__(
        self,
        provider_key: str,
        reader: Callable[..., Mapping[date, Mapping[str, ProviderCandidate]]],
    ) -> None:
        self.provider_key = provider_key
        self._reader = reader

    def resolve_period(
        self,
        db: Session,
        *,
        user_id: UUID,
        source_instance_id: UUID,
        start: date,
        end: date,
        metric_keys: Sequence[str],
    ) -> Mapping[date, Mapping[str, ProviderCandidate]]:
        return self._reader(
            db,
            user_id=user_id,
            source_instance_id=source_instance_id,
            start=start,
            end=end,
            metric_keys=metric_keys,
        )




def _normalize_metric_keys(metric_keys: Sequence[str] | None) -> tuple[str, ...]:
    if metric_keys is None:
        return tuple(CANONICAL_NUTRITION_METRICS)
    requested = tuple(metric_keys)
    if len(requested) != len(set(requested)):
        raise ValueError("duplicate metric key")
    unknown = tuple(key for key in requested if key not in CANONICAL_NUTRITION_METRICS)
    if unknown:
        raise ValueError("metric key must be canonical nutrition metric")
    requested_set = set(requested)
    return tuple(key for key in CANONICAL_NUTRITION_METRICS if key in requested_set)


def _normalize_provider_key(provider_key: str) -> str:
    if not isinstance(provider_key, str):
        raise ValueError("provider_key must be a string")
    normalized = provider_key.strip().lower()
    if not normalized or any(character.isspace() for character in normalized):
        raise ValueError("provider_key must be a non-empty token")
    return normalized


def _default_registry() -> Mapping[str, NutritionPeriodResolver]:
    from app.services.apple_health_nutrition_resolution import resolve_apple_health_period
    from app.services.google_health_nutrition_resolution import resolve_google_health_period
    from app.services.yazio_nutrition_resolution import resolve_yazio_period

    return MappingProxyType(
        {
            "apple_health": _FunctionPeriodResolver("apple_health", resolve_apple_health_period),
            "google_health": _FunctionPeriodResolver("google_health", resolve_google_health_period),
            "yazio": _FunctionPeriodResolver("yazio", resolve_yazio_period),
        }
    )


def _validate_candidate(
    candidate: object,
    *,
    provider_key: str,
    user_id: UUID,
    local_date: date,
    metric_key: str,
) -> ProviderCandidate:
    if not isinstance(candidate, ProviderCandidate):
        raise ValueError("period resolver must return ProviderCandidate values")
    for field_name, expected, actual in (
        ("provider_key", provider_key, candidate.provider_key),
        ("user_id", user_id, candidate.user_id),
        ("local_date", local_date, candidate.local_date),
        ("metric_key", metric_key, candidate.metric_key),
    ):
        if actual != expected:
            raise ValueError(f"period candidate {field_name} does not match request")
    return candidate


def _validate_result(
    result: object,
    *,
    provider_key: str,
    user_id: UUID,
    start: date,
    end: date,
    metric_keys: tuple[str, ...],
) -> PeriodCandidates:
    if not isinstance(result, Mapping):
        raise ValueError("period resolver must return a date mapping")
    expected_dates = tuple(start + timedelta(days=offset) for offset in range((end - start).days + 1))
    validated: dict[date, Mapping[str, ProviderCandidate]] = {}
    for local_date in expected_dates:
        day_result = result.get(local_date)
        if not isinstance(day_result, Mapping):
            raise ValueError("period resolver must return one metric mapping per day")
        if set(day_result) != set(metric_keys):
            raise ValueError("period resolver must return the requested metric set for every day")
        validated[local_date] = MappingProxyType(
            {
                metric_key: _validate_candidate(
                    day_result[metric_key],
                    provider_key=provider_key,
                    user_id=user_id,
                    local_date=local_date,
                    metric_key=metric_key,
                )
                for metric_key in metric_keys
            }
        )
    if set(result) != set(expected_dates):
        raise ValueError("period resolver returned dates outside the requested range")
    return MappingProxyType(validated)


def resolve_provider_period(
    db: Session,
    *,
    provider_key: str,
    user_id: UUID,
    source_instance_id: UUID,
    start: date,
    end: date,
    metric_keys: Sequence[str] | None = None,
    resolver_registry: Mapping[str, NutritionPeriodResolver] | None = None,
) -> PeriodCandidates:
    """Resolve a complete canonical metric matrix with one bounded provider read."""
    if type(start) is not date or type(end) is not date or start > end:
        raise ValueError("period range must contain dates in ascending order")
    if (end - start).days + 1 > _MAX_PERIOD_DAYS:
        raise ValueError("period range must not exceed 31 days")
    requested_metrics = _normalize_metric_keys(metric_keys)
    normalized_provider_key = _normalize_provider_key(provider_key)
    registry = _default_registry() if resolver_registry is None else resolver_registry
    resolver = registry.get(normalized_provider_key)
    if resolver is None or not callable(getattr(resolver, "resolve_period", None)):
        raise ProviderNotAvailableError(
            f"period resolver is not registered: {normalized_provider_key}"
        )
    result = resolver.resolve_period(
        db,
        user_id=user_id,
        source_instance_id=source_instance_id,
        start=start,
        end=end,
        metric_keys=requested_metrics,
    )
    return _validate_result(
        result,
        provider_key=normalized_provider_key,
        user_id=user_id,
        start=start,
        end=end,
        metric_keys=requested_metrics,
    )


__all__ = ["NutritionPeriodResolver", "PeriodCandidates", "resolve_provider_period"]
