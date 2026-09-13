from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.nutrition.resolution.metrics import CANONICAL_METRICS
from app.nutrition.resolution.sources import (
    DEFAULT_SOURCE_RESOLVERS,
    ProviderSourceResolver,
    resolve_default_provider_sources,
)
from app.source_priority.application import create_policy_with_rules
from app.source_priority.contracts import PriorityRuleSpec, validate_aware_datetime
from app.source_priority.models import SourcePriorityPolicy, SourcePriorityRule
from app.source_priority.repositories import list_policies, list_rules


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
    constraint_name = getattr(diagnostic, "constraint_name", None)
    if constraint_name in _EXPECTED_POLICY_UNIQUENESS_CONSTRAINTS:
        return True
    return (
        getattr(original, "sqlite_errorname", None) == "SQLITE_CONSTRAINT_UNIQUE"
        and str(original) in _SQLITE_POLICY_UNIQUENESS_MESSAGES
    )


class NutritionPriorityBootstrapStatus(StrEnum):
    NO_PROVIDERS = "no_providers"
    CREATED = "created"
    EXISTING_POLICY = "existing_policy"
    SELECTION_REQUIRED = "selection_required"
    CONFIGURATION_REQUIRED = "configuration_required"


@dataclass(frozen=True, slots=True)
class NutritionPriorityBootstrapResult:
    status: NutritionPriorityBootstrapStatus
    policy_id: UUID | None
    policy_version: int | None
    available_provider_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.status, NutritionPriorityBootstrapStatus):
            raise ValueError("status must be a NutritionPriorityBootstrapStatus")
        if self.policy_id is not None and not isinstance(self.policy_id, UUID):
            raise ValueError("policy_id must be a UUID or None")
        if self.policy_version is not None and (
            type(self.policy_version) is not int or self.policy_version < 1
        ):
            raise ValueError("policy_version must be a positive integer or None")
        if not isinstance(self.available_provider_keys, tuple):
            raise ValueError("available_provider_keys must be a tuple")
        if any(
            not isinstance(provider_key, str) or not provider_key
            for provider_key in self.available_provider_keys
        ):
            raise ValueError("available_provider_keys must contain non-empty strings")
        if tuple(sorted(self.available_provider_keys)) != self.available_provider_keys:
            raise ValueError("available_provider_keys must be sorted")
        if len(set(self.available_provider_keys)) != len(self.available_provider_keys):
            raise ValueError("available_provider_keys must not contain duplicates")


def _nutrition_is_configured(rules: list[SourcePriorityRule]) -> bool:
    nutrition_rules = [rule for rule in rules if rule.data_area == "nutrition"]
    if any(rule.metric_key is None for rule in nutrition_rules):
        return True
    metric_keys = {rule.metric_key for rule in nutrition_rules}
    return set(CANONICAL_METRICS).issubset(metric_keys)


def _policy_timestamp(policy: SourcePriorityPolicy) -> datetime:
    timestamp = policy.effective_from
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC)


def _reported_policy(
    policies: list[SourcePriorityPolicy],
    effective_from: datetime,
) -> SourcePriorityPolicy:
    effective_policies = [
        policy for policy in policies if _policy_timestamp(policy) <= effective_from
    ]
    if effective_policies:
        return max(
            effective_policies,
            key=lambda policy: (_policy_timestamp(policy), policy.version, str(policy.id)),
        )
    return max(
        policies,
        key=lambda policy: (_policy_timestamp(policy), policy.version, str(policy.id)),
    )


def _existing_policy_result(
    db: Session,
    policies: list[SourcePriorityPolicy],
    *,
    user_id: UUID,
    effective_from: datetime,
    available_provider_keys: tuple[str, ...],
    race_winner: bool = False,
) -> NutritionPriorityBootstrapResult:
    policy = _reported_policy(policies, effective_from)
    configured = False
    if race_winner or _policy_timestamp(policy) <= effective_from:
        configured = _nutrition_is_configured(list_rules(db, user_id, policy.id))
    status = (
        NutritionPriorityBootstrapStatus.EXISTING_POLICY
        if configured
        else NutritionPriorityBootstrapStatus.CONFIGURATION_REQUIRED
    )
    return NutritionPriorityBootstrapResult(
        status=status,
        policy_id=policy.id,
        policy_version=policy.version,
        available_provider_keys=available_provider_keys,
    )


def bootstrap_nutrition_priority(
    *,
    session_factory: Callable[[], Session],
    user_id: UUID,
    effective_from: datetime,
    resolver_registry: Mapping[str, ProviderSourceResolver] | None = None,
) -> NutritionPriorityBootstrapResult:
    normalized_effective_from = validate_aware_datetime(effective_from)
    registry = DEFAULT_SOURCE_RESOLVERS if resolver_registry is None else resolver_registry

    with session_factory() as db:
        bindings = resolve_default_provider_sources(
            db,
            user_id=user_id,
            provider_keys=registry.keys(),
            resolver_registry=resolver_registry,
        )
        available_provider_keys = tuple(binding.provider_key for binding in bindings)
        policies = list_policies(db, user_id)

        if policies:
            return _existing_policy_result(
                db,
                policies,
                user_id=user_id,
                effective_from=normalized_effective_from,
                available_provider_keys=available_provider_keys,
            )

        if not bindings:
            return NutritionPriorityBootstrapResult(
                status=NutritionPriorityBootstrapStatus.NO_PROVIDERS,
                policy_id=None,
                policy_version=None,
                available_provider_keys=available_provider_keys,
            )
        if len(bindings) > 1:
            return NutritionPriorityBootstrapResult(
                status=NutritionPriorityBootstrapStatus.SELECTION_REQUIRED,
                policy_id=None,
                policy_version=None,
                available_provider_keys=available_provider_keys,
            )

        try:
            snapshot = create_policy_with_rules(
                db,
                user_id,
                1,
                normalized_effective_from,
                (
                    PriorityRuleSpec(
                        data_area="nutrition",
                        metric_key=None,
                        provider_key=bindings.bindings[0].provider_key,
                        priority_rank=1,
                    ),
                ),
            )
            db.commit()
        except IntegrityError as exc:
            if not _is_expected_policy_uniqueness_error(exc):
                raise
            db.rollback()
            policies = list_policies(db, user_id)
            if not policies:
                raise
            return _existing_policy_result(
                db,
                policies,
                user_id=user_id,
                effective_from=normalized_effective_from,
                available_provider_keys=available_provider_keys,
                race_winner=True,
            )
        return NutritionPriorityBootstrapResult(
            status=NutritionPriorityBootstrapStatus.CREATED,
            policy_id=snapshot.policy_id,
            policy_version=snapshot.version,
            available_provider_keys=available_provider_keys,
        )
