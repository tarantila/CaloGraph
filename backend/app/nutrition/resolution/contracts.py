from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from app.nutrition.enums import (
    CoverageState,
    LineageState,
    PresenceState,
    ProjectionGranularity,
    ResolutionState,
)

from .metrics import is_known_metric, unit_matches
from .parity import ParityDiagnostic
from .reasons import EvidenceKind, ReasonCode

_INVALID_UNIT_REASONS = frozenset({ReasonCode.INVALID_UNIT, ReasonCode.METRIC_UNIT_MISMATCH})


def _validate_decimal(value: Decimal | None, name: str = "value") -> None:
    if value is not None and not isinstance(value, Decimal):
        raise TypeError(f"{name} must be Decimal or None")
    if value is not None and not value.is_finite():
        raise ValueError(f"{name} must be finite")


def _validate_value_presence(value: Decimal | None, presence_state: PresenceState) -> None:
    if value is None:
        if presence_state in {PresenceState.SUPPLIED, PresenceState.EXPLICIT_ZERO}:
            raise ValueError("missing value cannot have supplied or explicit_zero presence")
        return
    if presence_state not in {PresenceState.SUPPLIED, PresenceState.EXPLICIT_ZERO}:
        raise ValueError("missing value requires missing, unsupported, or unknown presence")
    if value == Decimal("0") and presence_state is not PresenceState.EXPLICIT_ZERO:
        raise ValueError("explicit_zero requires Decimal('0')")
    if value != Decimal("0") and presence_state is not PresenceState.SUPPLIED:
        raise ValueError("supplied is required for non-zero values")


def _validate_metric_and_unit(
    metric_key: str,
    value: Decimal | None,
    unit: str | None,
    reason_code: ReasonCode | None,
) -> None:
    if not metric_key:
        raise ValueError("metric_key must be non-empty")
    if not is_known_metric(metric_key):
        if value is not None:
            raise ValueError("unsupported metric cannot carry a numeric value")
        return
    if value is not None and not unit_matches(metric_key, unit) and reason_code not in _INVALID_UNIT_REASONS:
        raise ValueError("unit does not match metric")


@dataclass(frozen=True, slots=True)
class MetricContribution:
    evidence_id: UUID
    metric_key: str
    value: Decimal | None
    unit: str | None
    presence_state: PresenceState
    resolution_state: ResolutionState
    lineage_state: LineageState
    evidence_kind: EvidenceKind
    reason_code: ReasonCode | None = None
    expected: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.evidence_id, UUID):
            raise TypeError("evidence_id must be an internal UUID")
        if self.evidence_id.int == 0:
            raise ValueError("evidence_id must be non-zero")
        if not isinstance(self.presence_state, PresenceState):
            raise TypeError("presence_state must be PresenceState")
        if not isinstance(self.resolution_state, ResolutionState):
            raise TypeError("resolution_state must be ResolutionState")
        if not isinstance(self.lineage_state, LineageState):
            raise TypeError("lineage_state must be LineageState")
        if not isinstance(self.evidence_kind, EvidenceKind):
            raise TypeError("evidence_kind must be EvidenceKind")
        if self.reason_code is not None and not isinstance(self.reason_code, ReasonCode):
            raise TypeError("reason_code must be ReasonCode or None")
        if not isinstance(self.expected, bool):
            raise TypeError("expected must be bool")
        _validate_decimal(self.value)
        _validate_metric_and_unit(self.metric_key, self.value, self.unit, self.reason_code)
        _validate_value_presence(self.value, self.presence_state)

    @property
    def value_contributing(self) -> bool:
        return (
            self.expected
            and self.value is not None
            and is_known_metric(self.metric_key)
            and unit_matches(self.metric_key, self.unit)
            and self.presence_state in {PresenceState.SUPPLIED, PresenceState.EXPLICIT_ZERO}
        )

    @property
    def sort_key(self) -> tuple[str, str, str, str, str, str, str, str, str, str]:
        return (
            str(self.evidence_id),
            self.metric_key,
            self.unit or "",
            str(self.value) if self.value is not None else "",
            self.presence_state.value,
            self.resolution_state.value,
            self.lineage_state.value,
            self.evidence_kind.value,
            self.reason_code.value if self.reason_code is not None else "",
            str(self.expected),
        )


@dataclass(frozen=True, slots=True)
class AggregatedMetric:
    metric_key: str
    value: Decimal | None
    unit: str | None
    presence_state: PresenceState
    coverage_state: CoverageState
    resolution_state: ResolutionState
    lineage_state: LineageState
    source_lineage: tuple[MetricContribution, ...]
    numeric_contribution_count: int


@dataclass(frozen=True, slots=True)
class EventReconstructionCandidate:
    provider_key: str
    user_id: UUID
    local_date: date
    metric_key: str
    value: Decimal | None
    unit: str | None
    selected_granularity: ProjectionGranularity | None
    presence_state: PresenceState
    coverage_state: CoverageState
    resolution_state: ResolutionState
    lineage_state: LineageState
    source_lineage: tuple[MetricContribution, ...]
    reason_code: ReasonCode
    diagnostic_codes: tuple[ReasonCode, ...] = ()

    def __post_init__(self) -> None:
        _validate_candidate(
            provider_key=self.provider_key,
            user_id=self.user_id,
            local_date=self.local_date,
            metric_key=self.metric_key,
            value=self.value,
            unit=self.unit,
            selected_granularity=self.selected_granularity,
            presence_state=self.presence_state,
            coverage_state=self.coverage_state,
            resolution_state=self.resolution_state,
            lineage_state=self.lineage_state,
            source_lineage=self.source_lineage,
            reason_code=self.reason_code,
            diagnostic_codes=self.diagnostic_codes,
        )
        object.__setattr__(self, "source_lineage", _sorted_lineage(self.source_lineage))
        object.__setattr__(
            self,
            "diagnostic_codes",
            tuple(sorted(set(self.diagnostic_codes), key=lambda item: item.value)),
        )


@dataclass(frozen=True, slots=True)
class SummaryCandidate:
    provider_key: str
    user_id: UUID
    local_date: date
    metric_key: str
    value: Decimal | None
    unit: str | None
    selected_granularity: ProjectionGranularity | None
    presence_state: PresenceState
    coverage_state: CoverageState
    resolution_state: ResolutionState
    lineage_state: LineageState
    source_lineage: tuple[MetricContribution, ...]
    reason_code: ReasonCode
    diagnostic_codes: tuple[ReasonCode, ...] = ()

    def __post_init__(self) -> None:
        _validate_candidate(
            provider_key=self.provider_key,
            user_id=self.user_id,
            local_date=self.local_date,
            metric_key=self.metric_key,
            value=self.value,
            unit=self.unit,
            selected_granularity=self.selected_granularity,
            presence_state=self.presence_state,
            coverage_state=self.coverage_state,
            resolution_state=self.resolution_state,
            lineage_state=self.lineage_state,
            source_lineage=self.source_lineage,
            reason_code=self.reason_code,
            diagnostic_codes=self.diagnostic_codes,
        )
        object.__setattr__(self, "source_lineage", _sorted_lineage(self.source_lineage))
        object.__setattr__(
            self,
            "diagnostic_codes",
            tuple(sorted(set(self.diagnostic_codes), key=lambda item: item.value)),
        )


@dataclass(frozen=True, slots=True)
class ProviderCandidate:
    provider_key: str
    user_id: UUID
    local_date: date
    metric_key: str
    value: Decimal | None
    unit: str | None
    selected_granularity: ProjectionGranularity | None
    presence_state: PresenceState
    coverage_state: CoverageState
    resolution_state: ResolutionState
    lineage_state: LineageState
    source_lineage: tuple[MetricContribution, ...]
    reason_code: ReasonCode
    diagnostic_codes: tuple[ReasonCode, ...] = ()
    parity_diagnostic: ParityDiagnostic | None = None

    def __post_init__(self) -> None:
        _validate_candidate(
            provider_key=self.provider_key,
            user_id=self.user_id,
            local_date=self.local_date,
            metric_key=self.metric_key,
            value=self.value,
            unit=self.unit,
            selected_granularity=self.selected_granularity,
            presence_state=self.presence_state,
            coverage_state=self.coverage_state,
            resolution_state=self.resolution_state,
            lineage_state=self.lineage_state,
            source_lineage=self.source_lineage,
            reason_code=self.reason_code,
            diagnostic_codes=self.diagnostic_codes,
        )
        object.__setattr__(self, "source_lineage", _sorted_lineage(self.source_lineage))
        object.__setattr__(
            self,
            "diagnostic_codes",
            tuple(sorted(set(self.diagnostic_codes), key=lambda item: item.value)),
        )


def _validate_candidate(
    *,
    provider_key: str,
    user_id: UUID,
    local_date: date,
    metric_key: str,
    value: Decimal | None,
    unit: str | None,
    selected_granularity: ProjectionGranularity | None,
    presence_state: PresenceState,
    coverage_state: CoverageState,
    resolution_state: ResolutionState,
    lineage_state: LineageState,
    source_lineage: tuple[MetricContribution, ...],
    reason_code: ReasonCode,
    diagnostic_codes: tuple[ReasonCode, ...],
) -> None:
    if not provider_key:
        raise ValueError("provider_key must be non-empty")
    if not isinstance(user_id, UUID):
        raise TypeError("user_id must be UUID")
    if not isinstance(local_date, date):
        raise TypeError("local_date must be date")
    if not isinstance(selected_granularity, ProjectionGranularity | type(None)):
        raise ValueError("granularity must be ProjectionGranularity or None")
    for value_name, value_item, enum_type in (
        ("presence_state", presence_state, PresenceState),
        ("coverage_state", coverage_state, CoverageState),
        ("resolution_state", resolution_state, ResolutionState),
        ("lineage_state", lineage_state, LineageState),
    ):
        if not isinstance(value_item, enum_type):
            raise TypeError(f"{value_name} must be {enum_type.__name__}")
    if not isinstance(reason_code, ReasonCode):
        raise TypeError("reason_code must be ReasonCode")
    if any(not isinstance(item, ReasonCode) for item in diagnostic_codes):
        raise TypeError("diagnostic_codes must contain ReasonCode values")
    if not isinstance(source_lineage, tuple):
        raise TypeError("source_lineage must be a tuple")
    if any(not isinstance(item, MetricContribution) for item in source_lineage):
        raise TypeError("source_lineage must contain MetricContribution values")
    if any(item.metric_key != metric_key for item in source_lineage):
        raise ValueError("source_lineage metric scope mismatch")
    if value is not None and not source_lineage:
        raise ValueError("value-bearing candidate requires source_lineage")
    if value is not None:
        from .aggregation import aggregate_contributions

        aggregate = aggregate_contributions(source_lineage)
        if aggregate.value is None:
            if unit_matches(metric_key, unit):
                raise ValueError("candidate value does not match source_lineage")
        else:
            if aggregate.value != value:
                raise ValueError("candidate value does not match source_lineage")
            if aggregate.presence_state is not presence_state:
                raise ValueError("candidate presence does not match source_lineage")
            if (
                aggregate.coverage_state is not CoverageState.UNKNOWN
                and coverage_state is not CoverageState.UNKNOWN
                and aggregate.coverage_state is not coverage_state
            ):
                raise ValueError("candidate coverage does not match source_lineage")
            if aggregate.resolution_state is not resolution_state:
                raise ValueError("candidate resolution does not match source_lineage")
            if aggregate.lineage_state is not lineage_state:
                raise ValueError("candidate lineage does not match source_lineage")
    _validate_decimal(value)
    unit_reason: ReasonCode | None = reason_code
    if unit_reason not in _INVALID_UNIT_REASONS:
        unit_reason = next((item for item in diagnostic_codes if item in _INVALID_UNIT_REASONS), None)
    if coverage_state is CoverageState.COMPLETE and selected_granularity is ProjectionGranularity.PARTIAL_EVENT:
        raise ValueError("complete candidate cannot use partial_event granularity")
    if coverage_state is CoverageState.PARTIAL and selected_granularity in {
        ProjectionGranularity.EVENT,
        ProjectionGranularity.SUMMARY,
    }:
        raise ValueError("partial candidate must use partial_event granularity")
    _validate_metric_and_unit(metric_key, value, unit, unit_reason)
    _validate_value_presence(value, presence_state)


def _sorted_lineage(source_lineage: tuple[MetricContribution, ...]) -> tuple[MetricContribution, ...]:
    return tuple(sorted(source_lineage, key=lambda item: item.sort_key))


def build_event_candidate(
    *,
    provider_key: str,
    user_id: UUID,
    local_date: date,
    metric_key: str,
    contributions: tuple[MetricContribution, ...] | list[MetricContribution],
    event_set_known: bool = True,
    diagnostic_codes: tuple[ReasonCode, ...] = (),
) -> EventReconstructionCandidate:
    from .aggregation import aggregate_contributions

    ordered = tuple(contributions)
    aggregate = aggregate_contributions(ordered, event_set_known=event_set_known)
    if aggregate.coverage_state is CoverageState.PARTIAL:
        granularity = ProjectionGranularity.PARTIAL_EVENT
        reason_code = ReasonCode.PARTIAL_EVENTS_NO_SUMMARY
    elif aggregate.value is None:
        granularity = None
        reason_code = ReasonCode.ALL_SOURCES_MISSING
    else:
        granularity = ProjectionGranularity.EVENT
        reason_code = (
            ReasonCode.EVENT_COMPLETE_UNCERTAIN_NO_SUMMARY
            if aggregate.lineage_state is LineageState.UNCERTAIN
            else ReasonCode.EVENT_COMPLETE_CONFIRMED
        )
    return EventReconstructionCandidate(
        provider_key=provider_key,
        user_id=user_id,
        local_date=local_date,
        metric_key=metric_key,
        value=aggregate.value,
        unit=aggregate.unit,
        selected_granularity=granularity,
        presence_state=aggregate.presence_state,
        coverage_state=aggregate.coverage_state,
        resolution_state=aggregate.resolution_state,
        lineage_state=aggregate.lineage_state,
        source_lineage=_sorted_lineage(aggregate.source_lineage),
        reason_code=reason_code,
        diagnostic_codes=tuple(sorted(set(diagnostic_codes), key=lambda item: item.value)),
    )


def build_summary_candidate(
    *,
    provider_key: str,
    user_id: UUID,
    local_date: date,
    metric_key: str,
    value: Decimal | None,
    unit: str | None,
    presence_state: PresenceState,
    coverage_state: CoverageState,
    resolution_state: ResolutionState,
    lineage_state: LineageState,
    evidence_id: UUID,
    diagnostic_codes: tuple[ReasonCode, ...] = (),
) -> SummaryCandidate:
    lineage: tuple[MetricContribution, ...] = ()
    if value is not None:
        lineage = (
            MetricContribution(
                evidence_id=evidence_id,
                metric_key=metric_key,
                value=value,
                unit=unit,
                presence_state=presence_state,
                resolution_state=resolution_state,
                lineage_state=lineage_state,
                evidence_kind=EvidenceKind.SUMMARY,
                reason_code=diagnostic_codes[0] if diagnostic_codes else None,
            ),
        )
    return SummaryCandidate(
        provider_key=provider_key,
        user_id=user_id,
        local_date=local_date,
        metric_key=metric_key,
        value=value,
        unit=unit,
        selected_granularity=ProjectionGranularity.SUMMARY if value is not None else None,
        presence_state=presence_state,
        coverage_state=coverage_state,
        resolution_state=resolution_state,
        lineage_state=lineage_state,
        source_lineage=lineage,
        reason_code=ReasonCode.SUMMARY_ONLY,
        diagnostic_codes=tuple(sorted(set(diagnostic_codes), key=lambda item: item.value)),
    )
