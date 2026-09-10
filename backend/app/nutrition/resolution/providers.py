from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from types import MappingProxyType
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from .contracts import ProviderCandidate
from .metrics import CANONICAL_METRICS, UNSUPPORTED_METRIC_KEYS
from .sources import (
    ProviderSourceBindings,
    ProviderSourceInstance,
    normalize_provider_sources,
)


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


def _normalize_provider_key(provider_key: str) -> str:
    if not isinstance(provider_key, str):
        raise ValueError("provider_key must be a string")
    normalized = provider_key.strip().lower()
    if not normalized or any(character.isspace() for character in normalized):
        raise ValueError("provider_key must be a non-empty token")
    return normalized


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
    normalized_provider_key = _normalize_provider_key(provider_key)
    registry = _registry_or_default(resolver_registry)
    resolver = registry.get(normalized_provider_key)
    if resolver is None:
        raise ProviderNotAvailableError(
            f"provider resolver is not registered: {normalized_provider_key}"
        )
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
        provider_key=normalized_provider_key,
        user_id=user_id,
        local_date=local_date,
        metric_key=metric_key,
    )


def _requested_source_bindings(
    *,
    provider_keys: Sequence[str] | None,
    provider_sources: ProviderSourceBindings | Sequence[ProviderSourceInstance] | None,
    source_instance_id: UUID | None,
) -> ProviderSourceBindings:
    if provider_sources is not None:
        if source_instance_id is not None:
            raise ValueError("provider_sources and source_instance_id are mutually exclusive")
        bindings = normalize_provider_sources(provider_sources)
        if provider_keys is not None:
            requested_keys = tuple(_normalize_provider_key(key) for key in provider_keys)
            if set(requested_keys) != {binding.provider_key for binding in bindings}:
                raise ValueError("provider_keys do not match provider source bindings")
        return bindings

    if provider_keys is None:
        raise ValueError("provider_sources are required")
    requested_keys = tuple(_normalize_provider_key(key) for key in provider_keys)
    if len(requested_keys) != len(set(requested_keys)):
        raise ValueError("duplicate provider key")
    if source_instance_id is None:
        raise ValueError("provider_sources are required")
    if len(requested_keys) != 1:
        raise ValueError("legacy source_instance_id can bind only one provider")
    return ProviderSourceBindings(
        (ProviderSourceInstance(requested_keys[0], source_instance_id),)
    )


def collect_provider_candidates(
    db: Session,
    *,
    provider_keys: Sequence[str] | None = None,
    provider_sources: ProviderSourceBindings | Sequence[ProviderSourceInstance] | None = None,
    user_id: UUID,
    source_instance_id: UUID | None = None,
    local_date: date,
    metric_key: str,
    resolver_registry: Mapping[str, NutritionProviderResolver] | None = None,
) -> Mapping[str, ProviderCandidate]:
    bindings = _requested_source_bindings(
        provider_keys=provider_keys,
        provider_sources=provider_sources,
        source_instance_id=source_instance_id,
    )
    return {
        binding.provider_key: resolve_provider_metric(
            db,
            provider_key=binding.provider_key,
            user_id=user_id,
            source_instance_id=binding.source_instance_id,
            local_date=local_date,
            metric_key=metric_key,
            resolver_registry=resolver_registry,
        )
        for binding in bindings
    }
