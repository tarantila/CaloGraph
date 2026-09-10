from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from types import MappingProxyType
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from .contracts import ProviderCandidate
from .metrics import CANONICAL_METRICS, UNSUPPORTED_METRIC_KEYS


class ProviderNotAvailableError(LookupError):
    """The requested technical provider family has no registered resolver."""


class ProviderCandidateContractError(ValueError):
    """A resolver returned a candidate outside the requested boundary scope."""


class NutritionProviderResolver(Protocol):
    provider_key: str

    def resolve_metric(
        self,
        db: Session,
        *,
        user_id: UUID,
        source_instance_id: UUID,
        local_date: date,
        metric_key: str,
    ) -> ProviderCandidate: ...


class YazioProviderResolver:
    provider_key = "yazio"

    def resolve_metric(
        self,
        db: Session,
        *,
        user_id: UUID,
        source_instance_id: UUID,
        local_date: date,
        metric_key: str,
    ) -> ProviderCandidate:
        from app.services.yazio_nutrition_resolution import resolve_yazio_metric

        return resolve_yazio_metric(
            db,
            user_id=user_id,
            source_instance_id=source_instance_id,
            local_date=local_date,
            metric_key=metric_key,
        )


PROVIDER_RESOLVERS: Mapping[str, NutritionProviderResolver] = MappingProxyType(
    {"yazio": YazioProviderResolver()}
)


def _registry_or_default(
    resolver_registry: Mapping[str, NutritionProviderResolver] | None,
) -> Mapping[str, NutritionProviderResolver]:
    return PROVIDER_RESOLVERS if resolver_registry is None else resolver_registry


def _validate_metric_key(metric_key: str) -> None:
    if metric_key not in CANONICAL_METRICS and metric_key not in UNSUPPORTED_METRIC_KEYS:
        raise ValueError("unsupported metric")


def _validate_candidate_scope(
    candidate: object,
    *,
    provider_key: str,
    user_id: UUID,
    local_date: date,
    metric_key: str,
) -> ProviderCandidate:
    if not isinstance(candidate, ProviderCandidate):
        raise ProviderCandidateContractError("resolver must return a ProviderCandidate")
    for field_name, expected, actual in (
        ("provider_key", provider_key, candidate.provider_key),
        ("user_id", user_id, candidate.user_id),
        ("local_date", local_date, candidate.local_date),
        ("metric_key", metric_key, candidate.metric_key),
    ):
        if actual != expected:
            raise ProviderCandidateContractError(f"candidate {field_name} does not match request")
    return candidate


def resolve_provider_metric(
    db: Session,
    *,
    provider_key: str,
    user_id: UUID,
    source_instance_id: UUID,
    local_date: date,
    metric_key: str,
    resolver_registry: Mapping[str, NutritionProviderResolver] | None = None,
) -> ProviderCandidate:
    registry = _registry_or_default(resolver_registry)
    resolver = registry.get(provider_key)
    if resolver is None:
        raise ProviderNotAvailableError(f"provider resolver is not registered: {provider_key}")
    _validate_metric_key(metric_key)
    candidate = resolver.resolve_metric(
        db,
        user_id=user_id,
        source_instance_id=source_instance_id,
        local_date=local_date,
        metric_key=metric_key,
    )
    return _validate_candidate_scope(
        candidate,
        provider_key=provider_key,
        user_id=user_id,
        local_date=local_date,
        metric_key=metric_key,
    )


def collect_provider_candidates(
    db: Session,
    *,
    provider_keys: Sequence[str],
    user_id: UUID,
    source_instance_id: UUID,
    local_date: date,
    metric_key: str,
    resolver_registry: Mapping[str, NutritionProviderResolver] | None = None,
) -> Mapping[str, ProviderCandidate]:
    requested_keys = tuple(provider_keys)
    if len(requested_keys) != len(set(requested_keys)):
        raise ValueError("duplicate provider key")
    return {
        provider_key: resolve_provider_metric(
            db,
            provider_key=provider_key,
            user_id=user_id,
            source_instance_id=source_instance_id,
            local_date=local_date,
            metric_key=metric_key,
            resolver_registry=resolver_registry,
        )
        for provider_key in requested_keys
    }
