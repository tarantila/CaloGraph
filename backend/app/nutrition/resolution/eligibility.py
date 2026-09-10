from app.nutrition.enums import (
    CoverageState,
    LineageState,
    PresenceState,
    ProjectionGranularity,
    ResolutionState,
)

from .contracts import EventReconstructionCandidate, ProviderCandidate, SummaryCandidate
from .metrics import is_known_metric, unit_matches
from .reasons import ReasonCode

Candidate = EventReconstructionCandidate | SummaryCandidate | ProviderCandidate


def _has_valid_value(candidate: Candidate) -> bool:
    return (
        candidate.value is not None
        and is_known_metric(candidate.metric_key)
        and unit_matches(candidate.metric_key, candidate.unit)
        and candidate.presence_state in {PresenceState.SUPPLIED, PresenceState.EXPLICIT_ZERO}
    )


def is_summary_usable(candidate: SummaryCandidate) -> bool:
    return (
        _has_valid_value(candidate)
        and candidate.coverage_state is CoverageState.COMPLETE
        and candidate.resolution_state is ResolutionState.RESOLVED
        and candidate.lineage_state in {LineageState.CONFIRMED, LineageState.UNCERTAIN}
        and candidate.selected_granularity is ProjectionGranularity.SUMMARY
    )


def is_candidate_eligible(candidate: Candidate) -> bool:
    if not _has_valid_value(candidate):
        return False
    if candidate.coverage_state not in {CoverageState.COMPLETE, CoverageState.PARTIAL}:
        return False
    if candidate.lineage_state not in {LineageState.CONFIRMED, LineageState.UNCERTAIN}:
        return False
    if candidate.resolution_state is ResolutionState.RESOLVED:
        return True
    return (
        isinstance(candidate, (EventReconstructionCandidate, ProviderCandidate))
        and candidate.coverage_state is CoverageState.PARTIAL
        and candidate.resolution_state is ResolutionState.UNRESOLVED
        and candidate.selected_granularity is ProjectionGranularity.PARTIAL_EVENT
        and candidate.reason_code is ReasonCode.PARTIAL_EVENTS_NO_SUMMARY
    )
