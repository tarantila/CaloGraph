from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.nutrition.resolution.contracts import ProviderCandidate
from app.source_priority.contracts import (
    PriorityContractError,
    PriorityPolicySnapshot,
    PriorityRuleSnapshot,
    PriorityRuleSpec,
    PrioritySelection,
    validate_aware_datetime,
    validate_positive,
)
from app.source_priority.models import SourcePriorityPolicy, SourcePriorityRule
from app.source_priority.repositories import get_effective_policy, list_rules
from app.source_priority.selection import select_by_source_priority


def _validate_rule_specs(rules: Sequence[PriorityRuleSpec]) -> tuple[PriorityRuleSpec, ...]:
    normalized_rules = tuple(rules)
    seen_providers: set[tuple[str, str | None, str]] = set()
    seen_ranks: set[tuple[str, str | None, int]] = set()
    for rule in normalized_rules:
        if not isinstance(rule, PriorityRuleSpec):
            raise PriorityContractError("rules must contain PriorityRuleSpec values")
        provider_scope = (rule.data_area, rule.metric_key, rule.provider_key)
        rank_scope = (rule.data_area, rule.metric_key, rule.priority_rank)
        if provider_scope in seen_providers or rank_scope in seen_ranks:
            raise PriorityContractError("priority_rank_conflict")
        seen_providers.add(provider_scope)
        seen_ranks.add(rank_scope)
    return normalized_rules


def _snapshot_rule(rule: SourcePriorityRule) -> PriorityRuleSnapshot:
    return PriorityRuleSnapshot(
        rule_id=rule.id,
        data_area=rule.data_area,
        metric_key=rule.metric_key,
        provider_key=rule.provider_key,
        priority_rank=rule.priority_rank,
    )


def _snapshot_policy(
    policy: SourcePriorityPolicy,
    rules: Sequence[SourcePriorityRule],
) -> PriorityPolicySnapshot:
    effective_from = policy.effective_from
    if effective_from.tzinfo is None or effective_from.utcoffset() is None:
        effective_from = effective_from.replace(tzinfo=UTC)
    return PriorityPolicySnapshot(
        policy_id=policy.id,
        user_id=policy.user_id,
        version=policy.version,
        effective_from=effective_from,
        rules=tuple(_snapshot_rule(rule) for rule in rules),
    )


def create_policy_with_rules(
    db: Session,
    user_id: UUID,
    version: int,
    effective_from: datetime,
    rules: Sequence[PriorityRuleSpec],
) -> PriorityPolicySnapshot:
    validate_positive(version, "version")
    normalized_effective_from = validate_aware_datetime(effective_from)
    normalized_rules = _validate_rule_specs(rules)

    connection = db.connection()
    if connection.dialect.name == "sqlite" and not connection.connection.in_transaction:
        connection.exec_driver_sql("BEGIN")
    with db.begin_nested():
        policy = SourcePriorityPolicy(
            user_id=user_id,
            version=version,
            effective_from=normalized_effective_from,
        )
        db.add(policy)
        db.flush()

        db.add_all(
            SourcePriorityRule(
                user_id=user_id,
                policy_id=policy.id,
                data_area=rule.data_area,
                metric_key=rule.metric_key,
                provider_key=rule.provider_key,
                priority_rank=rule.priority_rank,
            )
            for rule in normalized_rules
        )
        db.flush()
        return _snapshot_policy(policy, list_rules(db, user_id, policy.id))


def get_effective_policy_snapshot(
    db: Session,
    user_id: UUID,
    policy_at: datetime,
) -> PriorityPolicySnapshot | None:
    normalized_policy_at = validate_aware_datetime(policy_at)
    policy = get_effective_policy(db, user_id, normalized_policy_at)
    if policy is None:
        return None
    return _snapshot_policy(policy, list_rules(db, user_id, policy.id))


def select_nutrition_candidate(
    db: Session,
    *,
    user_id: UUID,
    local_date: date,
    metric_key: str,
    candidates: Mapping[str, ProviderCandidate] | Sequence[ProviderCandidate],
    policy_at: datetime,
) -> PrioritySelection:
    policy = get_effective_policy_snapshot(db, user_id, policy_at)
    return select_by_source_priority(
        user_id=user_id,
        local_date=local_date,
        metric_key=metric_key,
        candidates=candidates,
        policy=policy,
    )
