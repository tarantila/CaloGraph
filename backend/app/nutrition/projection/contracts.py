from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from enum import StrEnum
from typing import Final
from uuid import UUID

from app.nutrition.resolution.metrics import CANONICAL_METRICS, canonical_unit
from app.nutrition.resolution.sources import ProviderSourceBindings
from app.source_priority.contracts import (
    PriorityPolicySnapshot,
    PriorityRuleSnapshot,
    PrioritySelection,
)

from .tokens import TechnicalEvidenceToken, token_identity, token_sort_key

PROJECTION_ALGORITHM_VERSION: Final = "nutrition-daily-v1"
METRIC_REGISTRY_VERSION: Final = "nutrition-metrics-v1"
WATERMARK_FORMAT_VERSION: Final = "nutrition-watermark-v2"
CANONICAL_METRIC_KEYS: tuple[str, ...] = tuple(CANONICAL_METRICS)


class ProjectionContractError(ValueError):
    """A B5 input or persistence result cannot be represented safely."""


class ProjectionPersistenceStatus(StrEnum):
    CREATED = "created"
    UNCHANGED = "unchanged"
    POLICY_MISSING = "policy_missing"

_PROJECTION_DECIMAL_QUANTUM = Decimal("0.000000000001")
_PROJECTION_DECIMAL_LIMIT = Decimal("1000000000000")


def validate_projection_decimal(value: Decimal | None, field_name: str = "value") -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ProjectionContractError(f"{field_name} must be a finite Decimal or None")
    with localcontext() as context:
        context.prec = max(38, len(value.as_tuple().digits) + 24)
        try:
            rounded = value.quantize(_PROJECTION_DECIMAL_QUANTUM)
        except InvalidOperation as exc:
            raise ProjectionContractError(f"{field_name} exceeds Numeric(24, 12) range") from exc
    if rounded != value:
        raise ProjectionContractError(f"{field_name} exceeds 12 fractional digits")
    if abs(value) >= _PROJECTION_DECIMAL_LIMIT:
        raise ProjectionContractError(f"{field_name} exceeds Numeric(24, 12) range")
    return value

@dataclass(frozen=True, slots=True)
class ProjectionInputManifest:
    user_id: UUID
    local_date: date
    projection_algorithm_version: str
    metric_registry_version: str
    watermark_format_version: str
    policy: PriorityPolicySnapshot | None
    relevant_rules: tuple[PriorityRuleSnapshot, ...]
    technical_evidence: tuple[TechnicalEvidenceToken, ...]
    relevant_provider_sources: ProviderSourceBindings = field(default_factory=ProviderSourceBindings)

    def __post_init__(self) -> None:
        if not isinstance(self.user_id, UUID) or self.user_id.int == 0:
            raise ProjectionContractError("manifest user_id must be a non-zero UUID")
        if not isinstance(self.local_date, date) or type(self.local_date) is not date:
            raise ProjectionContractError("manifest local_date must be a date")
        expected_versions = (
            (PROJECTION_ALGORITHM_VERSION, self.projection_algorithm_version, "projection_algorithm_version"),
            (METRIC_REGISTRY_VERSION, self.metric_registry_version, "metric_registry_version"),
            (WATERMARK_FORMAT_VERSION, self.watermark_format_version, "watermark_format_version"),
        )
        for expected, value, name in expected_versions:
            if value != expected:
                raise ProjectionContractError(f"{name} has unsupported version")
        if self.policy is not None:
            if not isinstance(self.policy, PriorityPolicySnapshot):
                raise ProjectionContractError("manifest policy must be PriorityPolicySnapshot or None")
            if self.policy.user_id != self.user_id:
                raise ProjectionContractError("manifest policy user_id does not match manifest")

        rules = tuple(self.relevant_rules)
        if any(not isinstance(rule, PriorityRuleSnapshot) for rule in rules):
            raise ProjectionContractError("relevant_rules must contain PriorityRuleSnapshot values")
        rule_by_id: dict[UUID, PriorityRuleSnapshot] = {}
        for rule in rules:
            previous = rule_by_id.get(rule.rule_id)
            if previous is not None and previous != rule:
                raise ProjectionContractError("duplicate rule id has conflicting snapshots")
            rule_by_id[rule.rule_id] = rule
        if self.policy is None and rules:
            raise ProjectionContractError("policy-less manifest cannot contain relevant rules")
        if self.policy is not None:
            policy_rules = {rule.rule_id: rule for rule in self.policy.rules}
            if any(policy_rules.get(rule_id) != rule for rule_id, rule in rule_by_id.items()):
                raise ProjectionContractError("relevant rule is not present in policy snapshot")
        if not isinstance(self.relevant_provider_sources, ProviderSourceBindings):
            raise ProjectionContractError(
                "relevant_provider_sources must be ProviderSourceBindings"
            )
        relevant_provider_keys = {rule.provider_key for rule in self.relevant_rules}
        if any(
            binding.provider_key not in relevant_provider_keys
            for binding in self.relevant_provider_sources
        ):
            raise ProjectionContractError(
                "relevant provider source is not referenced by a relevant rule"
            )
        object.__setattr__(
            self,
            "relevant_rules",
            tuple(sorted(rule_by_id.values(), key=lambda rule: (str(rule.rule_id), rule.priority_rank))),
        )

        tokens_by_identity: dict[tuple[str, UUID], TechnicalEvidenceToken] = {}
        for token in tuple(self.technical_evidence):
            identity = token_identity(token)
            previous_token = tokens_by_identity.get(identity)
            if previous_token is not None and previous_token != token:
                raise ProjectionContractError("duplicate token identity has conflicting values")
            tokens_by_identity[identity] = token
        object.__setattr__(
            self,
            "technical_evidence",
            tuple(sorted(tokens_by_identity.values(), key=token_sort_key)),
        )


@dataclass(frozen=True, slots=True)
class DailyProjectionBuildInput:
    user_id: UUID
    local_date: date
    selections: tuple[PrioritySelection, ...]
    input_manifest: ProjectionInputManifest

    def __post_init__(self) -> None:
        if not isinstance(self.user_id, UUID) or self.user_id.int == 0:
            raise ProjectionContractError("user_id must be a non-zero UUID")
        if not isinstance(self.local_date, date) or type(self.local_date) is not date:
            raise ProjectionContractError("local_date must be a date")
        selections = tuple(self.selections)
        if len(selections) != len(CANONICAL_METRIC_KEYS):
            raise ProjectionContractError("build input must contain exactly seven selections")
        if any(not isinstance(selection, PrioritySelection) for selection in selections):
            raise ProjectionContractError("selections must contain PrioritySelection values")
        by_metric: dict[str, PrioritySelection] = {}
        for selection in selections:
            if selection.user_id != self.user_id or selection.local_date != self.local_date:
                raise ProjectionContractError("selection scope does not match build input")
            if selection.metric_key not in CANONICAL_METRICS:
                raise ProjectionContractError("selection metric must be a canonical nutrition metric")
            if selection.metric_key in by_metric:
                raise ProjectionContractError("duplicate metric selection")
            by_metric[selection.metric_key] = selection
        if set(by_metric) != set(CANONICAL_METRIC_KEYS):
            raise ProjectionContractError("build input must contain exactly the canonical seven metrics")
        object.__setattr__(
            self,
            "selections",
            tuple(by_metric[metric_key] for metric_key in CANONICAL_METRIC_KEYS),
        )
        if not isinstance(self.input_manifest, ProjectionInputManifest):
            raise ProjectionContractError("input_manifest must be ProjectionInputManifest")
        if self.input_manifest.user_id != self.user_id:
            raise ProjectionContractError("manifest user_id does not match build input")
        if self.input_manifest.local_date != self.local_date:
            raise ProjectionContractError("manifest local_date does not match build input")

        policies = tuple(selection.policy for selection in self.selections)
        first_policy = policies[0]
        if any(policy != first_policy for policy in policies[1:]):
            raise ProjectionContractError("all selections must use the same policy")
        if self.input_manifest.policy != first_policy:
            raise ProjectionContractError("manifest policy does not match selections")

        expected_rule_ids = {
            disposition.rule_id
            for selection in self.selections
            for disposition in selection.dispositions
            if disposition.rule_id is not None
        }
        actual_rule_ids = {rule.rule_id for rule in self.input_manifest.relevant_rules}
        if actual_rule_ids != expected_rule_ids:
            raise ProjectionContractError("manifest relevant rules do not match selection dispositions")
        if first_policy is not None:
            policy_rules = {rule.rule_id: rule for rule in first_policy.rules}
            if any(rule_id not in policy_rules for rule_id in expected_rule_ids):
                raise ProjectionContractError("selection rule is not present in policy snapshot")
        elif expected_rule_ids:
            raise ProjectionContractError("policy-less selections cannot contain rule IDs")


@dataclass(frozen=True, slots=True)
class ProjectionPersistenceResult:
    user_id: UUID
    local_date: date
    projection_id: UUID | None
    projection_version: int | None
    input_watermark: str | None
    created: bool
    status: ProjectionPersistenceStatus

    def __post_init__(self) -> None:
        if not isinstance(self.user_id, UUID) or self.user_id.int == 0:
            raise ProjectionContractError("result user_id must be a non-zero UUID")
        if not isinstance(self.local_date, date) or type(self.local_date) is not date:
            raise ProjectionContractError("result local_date must be a date")
        if not isinstance(self.created, bool):
            raise ProjectionContractError("result created must be bool")
        if not isinstance(self.status, ProjectionPersistenceStatus):
            raise ProjectionContractError("result status must be ProjectionPersistenceStatus")
        if self.created != (self.status is ProjectionPersistenceStatus.CREATED):
            raise ProjectionContractError("result created/status mismatch")
        if self.created and (self.projection_id is None or self.projection_version is None):
            raise ProjectionContractError("created result requires projection identity")
        if not self.created and self.projection_id is not None:
            raise ProjectionContractError("unchanged result cannot expose a new projection")
        if self.projection_version is not None and self.projection_version < 1:
            raise ProjectionContractError("projection_version must be positive")


def no_value_fact_defaults(metric_key: str) -> tuple[None, str, None, None, str, str, str, str]:
    unit = canonical_unit(metric_key)
    if unit is None:
        raise ProjectionContractError("metric must be canonical")
    return (None, unit, None, None, "unknown", "unknown", "unresolved", "unknown")
