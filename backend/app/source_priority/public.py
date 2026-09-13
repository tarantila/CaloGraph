from __future__ import annotations

from datetime import UTC, datetime
from types import MappingProxyType
from typing import Final
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.nutrition.models import NutritionDailyProjection, NutritionProjectionHead
from app.nutrition.resolution.sources import DEFAULT_SOURCE_RESOLVERS, resolve_default_provider_sources
from app.source_priority.contracts import validate_aware_datetime
from app.source_priority.models import SourcePriorityRule
from app.source_priority.repositories import get_effective_policy, list_policies, list_rules

PUBLIC_NUTRITION_SOURCES: Final = MappingProxyType(
    {
        "yazio": MappingProxyType({"id": "yazio", "label": "YAZIO"}),
        "google_health": MappingProxyType({"id": "google_health", "label": "Google Health"}),
    }
)


def _public_source(
    provider_key: str,
    *,
    available: bool,
    rank: int | None,
) -> NutritionPrioritySource:
    definition = PUBLIC_NUTRITION_SOURCES[provider_key]
    return NutritionPrioritySource(
        id=definition["id"],
        label=definition["label"],
        available=available,
        rank=rank,
    )


def _source_list(
    available_provider_keys: set[str],
    configured_provider_keys: set[str],
    ranks: dict[str, int] | None = None,
) -> list[NutritionPrioritySource]:
    public_provider_keys = (available_provider_keys | configured_provider_keys) & set(
        PUBLIC_NUTRITION_SOURCES
    )
    available = available_provider_keys & set(PUBLIC_NUTRITION_SOURCES)
    if ranks is not None:
        ordered_keys = sorted(
            public_provider_keys,
            key=lambda provider_key: (
                0 if provider_key in ranks else 1,
                ranks.get(provider_key, 0),
                PUBLIC_NUTRITION_SOURCES[provider_key]["id"],
            ),
        )
    else:
        ordered_keys = sorted(
            public_provider_keys,
            key=lambda provider_key: PUBLIC_NUTRITION_SOURCES[provider_key]["id"],
        )
    return [
        _public_source(
            provider_key,
            available=provider_key in available,
            rank=ranks.get(provider_key) if ranks is not None and provider_key in ranks else None,
        )
        for provider_key in ordered_keys
    ]


def _is_public_global_policy(nutrition_rules: list[SourcePriorityRule]) -> bool:
    wildcard_rules = [rule for rule in nutrition_rules if rule.metric_key is None]
    if not wildcard_rules or any(rule.metric_key is not None for rule in nutrition_rules):
        return False
    ranks = [rule.priority_rank for rule in wildcard_rules]
    if len(set(ranks)) != len(ranks) or set(ranks) != set(range(1, len(ranks) + 1)):
        return False
    return all(rule.provider_key in PUBLIC_NUTRITION_SOURCES for rule in wildcard_rules)


def _projection_refresh_required(
    db: Session,
    user_id: UUID,
    active_policy_id: UUID | None,
) -> bool:
    if active_policy_id is None:
        return False
    statement = (
        select(NutritionProjectionHead.user_id)
        .join(
            NutritionDailyProjection,
            and_(
                NutritionDailyProjection.id == NutritionProjectionHead.current_projection_id,
                NutritionDailyProjection.user_id == NutritionProjectionHead.user_id,
                NutritionDailyProjection.local_date == NutritionProjectionHead.local_date,
            ),
        )
        .where(
            NutritionProjectionHead.user_id == user_id,
            NutritionDailyProjection.priority_policy_id != active_policy_id,
        )
        .limit(1)
    )
    return db.scalar(statement) is not None


def get_nutrition_priority_state(
    db: Session,
    user_id: UUID,
    *,
    at: datetime | None = None,
) -> NutritionPriorityState:
    evaluation_time = validate_aware_datetime(at or datetime.now(UTC))
    bindings = resolve_default_provider_sources(
        db,
        user_id=user_id,
        provider_keys=DEFAULT_SOURCE_RESOLVERS.keys(),
    )
    available_provider_keys = {binding.provider_key for binding in bindings}
    policies = list_policies(db, user_id)
    latest_policy = max(policies, key=lambda policy: (policy.version, str(policy.id))) if policies else None
    active_policy = get_effective_policy(db, user_id, evaluation_time)
    version = latest_policy.version if latest_policy is not None else None

    if active_policy is None:
        if not policies and not available_provider_keys:
            return NutritionPriorityState(
                status="no_providers",
                version=None,
                sources=[],
                configuration_mode="none",
                projection_refresh_required=False,
            )
        return NutritionPriorityState(
            status="configuration_required" if policies else "selection_required",
            version=version,
            sources=_source_list(available_provider_keys, set()),
            configuration_mode="none",
            projection_refresh_required=False,
        )

    all_nutrition_rules = list_rules(db, user_id, active_policy.id, data_area="nutrition")
    configured_provider_keys = {rule.provider_key for rule in all_nutrition_rules}
    has_metric_specific_rules = any(rule.metric_key is not None for rule in all_nutrition_rules)
    refresh_required = _projection_refresh_required(db, user_id, active_policy.id)

    if has_metric_specific_rules:
        return NutritionPriorityState(
            status="advanced_configuration",
            version=version,
            sources=_source_list(available_provider_keys, configured_provider_keys),
            configuration_mode="advanced",
            projection_refresh_required=refresh_required,
        )

    if _is_public_global_policy(all_nutrition_rules):
        ranks = {rule.provider_key: rule.priority_rank for rule in all_nutrition_rules}
        return NutritionPriorityState(
            status="configured",
            version=version,
            sources=_source_list(available_provider_keys, configured_provider_keys, ranks),
            configuration_mode="global",
            projection_refresh_required=refresh_required,
        )

    return NutritionPriorityState(
        status="configuration_required",
        version=version,
        sources=_source_list(available_provider_keys, configured_provider_keys),
        configuration_mode="none",
        projection_refresh_required=refresh_required,
    )


__all__ = ["PUBLIC_NUTRITION_SOURCES", "get_nutrition_priority_state"]
