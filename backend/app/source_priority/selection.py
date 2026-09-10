from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from uuid import UUID

from app.nutrition.resolution.contracts import ProviderCandidate
from app.nutrition.resolution.eligibility import is_candidate_eligible
from app.nutrition.resolution.metrics import is_known_metric

from .contracts import (
    AppliedPriorityScope,
    PriorityContractError,
    PriorityPolicySnapshot,
    PriorityReasonCode,
    PriorityRuleSnapshot,
    PrioritySelection,
    PrioritySelectionRole,
    ProviderDisposition,
)

_DATA_AREA = "nutrition"


def _contract_error(message: str) -> PriorityContractError:
    return PriorityContractError(message)


def _normalize_candidates(
    candidates: Mapping[str, ProviderCandidate] | Sequence[ProviderCandidate],
) -> tuple[ProviderCandidate, ...]:
    if isinstance(candidates, Mapping):
        items = tuple(candidates.items())
        normalized: list[ProviderCandidate] = []
        seen: set[str] = set()
        for mapping_key, candidate in items:
            if not isinstance(candidate, ProviderCandidate):
                raise _contract_error("candidates must contain ProviderCandidate values")
            if not isinstance(candidate.provider_key, str) or not candidate.provider_key:
                raise _contract_error("candidate provider_key must be a non-empty string")
            if not isinstance(mapping_key, str) or mapping_key != candidate.provider_key:
                raise _contract_error("mapping key must match candidate provider_key")
            if candidate.provider_key in seen:
                raise _contract_error("duplicate provider key")
            seen.add(candidate.provider_key)
            normalized.append(candidate)
        return tuple(normalized)

    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes, bytearray)):
        raise _contract_error("candidates must be a Mapping or Sequence")

    normalized_sequence = tuple(candidates)
    sequence_seen: set[str] = set()
    for candidate in normalized_sequence:
        if not isinstance(candidate, ProviderCandidate):
            raise _contract_error("candidates must contain ProviderCandidate values")
        if not isinstance(candidate.provider_key, str) or not candidate.provider_key:
            raise _contract_error("candidate provider_key must be a non-empty string")
        if candidate.provider_key in sequence_seen:
            raise _contract_error("duplicate provider key")
        sequence_seen.add(candidate.provider_key)
    return normalized_sequence


def _validate_scope(
    *,
    user_id: UUID,
    local_date: date,
    metric_key: str,
    candidates: tuple[ProviderCandidate, ...],
    policy: PriorityPolicySnapshot | None,
) -> None:
    if not isinstance(user_id, UUID):
        raise _contract_error("user_id must be UUID")
    if not isinstance(local_date, date) or type(local_date) is not date:
        raise _contract_error("local_date must be date")
    if not isinstance(metric_key, str) or not is_known_metric(metric_key):
        raise _contract_error("metric_key must be a canonical nutrition metric")

    for candidate in candidates:
        if candidate.user_id != user_id:
            raise _contract_error("candidate user_id does not match request")
        if candidate.local_date != local_date:
            raise _contract_error("candidate local_date does not match request")
        if candidate.metric_key != metric_key:
            raise _contract_error("candidate metric_key does not match request")

    if policy is not None:
        if not isinstance(policy, PriorityPolicySnapshot):
            raise _contract_error("policy must be a PriorityPolicySnapshot or None")
        if policy.user_id != user_id:
            raise _contract_error("policy user_id does not match request")
        if not isinstance(policy.rules, tuple):
            raise _contract_error("policy rules must be immutable")
        if any(not isinstance(rule, PriorityRuleSnapshot) for rule in policy.rules):
            raise _contract_error("policy rules must contain PriorityRuleSnapshot values")


def _applicable_rules(
    policy: PriorityPolicySnapshot,
    metric_key: str,
) -> tuple[AppliedPriorityScope, tuple[PriorityRuleSnapshot, ...]]:
    metric_rules = tuple(
        rule
        for rule in policy.rules
        if rule.data_area == _DATA_AREA and rule.metric_key == metric_key
    )
    if metric_rules:
        return AppliedPriorityScope.METRIC, metric_rules

    wildcard_rules = tuple(
        rule
        for rule in policy.rules
        if rule.data_area == _DATA_AREA and rule.metric_key is None
    )
    return AppliedPriorityScope.WILDCARD, wildcard_rules


def _validate_and_sort_rules(
    rules: tuple[PriorityRuleSnapshot, ...],
) -> tuple[PriorityRuleSnapshot, ...]:
    providers: set[str] = set()
    ranks: set[int] = set()
    for rule in rules:
        if rule.provider_key in providers or rule.priority_rank in ranks:
            raise _contract_error(PriorityReasonCode.PRIORITY_RANK_CONFLICT.value)
        providers.add(rule.provider_key)
        ranks.add(rule.priority_rank)

    # Rules have already passed conflict validation; these keys only stabilize
    # diagnostics and are never used to resolve an equal-priority conflict.
    return tuple(sorted(rules, key=lambda rule: (rule.priority_rank, rule.provider_key, str(rule.rule_id))))


def select_by_source_priority(
    *,
    user_id: UUID,
    local_date: date,
    metric_key: str,
    candidates: Mapping[str, ProviderCandidate] | Sequence[ProviderCandidate],
    policy: PriorityPolicySnapshot | None,
) -> PrioritySelection:
    """Select one eligible nutrition candidate using an immutable policy snapshot."""
    normalized_candidates = _normalize_candidates(candidates)
    _validate_scope(
        user_id=user_id,
        local_date=local_date,
        metric_key=metric_key,
        candidates=normalized_candidates,
        policy=policy,
    )

    # Evaluate every candidate exactly through B1's eligibility contract,
    # including branches where no policy can select a candidate.
    eligibility = {
        candidate.provider_key: is_candidate_eligible(candidate)
        for candidate in normalized_candidates
    }

    if policy is None:
        return PrioritySelection(
            user_id=user_id,
            local_date=local_date,
            metric_key=metric_key,
            policy=None,
            applied_scope=AppliedPriorityScope.NONE,
            selected_candidate=None,
            selected_role=None,
            reason_code=PriorityReasonCode.PRIORITY_POLICY_MISSING,
            dispositions=(),
        )

    applied_scope, raw_rules = _applicable_rules(policy, metric_key)
    if not raw_rules:
        return PrioritySelection(
            user_id=user_id,
            local_date=local_date,
            metric_key=metric_key,
            policy=policy,
            applied_scope=AppliedPriorityScope.NONE,
            selected_candidate=None,
            selected_role=None,
            reason_code=PriorityReasonCode.NO_APPLICABLE_PRIORITY_RULE,
            dispositions=(),
        )

    rules = _validate_and_sort_rules(raw_rules)
    by_provider = {candidate.provider_key: candidate for candidate in normalized_candidates}
    winner_rule: PriorityRuleSnapshot | None = None
    winner_candidate: ProviderCandidate | None = None
    for rule in rules:
        candidate = by_provider.get(rule.provider_key)
        if candidate is not None and eligibility[rule.provider_key]:
            winner_rule = rule
            winner_candidate = candidate
            break

    higher_rules_skipped = (
        winner_rule is not None
        and any(
            rule.priority_rank < winner_rule.priority_rank
            and (
                rule.provider_key not in by_provider
                or not eligibility[rule.provider_key]
            )
            for rule in rules
        )
    )
    dispositions: list[ProviderDisposition] = []
    for rule in rules:
        candidate = by_provider.get(rule.provider_key)
        if candidate is None:
            dispositions.append(
                ProviderDisposition(
                    provider_key=rule.provider_key,
                    rule_id=rule.rule_id,
                    priority_rank=rule.priority_rank,
                    candidate_present=False,
                    eligible=None,
                    role=PrioritySelectionRole.DIAGNOSTIC,
                    reason_code=PriorityReasonCode.PROVIDER_NOT_AVAILABLE,
                    candidate=None,
                )
            )
            continue

        candidate_eligible = eligibility[rule.provider_key]
        if not candidate_eligible:
            dispositions.append(
                ProviderDisposition(
                    provider_key=rule.provider_key,
                    rule_id=rule.rule_id,
                    priority_rank=rule.priority_rank,
                    candidate_present=True,
                    eligible=False,
                    role=PrioritySelectionRole.DIAGNOSTIC,
                    reason_code=PriorityReasonCode.CANDIDATE_INELIGIBLE,
                    candidate=candidate,
                )
            )
            continue

        if winner_rule is not None and rule.provider_key == winner_rule.provider_key:
            winner_role = (
                PrioritySelectionRole.FALLBACK
                if higher_rules_skipped
                else PrioritySelectionRole.SELECTED
            )
            dispositions.append(
                ProviderDisposition(
                    provider_key=rule.provider_key,
                    rule_id=rule.rule_id,
                    priority_rank=rule.priority_rank,
                    candidate_present=True,
                    eligible=True,
                    role=winner_role,
                    reason_code=(
                        PriorityReasonCode.METRIC_SPECIFIC_PRIORITY
                        if applied_scope is AppliedPriorityScope.METRIC
                        else PriorityReasonCode.WILDCARD_PRIORITY
                    ),
                    candidate=candidate,
                )
            )
        else:
            dispositions.append(
                ProviderDisposition(
                    provider_key=rule.provider_key,
                    rule_id=rule.rule_id,
                    priority_rank=rule.priority_rank,
                    candidate_present=True,
                    eligible=True,
                    role=PrioritySelectionRole.REJECTED,
                    reason_code=PriorityReasonCode.LOWER_PRIORITY_PROVIDER,
                    candidate=candidate,
                )
            )

    configured_providers = {rule.provider_key for rule in rules}
    for candidate in sorted(normalized_candidates, key=lambda item: item.provider_key):
        if candidate.provider_key in configured_providers:
            continue
        dispositions.append(
            ProviderDisposition(
                provider_key=candidate.provider_key,
                rule_id=None,
                priority_rank=None,
                candidate_present=True,
                eligible=eligibility[candidate.provider_key],
                role=PrioritySelectionRole.DIAGNOSTIC,
                reason_code=PriorityReasonCode.PROVIDER_NOT_CONFIGURED_FOR_METRIC,
                candidate=candidate,
            )
        )

    selected_role: PrioritySelectionRole | None = None
    reason_code = PriorityReasonCode.NO_ELIGIBLE_PROVIDER
    if winner_rule is not None and winner_candidate is not None:
        selected_role = (
            PrioritySelectionRole.FALLBACK
            if higher_rules_skipped
            else PrioritySelectionRole.SELECTED
        )
        reason_code = (
            PriorityReasonCode.METRIC_SPECIFIC_PRIORITY
            if applied_scope is AppliedPriorityScope.METRIC
            else PriorityReasonCode.WILDCARD_PRIORITY
        )

    return PrioritySelection(
        user_id=user_id,
        local_date=local_date,
        metric_key=metric_key,
        policy=policy,
        applied_scope=applied_scope,
        selected_candidate=winner_candidate,
        selected_role=selected_role,
        reason_code=reason_code,
        dispositions=tuple(dispositions),
    )
