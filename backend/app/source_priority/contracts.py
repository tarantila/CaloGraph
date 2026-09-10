from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from app.nutrition.resolution.contracts import ProviderCandidate
from app.nutrition.resolution.metrics import is_known_metric

UNSET: Final = object()


def validate_aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware; naive datetimes are not accepted")
    return value.astimezone(UTC)


def validate_non_empty(value: str, field_name: str) -> str:
    if not value:
        raise ValueError(f"{field_name} must be non-empty")
    return value


class PriorityContractError(ValueError):
    """A source-priority contract cannot be represented or evaluated safely."""


class PriorityReasonCode(StrEnum):
    METRIC_SPECIFIC_PRIORITY = "metric_specific_priority"
    WILDCARD_PRIORITY = "wildcard_priority"
    LOWER_PRIORITY_PROVIDER = "lower_priority_provider"
    PROVIDER_NOT_AVAILABLE = "provider_not_available"
    PRIORITY_POLICY_MISSING = "priority_policy_missing"
    NO_ELIGIBLE_PROVIDER = "no_eligible_provider"
    PRIORITY_RANK_CONFLICT = "priority_rank_conflict"
    PROVIDER_NOT_CONFIGURED_FOR_METRIC = "provider_not_configured_for_metric"
    CANDIDATE_INELIGIBLE = "candidate_ineligible"
    NO_APPLICABLE_PRIORITY_RULE = "no_applicable_priority_rule"


class PrioritySelectionRole(StrEnum):
    SELECTED = "selected"
    FALLBACK = "fallback"
    REJECTED = "rejected"
    DIAGNOSTIC = "diagnostic"


class AppliedPriorityScope(StrEnum):
    METRIC = "metric"
    WILDCARD = "wildcard"
    NONE = "none"


def _validate_priority_metric(metric_key: str | None) -> None:
    if metric_key is None:
        return
    if not isinstance(metric_key, str) or not metric_key:
        raise PriorityContractError("metric_key must be a non-empty string")
    if not is_known_metric(metric_key):
        raise PriorityContractError("metric_key must be a canonical nutrition metric")

def _validate_priority_rule_fields(
    *,
    data_area: str,
    metric_key: str | None,
    provider_key: str,
    priority_rank: int,
) -> None:
    if not isinstance(data_area, str) or not data_area:
        raise PriorityContractError("data_area must be a non-empty string")
    _validate_priority_metric(metric_key)
    if not isinstance(provider_key, str) or not provider_key:
        raise PriorityContractError("provider_key must be a non-empty string")
    if type(priority_rank) is not int or priority_rank < 1:
        raise PriorityContractError("priority_rank must be a positive integer")


def _validate_unique_priority_rules(rules: tuple[PriorityRuleSnapshot, ...]) -> None:
    seen_providers: set[tuple[str, str | None, str]] = set()
    seen_ranks: set[tuple[str, str | None, int]] = set()
    for rule in rules:
        provider_scope = (rule.data_area, rule.metric_key, rule.provider_key)
        rank_scope = (rule.data_area, rule.metric_key, rule.priority_rank)
        if provider_scope in seen_providers or rank_scope in seen_ranks:
            raise PriorityContractError(PriorityReasonCode.PRIORITY_RANK_CONFLICT.value)
        seen_providers.add(provider_scope)
        seen_ranks.add(rank_scope)


@dataclass(frozen=True, slots=True)
class PriorityRuleSpec:
    data_area: str
    metric_key: str | None
    provider_key: str
    priority_rank: int

    def __post_init__(self) -> None:
        _validate_priority_rule_fields(
            data_area=self.data_area,
            metric_key=self.metric_key,
            provider_key=self.provider_key,
            priority_rank=self.priority_rank,
        )


@dataclass(frozen=True, slots=True)
class PriorityRuleSnapshot:
    rule_id: UUID
    data_area: str
    metric_key: str | None
    provider_key: str
    priority_rank: int

    def __post_init__(self) -> None:
        if not isinstance(self.rule_id, UUID):
            raise PriorityContractError("rule_id must be UUID")
        _validate_priority_rule_fields(
            data_area=self.data_area,
            metric_key=self.metric_key,
            provider_key=self.provider_key,
            priority_rank=self.priority_rank,
        )


@dataclass(frozen=True, slots=True)
class PriorityPolicySnapshot:
    policy_id: UUID
    user_id: UUID
    version: int
    effective_from: datetime
    rules: tuple[PriorityRuleSnapshot, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, UUID) or not isinstance(self.user_id, UUID):
            raise PriorityContractError("policy and user scope identifiers must be UUID")
        if self.version < 1:
            raise PriorityContractError("version must be at least 1")
        object.__setattr__(self, "effective_from", validate_aware_datetime(self.effective_from))
        normalized_rules = tuple(self.rules)
        if any(not isinstance(rule, PriorityRuleSnapshot) for rule in normalized_rules):
            raise PriorityContractError("rules must contain PriorityRuleSnapshot values")
        _validate_unique_priority_rules(normalized_rules)
        object.__setattr__(
            self,
            "rules",
            tuple(
                sorted(
                    normalized_rules,
                    key=lambda rule: (
                        rule.data_area,
                        rule.metric_key is not None,
                        rule.metric_key or "",
                        rule.priority_rank,
                        rule.provider_key,
                        str(rule.rule_id),
                    ),
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class ProviderDisposition:
    provider_key: str
    rule_id: UUID | None
    priority_rank: int | None
    candidate_present: bool
    eligible: bool | None
    role: PrioritySelectionRole | None
    reason_code: PriorityReasonCode
    candidate: ProviderCandidate | None = None

    def __post_init__(self) -> None:
        if not self.provider_key:
            raise PriorityContractError("provider_key must be non-empty")
        if self.priority_rank is not None and self.priority_rank < 1:
            raise PriorityContractError("priority_rank must be at least 1")
        if self.candidate_present != (self.candidate is not None):
            raise PriorityContractError("candidate_present does not match candidate")
        if self.candidate is not None and self.candidate.provider_key != self.provider_key:
            raise PriorityContractError("candidate provider does not match disposition provider")

@dataclass(frozen=True, slots=True)
class PrioritySelection:
    user_id: UUID
    local_date: date
    metric_key: str
    policy: PriorityPolicySnapshot | None
    applied_scope: AppliedPriorityScope
    selected_candidate: ProviderCandidate | None
    selected_role: PrioritySelectionRole | None
    reason_code: PriorityReasonCode
    dispositions: tuple[ProviderDisposition, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.user_id, UUID):
            raise PriorityContractError("user_id must be UUID")
        if not isinstance(self.local_date, date):
            raise PriorityContractError("local_date must be date")
        _validate_priority_metric(self.metric_key)
        if not isinstance(self.metric_key, str):
            raise PriorityContractError("metric_key must be a string")
        normalized_dispositions = tuple(self.dispositions)
        if any(not isinstance(item, ProviderDisposition) for item in normalized_dispositions):
            raise PriorityContractError("dispositions must contain ProviderDisposition values")
        if self.selected_candidate is None and self.selected_role is not None:
            raise PriorityContractError("selected_role requires selected_candidate")
        if self.selected_candidate is not None and self.selected_role not in {
            PrioritySelectionRole.SELECTED,
            PrioritySelectionRole.FALLBACK,
        }:
            raise PriorityContractError("selected_candidate requires selected or fallback role")
        winning = tuple(item for item in normalized_dispositions if item.role is self.selected_role)
        if self.selected_candidate is None:
            if winning:
                raise PriorityContractError("empty selection cannot have a winning disposition")
        elif len(winning) != 1 or winning[0].candidate != self.selected_candidate:
            raise PriorityContractError("winning disposition candidate must equal selected_candidate")
        object.__setattr__(self, "dispositions", normalized_dispositions)


def validate_positive(value: int, field_name: str) -> int:
    if value < 1:
        raise ValueError(f"{field_name} must be at least 1")
    return value
