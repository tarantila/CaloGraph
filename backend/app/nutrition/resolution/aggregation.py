from __future__ import annotations

from decimal import Decimal

from app.nutrition.enums import CoverageState, LineageState, PresenceState, ResolutionState

from .contracts import AggregatedMetric, MetricContribution
from .metrics import canonical_unit, is_known_metric

_PRESENCE_ORDER = (
    PresenceState.SUPPLIED,
    PresenceState.EXPLICIT_ZERO,
    PresenceState.MISSING,
    PresenceState.UNSUPPORTED,
    PresenceState.UNKNOWN,
)
_RESOLUTION_ORDER = (
    ResolutionState.CONFLICT,
    ResolutionState.DUPLICATE_CANDIDATE,
    ResolutionState.UNRESOLVED,
    ResolutionState.RESOLVED,
)


def _relevant(contributions: list[MetricContribution] | tuple[MetricContribution, ...]) -> tuple[MetricContribution, ...]:
    return tuple(item for item in contributions if item.expected)


def aggregate_presence(
    contributions: list[MetricContribution] | tuple[MetricContribution, ...],
) -> PresenceState:
    relevant = _relevant(contributions)
    for state in _PRESENCE_ORDER:
        if any(item.presence_state is state for item in relevant):
            return state
    return PresenceState.UNKNOWN


def aggregate_coverage(
    contributions: list[MetricContribution] | tuple[MetricContribution, ...],
    *,
    event_set_known: bool = True,
) -> CoverageState:
    relevant = _relevant(contributions)
    if not event_set_known or not relevant:
        return CoverageState.UNKNOWN
    usable_count = sum(item.value_contributing for item in relevant)
    if usable_count == 0:
        return CoverageState.UNKNOWN
    return CoverageState.COMPLETE if usable_count == len(relevant) else CoverageState.PARTIAL


def aggregate_resolution(
    contributions: list[MetricContribution] | tuple[MetricContribution, ...],
) -> ResolutionState:
    relevant = _relevant(contributions)
    for state in _RESOLUTION_ORDER:
        if any(item.resolution_state is state for item in relevant):
            return state
    return ResolutionState.UNRESOLVED


def aggregate_lineage(
    contributions: list[MetricContribution] | tuple[MetricContribution, ...],
) -> LineageState:
    value_contributions = tuple(item for item in _relevant(contributions) if item.value_contributing)
    if any(item.lineage_state is LineageState.UNCERTAIN for item in value_contributions):
        return LineageState.UNCERTAIN
    if any(item.lineage_state is LineageState.UNKNOWN for item in value_contributions):
        return LineageState.UNKNOWN
    if value_contributions and all(item.lineage_state is LineageState.CONFIRMED for item in value_contributions):
        return LineageState.CONFIRMED
    return LineageState.UNKNOWN


def aggregate_contributions(
    contributions: list[MetricContribution] | tuple[MetricContribution, ...],
    *,
    event_set_known: bool = True,
) -> AggregatedMetric:
    ordered = tuple(sorted(contributions, key=lambda item: item.sort_key))
    metric_keys = {item.metric_key for item in ordered}
    if len(metric_keys) > 1:
        raise ValueError("all contributions must use one metric")
    metric_key = next(iter(metric_keys), "")
    if metric_key and any(item.metric_key != metric_key for item in ordered):
        raise ValueError("all contributions must use one metric")

    value_contributions = tuple(item for item in ordered if item.value_contributing)
    value = sum((item.value for item in value_contributions if item.value is not None), Decimal("0"))
    aggregate_value = value if value_contributions else None
    return AggregatedMetric(
        metric_key=metric_key,
        value=aggregate_value,
        unit=canonical_unit(metric_key) if is_known_metric(metric_key) else None,
        presence_state=aggregate_presence(ordered),
        coverage_state=aggregate_coverage(ordered, event_set_known=event_set_known),
        resolution_state=aggregate_resolution(ordered),
        lineage_state=aggregate_lineage(ordered),
        source_lineage=ordered,
        numeric_contribution_count=len(value_contributions),
    )
