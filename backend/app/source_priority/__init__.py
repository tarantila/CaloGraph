"""User-scoped source-priority policy and selection contracts."""

from .application import (
    create_policy_with_rules,
    get_effective_policy_snapshot,
    select_nutrition_candidate,
)
from .contracts import (
    AppliedPriorityScope,
    PriorityContractError,
    PriorityPolicySnapshot,
    PriorityReasonCode,
    PriorityRuleSnapshot,
    PriorityRuleSpec,
    PrioritySelection,
    PrioritySelectionRole,
    ProviderDisposition,
)
from .selection import select_by_source_priority

__all__ = [
    "AppliedPriorityScope",
    "PriorityContractError",
    "PriorityPolicySnapshot",
    "PriorityReasonCode",
    "PriorityRuleSnapshot",
    "PriorityRuleSpec",
    "PrioritySelection",
    "PrioritySelectionRole",
    "ProviderDisposition",
    "create_policy_with_rules",
    "get_effective_policy_snapshot",
    "select_by_source_priority",
    "select_nutrition_candidate",
]
