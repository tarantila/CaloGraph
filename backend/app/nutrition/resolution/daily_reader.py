"""Provider-scoped read facade for the canonical daily nutrient catalog."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from uuid import UUID

from sqlalchemy.orm import Session

from .contracts import ProviderCandidate
from .metrics import CANONICAL_NUTRITION_METRICS
from .providers import NUTRIENT_READ_PROVIDER_RESOLVERS, resolve_provider_metric


def _requested_metric_keys(metric_keys: Iterable[str] | None) -> tuple[str, ...]:
    if metric_keys is None:
        return tuple(CANONICAL_NUTRITION_METRICS)
    if isinstance(metric_keys, (str, bytes, bytearray)):
        raise ValueError("metric_keys must be an iterable of metric keys")
    requested = tuple(metric_keys)
    if not all(isinstance(key, str) for key in requested):
        raise ValueError("metric key must be a string")
    if len(requested) != len(set(requested)):
        raise ValueError("duplicate metric key")
    unknown = tuple(key for key in requested if key not in CANONICAL_NUTRITION_METRICS)
    if unknown:
        raise ValueError("metric key must be canonical nutrition metric")
    requested_set = set(requested)
    return tuple(key for key in CANONICAL_NUTRITION_METRICS if key in requested_set)

def resolve_daily_nutrient(
    db: Session,
    *,
    provider_key: str,
    user_id: UUID,
    source_instance_id: UUID,
    local_date: date,
    metric_key: str,
) -> ProviderCandidate:
    """Resolve one canonical nutrient without selecting across providers."""
    if not isinstance(metric_key, str) or metric_key not in CANONICAL_NUTRITION_METRICS:
        raise ValueError("metric key must be canonical nutrition metric")
    return resolve_provider_metric(
        db,
        provider_key=provider_key,
        user_id=user_id,
        source_instance_id=source_instance_id,
        local_date=local_date,
        metric_key=metric_key,
        resolver_registry=NUTRIENT_READ_PROVIDER_RESOLVERS,
    )


def resolve_daily_nutrients(
    db: Session,
    *,
    provider_key: str,
    user_id: UUID,
    source_instance_id: UUID,
    local_date: date,
    metric_keys: Iterable[str] | None = None,
) -> tuple[ProviderCandidate, ...]:
    """Resolve a bounded, deterministically ordered canonical metric set."""
    return tuple(
        resolve_daily_nutrient(
            db,
            provider_key=provider_key,
            user_id=user_id,
            source_instance_id=source_instance_id,
            local_date=local_date,
            metric_key=metric_key,
        )
        for metric_key in _requested_metric_keys(metric_keys)
    )


__all__ = ["resolve_daily_nutrient", "resolve_daily_nutrients"]
