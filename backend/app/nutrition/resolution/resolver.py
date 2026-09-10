from __future__ import annotations

from dataclasses import replace
from datetime import date
from uuid import UUID

from app.nutrition.enums import CoverageState, LineageState, PresenceState, ResolutionState

from .contracts import (
    EventReconstructionCandidate,
    MetricContribution,
    ProviderCandidate,
    SummaryCandidate,
)
from .eligibility import is_candidate_eligible, is_summary_usable
from .parity import ParityDiagnostic, compare_decimal_parity
from .reasons import ReasonCode

_EMPTY_PROVIDER = "unknown"
_EMPTY_USER_ID = UUID(int=0)
_EMPTY_DATE = date.min
_EMPTY_METRIC = "unknown"


def _scope(candidate: EventReconstructionCandidate | SummaryCandidate) -> tuple[object, object, object, str]:
    return (candidate.user_id, candidate.local_date, candidate.provider_key, candidate.metric_key)


def _event_reason(candidate: EventReconstructionCandidate) -> ReasonCode:
    if candidate.coverage_state is CoverageState.PARTIAL:
        return ReasonCode.PARTIAL_EVENTS_NO_SUMMARY
    if candidate.lineage_state is LineageState.UNCERTAIN:
        return ReasonCode.EVENT_COMPLETE_UNCERTAIN_NO_SUMMARY
    if (
        candidate.value is not None
        and candidate.coverage_state is CoverageState.COMPLETE
        and candidate.resolution_state is ResolutionState.RESOLVED
        and candidate.lineage_state is LineageState.CONFIRMED
    ):
        return ReasonCode.EVENT_COMPLETE_CONFIRMED
    return candidate.reason_code


def _diagnostics(*codes: ReasonCode) -> tuple[ReasonCode, ...]:
    return tuple(sorted(set(codes), key=lambda item: item.value))


def _diagnostic_evidence(
    *lineages: tuple[MetricContribution, ...],
) -> tuple[MetricContribution, ...]:
    return tuple(
        sorted(
            (replace(item, contributing=False) for lineage in lineages for item in lineage),
            key=lambda item: item.sort_key,
        )
    )


def _parity(
    event: EventReconstructionCandidate | None,
    summary: SummaryCandidate | None,
) -> ParityDiagnostic | None:
    if (
        event is None
        or summary is None
        or event.value is None
        or summary.value is None
        or event.unit != summary.unit
    ):
        return None
    return compare_decimal_parity(
        event.value,
        summary.value,
        sum(item.value_contributing for item in event.source_lineage),
    )


def _from_candidate(
    candidate: EventReconstructionCandidate | SummaryCandidate,
    *,
    reason_code: ReasonCode,
    diagnostic_codes: tuple[ReasonCode, ...] = (),
    parity_diagnostic: ParityDiagnostic | None = None,
    diagnostic_evidence: tuple[MetricContribution, ...] = (),
) -> ProviderCandidate:
    return ProviderCandidate(
        provider_key=candidate.provider_key,
        user_id=candidate.user_id,
        local_date=candidate.local_date,
        metric_key=candidate.metric_key,
        value=candidate.value,
        unit=candidate.unit,
        selected_granularity=candidate.selected_granularity,
        presence_state=candidate.presence_state,
        coverage_state=candidate.coverage_state,
        resolution_state=candidate.resolution_state,
        lineage_state=candidate.lineage_state,
        source_lineage=candidate.source_lineage,
        reason_code=reason_code,
        diagnostic_codes=_diagnostics(*candidate.diagnostic_codes, *diagnostic_codes),
        parity_diagnostic=parity_diagnostic,
        resolution_diagnostic_evidence=_diagnostic_evidence(diagnostic_evidence),
    )


def _empty_candidate(
    candidate: EventReconstructionCandidate | SummaryCandidate | None,
    *,
    reason_code: ReasonCode,
    diagnostic_codes: tuple[ReasonCode, ...] = (),
    diagnostic_evidence: tuple[MetricContribution, ...] = (),
) -> ProviderCandidate:
    if candidate is None:
        return ProviderCandidate(
            provider_key=_EMPTY_PROVIDER,
            user_id=_EMPTY_USER_ID,
            local_date=_EMPTY_DATE,
            metric_key=_EMPTY_METRIC,
            value=None,
            unit=None,
            selected_granularity=None,
            presence_state=PresenceState.MISSING,
            coverage_state=CoverageState.UNKNOWN,
            resolution_state=ResolutionState.UNRESOLVED,
            lineage_state=LineageState.UNKNOWN,
            source_lineage=(),
            reason_code=reason_code,
            diagnostic_codes=_diagnostics(*diagnostic_codes),
        )
    return ProviderCandidate(
        provider_key=candidate.provider_key,
        user_id=candidate.user_id,
        local_date=candidate.local_date,
        metric_key=candidate.metric_key,
        value=None,
        unit=None,
        selected_granularity=None,
        presence_state=PresenceState.MISSING,
        coverage_state=CoverageState.UNKNOWN,
        resolution_state=ResolutionState.UNRESOLVED,
        lineage_state=LineageState.UNKNOWN,
        source_lineage=(),
        reason_code=reason_code,
        diagnostic_codes=_diagnostics(*candidate.diagnostic_codes, *diagnostic_codes),
        resolution_diagnostic_evidence=_diagnostic_evidence(
            candidate.source_lineage,
            diagnostic_evidence,
        ),
    )


def resolve_event_vs_summary(
    event: EventReconstructionCandidate | None,
    summary: SummaryCandidate | None,
) -> ProviderCandidate:
    if event is None and summary is None:
        return _empty_candidate(None, reason_code=ReasonCode.ALL_SOURCES_MISSING)
    if event is not None and summary is not None and _scope(event) != _scope(summary):
        raise ValueError("event and summary candidate scope mismatch")

    parity_diagnostic = _parity(event, summary)
    summary_usable = summary is not None and is_summary_usable(summary)
    event_eligible = event is not None and is_candidate_eligible(event)

    if summary_usable and summary is not None:
        if event is not None and event.resolution_state is ResolutionState.CONFLICT:
            return _from_candidate(
                summary,
                reason_code=ReasonCode.SUMMARY_FALLBACK_EVENT_CONFLICT,
                parity_diagnostic=parity_diagnostic,
                diagnostic_evidence=event.source_lineage,
            )
        if event is not None and event.resolution_state is ResolutionState.DUPLICATE_CANDIDATE:
            return _from_candidate(
                summary,
                reason_code=ReasonCode.SUMMARY_FALLBACK_DUPLICATE_CANDIDATE,
                parity_diagnostic=parity_diagnostic,
                diagnostic_evidence=event.source_lineage,
            )
        if event is not None and event.coverage_state is CoverageState.PARTIAL:
            return _from_candidate(
                summary,
                reason_code=ReasonCode.SUMMARY_FALLBACK_PARTIAL_EVENTS,
                parity_diagnostic=parity_diagnostic,
                diagnostic_evidence=event.source_lineage,
            )
        if event is not None and event.lineage_state is LineageState.UNCERTAIN:
            return _from_candidate(
                summary,
                reason_code=ReasonCode.SUMMARY_FALLBACK_UNCERTAIN_LINEAGE,
                parity_diagnostic=parity_diagnostic,
                diagnostic_evidence=event.source_lineage,
            )
        if event_eligible and event is not None:
            return _from_candidate(
                event,
                reason_code=ReasonCode.EVENT_COMPLETE_CONFIRMED,
                parity_diagnostic=parity_diagnostic,
                diagnostic_evidence=summary.source_lineage,
            )
        return _from_candidate(
            summary,
            reason_code=ReasonCode.SUMMARY_ONLY,
            parity_diagnostic=parity_diagnostic,
            diagnostic_evidence=event.source_lineage if event is not None else (),
        )

    if event is not None and event_eligible:
        diagnostics: tuple[ReasonCode, ...] = ()
        if summary is not None:
            diagnostics = (ReasonCode.SUMMARY_UNUSABLE,)
        return _from_candidate(
            event,
            reason_code=_event_reason(event),
            diagnostic_codes=diagnostics,
            parity_diagnostic=parity_diagnostic,
            diagnostic_evidence=summary.source_lineage if summary is not None else (),
        )

    if summary is not None and event is None:
        return _empty_candidate(summary, reason_code=ReasonCode.SUMMARY_UNUSABLE)
    if event is not None and summary is not None:
        return _empty_candidate(
            event,
            reason_code=ReasonCode.SUMMARY_UNUSABLE,
            diagnostic_evidence=summary.source_lineage,
        )
    return _empty_candidate(event or summary, reason_code=ReasonCode.ALL_SOURCES_MISSING)
