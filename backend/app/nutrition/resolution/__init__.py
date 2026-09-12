from .aggregation import (
    aggregate_contributions,
    aggregate_coverage,
    aggregate_lineage,
    aggregate_presence,
    aggregate_resolution,
)
from .contracts import (
    AggregatedMetric,
    EventReconstructionCandidate,
    MetricContribution,
    ProviderCandidate,
    SummaryCandidate,
    build_event_candidate,
    build_summary_candidate,
)
from .eligibility import is_candidate_eligible, is_summary_usable
from .metrics import (
    CANONICAL_METRICS,
    MetricDefinition,
    canonical_unit,
    is_known_metric,
    is_unsupported_metric,
    metric_definition,
)
from .parity import ParityDiagnostic, compare_decimal_parity
from .providers import (
    PROVIDER_RESOLVERS,
    NutritionProviderResolver,
    ProviderCandidateContractError,
    ProviderNotAvailableError,
    YazioProviderResolver,
    collect_provider_candidates,
    resolve_provider_metric,
)
from .reasons import EvidenceKind, ReasonCode
from .resolver import resolve_event_vs_summary
from .sources import ProviderSourceBindings, ProviderSourceInstance

__all__ = [
    "CANONICAL_METRICS",
    "PROVIDER_RESOLVERS",
    "AggregatedMetric",
    "EventReconstructionCandidate",
    "EvidenceKind",
    "MetricContribution",
    "MetricDefinition",
    "NutritionProviderResolver",
    "ParityDiagnostic",
    "ProviderCandidate",
    "ProviderCandidateContractError",
    "ProviderNotAvailableError",
    "ProviderSourceBindings",
    "ProviderSourceInstance",
    "ReasonCode",
    "SummaryCandidate",
    "YazioProviderResolver",
    "aggregate_contributions",
    "aggregate_coverage",
    "aggregate_lineage",
    "aggregate_presence",
    "aggregate_resolution",
    "build_event_candidate",
    "build_summary_candidate",
    "canonical_unit",
    "collect_provider_candidates",
    "compare_decimal_parity",
    "is_candidate_eligible",
    "is_known_metric",
    "is_summary_usable",
    "is_unsupported_metric",
    "metric_definition",
    "resolve_event_vs_summary",
    "resolve_provider_metric",
]
