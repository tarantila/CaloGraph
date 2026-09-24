from __future__ import annotations

from datetime import UTC, datetime
from types import MappingProxyType
from typing import ClassVar, Final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.google_health.constants import GOOGLE_HEALTH_REQUIRED_SCOPES
from app.google_health.credentials import resolve_google_health_credentials
from app.models import GoogleHealthConnection, User
from app.nutrition.projection.refresh import (
    DEFAULT_REFRESH_BATCH_SIZE,
    list_stale_nutrition_projection_dates,
)
from app.nutrition.resolution.discovery import discover_nutrition_providers
from app.nutrition.resolution.sources import (
    DEFAULT_SOURCE_RESOLVERS,
    resolve_default_provider_sources,
)
from app.schemas_source_priority import (
    NutritionPrioritySource,
    NutritionPriorityState,
    NutritionPriorityUpdateRequest,
)
from app.source_priority.application import create_policy_with_rules
from app.source_priority.contracts import PriorityRuleSpec, validate_aware_datetime
from app.source_priority.models import SourcePriorityPolicy, SourcePriorityRule
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


def _available_provider_keys(
    db: Session,
    *,
    user_id: UUID,
    provider_keys: set[str],
) -> set[str]:
    available_provider_keys = set(provider_keys)
    if "google_health" not in available_provider_keys:
        return available_provider_keys
    connection = db.scalar(
        select(GoogleHealthConnection).where(
            GoogleHealthConnection.user_id == user_id,
            GoogleHealthConnection.state == "active",
        )
    )
    if (
        not settings.google_health_enabled
        or connection is None
        or not connection.encrypted_refresh_token
        or not GOOGLE_HEALTH_REQUIRED_SCOPES.issubset(set(connection.granted_scopes or ()))
    ):
        available_provider_keys.discard("google_health")
        return available_provider_keys
    try:
        resolve_google_health_credentials(connection)
    except Exception:
        available_provider_keys.discard("google_health")
        return available_provider_keys
    evidenced_providers = {
        provider.provider_key
        for provider in discover_nutrition_providers(db, user_id=user_id).providers
    }
    if "google_health" not in evidenced_providers:
        available_provider_keys.discard("google_health")
    return available_provider_keys


def _is_public_global_policy(
    nutrition_rules: list[SourcePriorityRule],
    available_provider_keys: set[str],
) -> bool:
    wildcard_rules = [rule for rule in nutrition_rules if rule.metric_key is None]
    if not wildcard_rules or any(rule.metric_key is not None for rule in nutrition_rules):
        return False
    ranks = [rule.priority_rank for rule in wildcard_rules]
    if len(set(ranks)) != len(ranks) or set(ranks) != set(range(1, len(ranks) + 1)):
        return False
    configured_provider_keys = {rule.provider_key for rule in wildcard_rules}
    return (
        configured_provider_keys <= set(PUBLIC_NUTRITION_SOURCES)
        and available_provider_keys <= configured_provider_keys
    )


def _projection_refresh_required(
    db: Session,
    user_id: UUID,
    policy_id: UUID | None,
) -> bool:
    policy: SourcePriorityPolicy | None = None
    if policy_id is not None:
        policy = db.scalar(
            select(SourcePriorityPolicy).where(
                SourcePriorityPolicy.id == policy_id,
                SourcePriorityPolicy.user_id == user_id,
            )
        )
        if policy is None:
            return False
    return bool(
        list_stale_nutrition_projection_dates(
            db,
            user_id=user_id,
            policy=policy,
            limit=DEFAULT_REFRESH_BATCH_SIZE,
        )
    )


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
    provider_keys = {binding.provider_key for binding in bindings}
    available_provider_keys = _available_provider_keys(
        db,
        user_id=user_id,
        provider_keys=provider_keys,
    )
    policies = list_policies(db, user_id)
    latest_policy = (
        max(policies, key=lambda policy: (policy.version, str(policy.id))) if policies else None
    )
    active_policy = get_effective_policy(db, user_id, evaluation_time)
    version = latest_policy.version if latest_policy is not None else None

    if active_policy is None:
        refresh_policy = latest_policy
        refresh_required = _projection_refresh_required(
            db,
            user_id,
            refresh_policy.id if refresh_policy is not None else None,
        )
        if not policies and not provider_keys:
            return NutritionPriorityState(
                status="no_providers",
                version=None,
                sources=[],
                configuration_mode="none",
                projection_refresh_required=refresh_required,
            )
        return NutritionPriorityState(
            status="configuration_required" if policies else "selection_required",
            version=version,
            sources=_source_list(available_provider_keys, provider_keys),
            configuration_mode="none",
            projection_refresh_required=refresh_required,
        )

    all_nutrition_rules = list_rules(db, user_id, active_policy.id, data_area="nutrition")
    configured_provider_keys = {rule.provider_key for rule in all_nutrition_rules}
    wildcard_ranks = {
        rule.provider_key: rule.priority_rank
        for rule in all_nutrition_rules
        if rule.metric_key is None
    }
    has_metric_specific_rules = any(rule.metric_key is not None for rule in all_nutrition_rules)
    refresh_required = _projection_refresh_required(db, user_id, active_policy.id)

    if has_metric_specific_rules:
        return NutritionPriorityState(
            status="advanced_configuration",
            version=version,
            sources=_source_list(available_provider_keys, configured_provider_keys | provider_keys),
            configuration_mode="advanced",
            projection_refresh_required=refresh_required,
        )

    if _is_public_global_policy(all_nutrition_rules, provider_keys):
        return NutritionPriorityState(
            status="configured",
            version=version,
            sources=_source_list(
                available_provider_keys,
                configured_provider_keys | provider_keys,
                wildcard_ranks,
            ),
            configuration_mode="global",
            projection_refresh_required=refresh_required,
        )
    return NutritionPriorityState(
        status="configuration_required",
        version=version,
        sources=_source_list(
            available_provider_keys,
            configured_provider_keys | provider_keys,
            wildcard_ranks,
        ),
        configuration_mode="none",
        projection_refresh_required=refresh_required,
    )


class NutritionPriorityUpdateConflict(ValueError):
    code: ClassVar[str]

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.code)


class StalePolicyConflict(NutritionPriorityUpdateConflict):
    code = "stale_policy"

    def __init__(self, *, current_version: int | None) -> None:
        self.current_version = current_version
        super().__init__()


class ProviderSetChangedConflict(NutritionPriorityUpdateConflict):
    code = "provider_set_changed"


class InvalidSourceOrderConflict(NutritionPriorityUpdateConflict):
    code = "invalid_source_order"


class AdvancedConfigurationConflict(NutritionPriorityUpdateConflict):
    code = "advanced_configuration"


_EXPECTED_POLICY_UNIQUENESS_CONSTRAINTS = frozenset(
    {
        "uq_source_priority_policies_user_version",
        "uq_source_priority_policies_user_effective_from",
    }
)
_SQLITE_POLICY_UNIQUENESS_MESSAGES = frozenset(
    {
        "UNIQUE constraint failed: source_priority_policies.user_id, "
        "source_priority_policies.version",
        "UNIQUE constraint failed: source_priority_policies.user_id, "
        "source_priority_policies.effective_from",
    }
)


def _is_expected_policy_uniqueness_error(error: IntegrityError) -> bool:
    original = getattr(error, "orig", None)
    diagnostic = getattr(original, "diag", None)
    if getattr(diagnostic, "constraint_name", None) in _EXPECTED_POLICY_UNIQUENESS_CONSTRAINTS:
        return True
    return (
        getattr(original, "sqlite_errorname", None) == "SQLITE_CONSTRAINT_UNIQUE"
        and str(original) in _SQLITE_POLICY_UNIQUENESS_MESSAGES
    )


def _latest_policy(policies: list[SourcePriorityPolicy]) -> SourcePriorityPolicy | None:
    return max(policies, key=lambda policy: (policy.version, str(policy.id))) if policies else None


def _raise_stale_after_race(
    db: Session,
    user_id: UUID,
    expected_version: int | None,
) -> None:
    latest = _latest_policy(list_policies(db, user_id))
    latest_version = latest.version if latest is not None else None
    if latest_version != expected_version:
        raise StalePolicyConflict(current_version=latest_version)


def update_nutrition_priority(
    db: Session,
    user_id: UUID,
    payload: NutritionPriorityUpdateRequest,
) -> tuple[NutritionPriorityState, bool]:
    """Apply a public Nutrition source ordering as an immutable policy version."""
    db.scalar(select(User).where(User.id == user_id).with_for_update())

    source_order = list(payload.source_order)
    if not source_order or len(source_order) != len(set(source_order)):
        raise InvalidSourceOrderConflict()
    if any(source_id not in PUBLIC_NUTRITION_SOURCES for source_id in source_order):
        raise InvalidSourceOrderConflict()

    evaluation_time = datetime.now(UTC)
    bindings = resolve_default_provider_sources(
        db,
        user_id=user_id,
        provider_keys=DEFAULT_SOURCE_RESOLVERS.keys(),
    )
    available_provider_keys = {binding.provider_key for binding in bindings} & set(
        PUBLIC_NUTRITION_SOURCES
    )
    if set(source_order) != available_provider_keys:
        raise ProviderSetChangedConflict()

    policies = list_policies(db, user_id)
    latest_policy = _latest_policy(policies)
    latest_version = latest_policy.version if latest_policy is not None else None
    if payload.expected_version != latest_version:
        raise StalePolicyConflict(current_version=latest_version)

    active_policy = get_effective_policy(db, user_id, evaluation_time)
    active_rules = (
        list_rules(db, user_id, active_policy.id, data_area="nutrition")
        if active_policy is not None
        else []
    )
    if active_policy is not None and any(rule.metric_key is not None for rule in active_rules):
        raise AdvancedConfigurationConflict()

    if active_policy is not None and _is_public_global_policy(
        active_rules, available_provider_keys
    ):
        configured_order = tuple(
            rule.provider_key for rule in sorted(active_rules, key=lambda rule: rule.priority_rank)
        )
        if configured_order == tuple(source_order):
            state = get_nutrition_priority_state(db, user_id, at=evaluation_time)
            return state, False

    next_version = (latest_version or 0) + 1
    rules = tuple(
        PriorityRuleSpec("nutrition", None, provider_key, rank)
        for rank, provider_key in enumerate(source_order, start=1)
    )
    try:
        create_policy_with_rules(db, user_id, next_version, evaluation_time, rules)
        db.commit()
    except IntegrityError as exc:
        if not _is_expected_policy_uniqueness_error(exc):
            raise
        db.rollback()
        _raise_stale_after_race(db, user_id, payload.expected_version)
        raise

    state = get_nutrition_priority_state(db, user_id, at=evaluation_time)
    return state, True


__all__ = [
    "PUBLIC_NUTRITION_SOURCES",
    "AdvancedConfigurationConflict",
    "InvalidSourceOrderConflict",
    "NutritionPriorityUpdateConflict",
    "ProviderSetChangedConflict",
    "StalePolicyConflict",
    "get_nutrition_priority_state",
    "update_nutrition_priority",
]
