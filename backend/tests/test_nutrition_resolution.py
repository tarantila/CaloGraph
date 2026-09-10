from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.nutrition.enums import (
    CoverageState,
    LineageState,
    PresenceState,
    ProjectionGranularity,
    ResolutionState,
)
from app.nutrition.resolution import (
    EventReconstructionCandidate,
    EvidenceKind,
    MetricContribution,
    ParityDiagnostic,
    ProviderCandidate,
    ReasonCode,
    SummaryCandidate,
    aggregate_contributions,
    aggregate_coverage,
    aggregate_lineage,
    aggregate_presence,
    aggregate_resolution,
    build_event_candidate,
    canonical_unit,
    compare_decimal_parity,
    is_candidate_eligible,
    is_known_metric,
    is_summary_usable,
    metric_definition,
    resolve_event_vs_summary,
)

USER_ID = UUID("11111111-1111-1111-1111-111111111111")
OTHER_USER_ID = UUID("22222222-2222-2222-2222-222222222222")
LOCAL_DATE = date(2026, 9, 7)

def evidence_uuid(value: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"https://calograph.test/b1/{value}")


def contribution(
    evidence_id: str | UUID,
    value: Decimal | None,
    *,
    metric_key: str = "protein_g",
    unit: str | None = "g",
    presence: PresenceState | None = None,
    resolution: ResolutionState = ResolutionState.RESOLVED,
    lineage: LineageState = LineageState.CONFIRMED,
    evidence_kind: EvidenceKind = EvidenceKind.EVENT,
    reason_code: ReasonCode | None = None,
    source_observation_id: str | UUID | None = None,
) -> MetricContribution:
    if presence is None:
        presence = (
            PresenceState.MISSING
            if value is None
            else PresenceState.EXPLICIT_ZERO
            if value == Decimal("0")
            else PresenceState.SUPPLIED
        )
    evidence_id = evidence_uuid(evidence_id) if isinstance(evidence_id, str) else evidence_id
    if source_observation_id is None:
        source_observation_id = evidence_uuid(f"{evidence_id}:source")
    elif isinstance(source_observation_id, str):
        source_observation_id = evidence_uuid(source_observation_id)
    return MetricContribution(
        evidence_id=evidence_id,
        source_observation_id=source_observation_id,
        metric_key=metric_key,
        value=value,
        unit=unit,
        presence_state=presence,
        resolution_state=resolution,
        lineage_state=lineage,
        evidence_kind=evidence_kind,
        reason_code=reason_code,
    )


def event_candidate(*contributions: MetricContribution, event_set_known: bool = True):
    return build_event_candidate(
        provider_key="yazio",
        user_id=USER_ID,
        local_date=LOCAL_DATE,
        metric_key="protein_g",
        contributions=contributions,
        event_set_known=event_set_known,
    )


def summary_candidate(
    value: Decimal | None,
    *,
    unit: str | None = "g",
    presence: PresenceState | None = None,
    coverage: CoverageState = CoverageState.COMPLETE,
    resolution: ResolutionState = ResolutionState.RESOLVED,
    lineage: LineageState = LineageState.CONFIRMED,
    metric_key: str = "protein_g",
    user_id: UUID = USER_ID,
    local_date: date = LOCAL_DATE,
    diagnostic_codes: tuple[ReasonCode, ...] = (),
) -> SummaryCandidate:
    if presence is None:
        presence = (
            PresenceState.MISSING
            if value is None
            else PresenceState.EXPLICIT_ZERO
            if value == Decimal("0")
            else PresenceState.SUPPLIED
        )
    source_lineage: tuple[MetricContribution, ...] = ()
    if value is not None:
        source_lineage = (
            contribution(
                "summary-1",
                value,
                metric_key=metric_key,
                unit=unit,
                presence=presence,
                resolution=resolution,
                lineage=lineage,
                evidence_kind=EvidenceKind.SUMMARY,
                reason_code=diagnostic_codes[0] if diagnostic_codes else None,
            ),
        )
        if coverage is CoverageState.PARTIAL:
            source_lineage += (
                contribution(
                    "summary-missing",
                    None,
                    metric_key=metric_key,
                    unit=unit,
                    resolution=resolution,
                    lineage=lineage,
                    evidence_kind=EvidenceKind.SUMMARY,
                ),
            )
    return SummaryCandidate(
        provider_key="yazio",
        user_id=user_id,
        local_date=local_date,
        metric_key=metric_key,
        value=value,
        unit=unit,
        selected_granularity=(
            ProjectionGranularity.SUMMARY
            if value is not None and coverage is CoverageState.COMPLETE
            else None
        ),
        presence_state=presence,
        coverage_state=coverage,
        resolution_state=resolution,
        lineage_state=lineage,
        source_lineage=source_lineage,
        reason_code=ReasonCode.SUMMARY_ONLY,
        diagnostic_codes=diagnostic_codes,
    )


def test_metric_registry_contains_only_canonical_v1_metrics() -> None:
    expected = {
        "dietary_energy_kcal": "kcal",
        "protein_g": "g",
        "carbohydrates_g": "g",
        "fat_g": "g",
        "fiber_g": "g",
        "sugar_g": "g",
        "saturated_fat_g": "g",
    }
    assert {key: metric_definition(key).canonical_unit for key in expected} == expected
    assert all(is_known_metric(key) for key in expected)
    assert canonical_unit("salt") is None
    assert not is_known_metric("salt_g")


def test_metric_contribution_is_immutable_and_requires_decimal_values() -> None:
    item = contribution("e1", Decimal("2.5"))
    with pytest.raises((AttributeError, TypeError)):
        item.value = Decimal("3")  # type: ignore[misc]
    with pytest.raises(TypeError, match="Decimal"):
        contribution("e2", 2.5)  # type: ignore[arg-type]


def test_explicit_zero_requires_exact_decimal_zero_and_missing_requires_none() -> None:
    zero = contribution("zero", Decimal("0"))
    missing = contribution("missing", None)
    assert zero.presence_state is PresenceState.EXPLICIT_ZERO
    assert zero.value == Decimal("0")
    assert missing.presence_state is PresenceState.MISSING
    with pytest.raises(ValueError, match="explicit_zero"):
        contribution("invalid", None, presence=PresenceState.EXPLICIT_ZERO)
    with pytest.raises(ValueError, match="missing"):
        contribution("invalid", Decimal("1"), presence=PresenceState.MISSING)


def test_presence_and_coverage_keep_zero_and_missing_distinct() -> None:
    result = aggregate_contributions(
        [contribution("zero", Decimal("0")), contribution("missing", None)]
    )
    assert result.value == Decimal("0")
    assert result.presence_state is PresenceState.EXPLICIT_ZERO
    assert result.coverage_state is CoverageState.PARTIAL
    assert aggregate_presence([contribution("zero", Decimal("0")), contribution("missing", None)]) is PresenceState.EXPLICIT_ZERO
    assert aggregate_coverage([contribution("zero", Decimal("0")), contribution("missing", None)]) is CoverageState.PARTIAL

    supplied = aggregate_contributions(
        [contribution("value", Decimal("5")), contribution("missing", None)]
    )
    assert supplied.value == Decimal("5")
    assert supplied.presence_state is PresenceState.SUPPLIED
    assert supplied.coverage_state is CoverageState.PARTIAL


def test_all_zero_contributions_are_complete_explicit_zero() -> None:
    result = aggregate_contributions(
        [contribution("zero-1", Decimal("0")), contribution("zero-2", Decimal("0"))]
    )
    assert result.value == Decimal("0")
    assert result.presence_state is PresenceState.EXPLICIT_ZERO
    assert result.coverage_state is CoverageState.COMPLETE


def test_none_only_contributions_have_no_numeric_value() -> None:
    result = aggregate_contributions([contribution("missing-1", None), contribution("missing-2", None)])
    assert result.value is None
    assert result.presence_state is PresenceState.MISSING
    assert result.coverage_state is CoverageState.UNKNOWN

def test_unknown_event_set_keeps_coverage_unknown_even_with_value() -> None:
    result = aggregate_contributions([contribution("value", Decimal("5"))], event_set_known=False)
    assert result.value == Decimal("5")
    assert result.coverage_state is CoverageState.UNKNOWN


def test_metric_scopes_cannot_be_mixed() -> None:
    with pytest.raises(ValueError, match="one metric"):
        aggregate_contributions(
            [
                contribution("protein", Decimal("1"), metric_key="protein_g"),
                contribution("energy", Decimal("2"), metric_key="dietary_energy_kcal", unit="kcal"),
            ]
        )


def test_aggregation_severity_and_lineage_are_conservative() -> None:
    assert aggregate_resolution(
        [
            contribution("resolved", Decimal("1")),
            contribution("unresolved", Decimal("2"), resolution=ResolutionState.UNRESOLVED),
            contribution("duplicate", Decimal("3"), resolution=ResolutionState.DUPLICATE_CANDIDATE),
            contribution("conflict", Decimal("4"), resolution=ResolutionState.CONFLICT),
        ]
    ) is ResolutionState.CONFLICT
    assert aggregate_resolution(
        [contribution("duplicate", Decimal("1"), resolution=ResolutionState.DUPLICATE_CANDIDATE)]
    ) is ResolutionState.DUPLICATE_CANDIDATE
    assert aggregate_resolution(
        [contribution("unresolved", Decimal("1"), resolution=ResolutionState.UNRESOLVED)]
    ) is ResolutionState.UNRESOLVED

    assert aggregate_lineage(
        [contribution("a", Decimal("1"), lineage=LineageState.CONFIRMED), contribution("b", Decimal("2"), lineage=LineageState.UNCERTAIN)]
    ) is LineageState.UNCERTAIN
    assert aggregate_lineage(
        [contribution("a", Decimal("1"), lineage=LineageState.CONFIRMED), contribution("b", Decimal("2"), lineage=LineageState.UNKNOWN)]
    ) is LineageState.UNKNOWN
    assert aggregate_lineage(
        [contribution("a", Decimal("1"), lineage=LineageState.CONFIRMED), contribution("missing", None, lineage=LineageState.UNKNOWN)]
    ) is LineageState.CONFIRMED


def test_simple_and_product_derived_values_sum_without_losing_uncertainty() -> None:
    result = aggregate_contributions(
        [
            contribution("simple", Decimal("10"), lineage=LineageState.CONFIRMED),
            contribution("derived", Decimal("20"), lineage=LineageState.UNCERTAIN),
        ]
    )
    assert result.value == Decimal("30")
    assert result.coverage_state is CoverageState.COMPLETE
    assert result.lineage_state is LineageState.UNCERTAIN


def test_partial_event_is_eligible_by_explicit_exception() -> None:
    candidate = event_candidate(
        contribution("value", Decimal("5")),
        contribution("missing", None, resolution=ResolutionState.UNRESOLVED),
    )
    assert candidate.selected_granularity is ProjectionGranularity.PARTIAL_EVENT
    assert candidate.coverage_state is CoverageState.PARTIAL
    assert candidate.resolution_state is ResolutionState.UNRESOLVED
    assert candidate.reason_code is ReasonCode.PARTIAL_EVENTS_NO_SUMMARY
    assert is_candidate_eligible(candidate)


def test_summary_usability_accepts_explicit_zero_and_rejects_bad_states() -> None:
    assert is_summary_usable(summary_candidate(Decimal("0")))
    assert is_summary_usable(summary_candidate(Decimal("10"), lineage=LineageState.UNCERTAIN))
    assert not is_summary_usable(summary_candidate(None))
    assert not is_summary_usable(summary_candidate(Decimal("10"), coverage=CoverageState.PARTIAL))
    assert not is_summary_usable(summary_candidate(Decimal("10"), resolution=ResolutionState.UNRESOLVED))
    assert not is_summary_usable(summary_candidate(Decimal("10"), lineage=LineageState.UNKNOWN))
    assert not is_summary_usable(
        summary_candidate(Decimal("10"), unit="mg", diagnostic_codes=(ReasonCode.INVALID_UNIT,))
    )


@pytest.mark.parametrize(
    ("event", "summary", "expected_granularity", "expected_reason", "expected_value"),
    [
        (
            event_candidate(contribution("e", Decimal("100"))),
            summary_candidate(Decimal("100")),
            ProjectionGranularity.EVENT,
            ReasonCode.EVENT_COMPLETE_CONFIRMED,
            Decimal("100"),
        ),
        (
            event_candidate(contribution("e", Decimal("100"), lineage=LineageState.UNCERTAIN)),
            summary_candidate(Decimal("100")),
            ProjectionGranularity.SUMMARY,
            ReasonCode.SUMMARY_FALLBACK_UNCERTAIN_LINEAGE,
            Decimal("100"),
        ),
        (
            event_candidate(
                contribution("e", Decimal("5")),
                contribution("missing", None, resolution=ResolutionState.UNRESOLVED),
            ),
            summary_candidate(Decimal("100")),
            ProjectionGranularity.SUMMARY,
            ReasonCode.SUMMARY_FALLBACK_PARTIAL_EVENTS,
            Decimal("100"),
        ),
        (
            event_candidate(
                contribution("e", Decimal("5")),
                contribution("missing", None, resolution=ResolutionState.UNRESOLVED),
            ),
            None,
            ProjectionGranularity.PARTIAL_EVENT,
            ReasonCode.PARTIAL_EVENTS_NO_SUMMARY,
            Decimal("5"),
        ),
        (
            event_candidate(contribution("e", Decimal("100"))),
            None,
            ProjectionGranularity.EVENT,
            ReasonCode.EVENT_COMPLETE_CONFIRMED,
            Decimal("100"),
        ),
        (
            event_candidate(contribution("e", Decimal("100"), lineage=LineageState.UNCERTAIN)),
            None,
            ProjectionGranularity.EVENT,
            ReasonCode.EVENT_COMPLETE_UNCERTAIN_NO_SUMMARY,
            Decimal("100"),
        ),
    ],
)
def test_event_summary_decision_table(event, summary, expected_granularity, expected_reason, expected_value):
    result = resolve_event_vs_summary(event, summary)
    assert result.selected_granularity is expected_granularity
    assert result.reason_code is expected_reason
    assert result.value == expected_value


def test_event_and_summary_zero_are_not_added() -> None:
    result = resolve_event_vs_summary(event_candidate(contribution("e", Decimal("0"))), summary_candidate(Decimal("0")))
    assert result.value == Decimal("0")
    assert result.presence_state is PresenceState.EXPLICIT_ZERO


def test_missing_event_and_zero_summary_select_summary() -> None:
    result = resolve_event_vs_summary(
        event_candidate(contribution("missing", None, resolution=ResolutionState.UNRESOLVED)),
        summary_candidate(Decimal("0")),
    )
    assert result.value == Decimal("0")
    assert result.selected_granularity is ProjectionGranularity.SUMMARY


def test_zero_event_and_missing_summary_select_zero_event() -> None:
    result = resolve_event_vs_summary(event_candidate(contribution("e", Decimal("0"))), summary_candidate(None))
    assert result.value == Decimal("0")
    assert result.selected_granularity is ProjectionGranularity.EVENT


def test_conflict_and_duplicate_event_fall_back_to_usable_summary() -> None:
    conflict = event_candidate(
        contribution("e", Decimal("100"), resolution=ResolutionState.CONFLICT)
    )
    duplicate = event_candidate(
        contribution("e", Decimal("100"), resolution=ResolutionState.DUPLICATE_CANDIDATE)
    )
    summary = summary_candidate(Decimal("100"))
    assert resolve_event_vs_summary(conflict, summary).reason_code is ReasonCode.SUMMARY_FALLBACK_EVENT_CONFLICT
    assert resolve_event_vs_summary(duplicate, summary).reason_code is ReasonCode.SUMMARY_FALLBACK_DUPLICATE_CANDIDATE


def test_malformed_summary_does_not_override_usable_event() -> None:
    result = resolve_event_vs_summary(
        event_candidate(contribution("e", Decimal("100"))),
        summary_candidate(Decimal("100"), unit="mg", diagnostic_codes=(ReasonCode.INVALID_UNIT,)),
    )
    assert result.selected_granularity is ProjectionGranularity.EVENT
    assert result.value == Decimal("100")
    assert ReasonCode.SUMMARY_UNUSABLE in result.diagnostic_codes


def test_unusable_summary_fallback_clears_invalid_unit() -> None:
    result = resolve_event_vs_summary(
        None,
        summary_candidate(Decimal("10"), unit="mg", diagnostic_codes=(ReasonCode.INVALID_UNIT,)),
    )
    assert result.value is None
    assert result.unit is None
    assert result.reason_code is ReasonCode.SUMMARY_UNUSABLE

def test_missing_sources_produce_explicit_empty_provider_candidate() -> None:
    result = resolve_event_vs_summary(None, None)
    assert isinstance(result, ProviderCandidate)
    assert result.value is None
    assert result.selected_granularity is None
    assert result.reason_code is ReasonCode.ALL_SOURCES_MISSING
    assert result.presence_state is PresenceState.MISSING
    assert result.coverage_state is CoverageState.UNKNOWN


def test_scope_mismatch_is_an_explicit_contract_error() -> None:
    with pytest.raises(ValueError, match="scope"):
        resolve_event_vs_summary(
            event_candidate(contribution("e", Decimal("1"))),
            summary_candidate(Decimal("1"), user_id=OTHER_USER_ID),
        )

def test_invalid_contract_states_fail_fast() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        MetricContribution(
            evidence_id=evidence_uuid("salt"),
            source_observation_id=evidence_uuid("salt-source"),
            metric_key="salt",
            value=Decimal("1"),
            unit="g",
            presence_state=PresenceState.SUPPLIED,
            resolution_state=ResolutionState.RESOLVED,
            lineage_state=LineageState.CONFIRMED,
            evidence_kind=EvidenceKind.EVENT,
        )
    with pytest.raises(ValueError, match="unit"):
        MetricContribution(
            evidence_id=evidence_uuid("bad-unit"),
            source_observation_id=evidence_uuid("bad-unit-source"),
            metric_key="protein_g",
            value=Decimal("1"),
            unit="mg",
            presence_state=PresenceState.SUPPLIED,
            resolution_state=ResolutionState.RESOLVED,
            lineage_state=LineageState.CONFIRMED,
            evidence_kind=EvidenceKind.EVENT,
        )
    with pytest.raises(ValueError, match="granularity"):
        EventReconstructionCandidate(
            provider_key="yazio",
            user_id=USER_ID,
            local_date=LOCAL_DATE,
            metric_key="protein_g",
            value=Decimal("1"),
            unit="g",
            selected_granularity="invalid",  # type: ignore[arg-type]
            presence_state=PresenceState.SUPPLIED,
            coverage_state=CoverageState.COMPLETE,
            resolution_state=ResolutionState.RESOLVED,
            lineage_state=LineageState.CONFIRMED,
            source_lineage=(),
            reason_code=ReasonCode.EVENT_COMPLETE_CONFIRMED,
        )

def test_complete_event_cannot_be_tagged_as_partial_event() -> None:
    complete = event_candidate(contribution("complete", Decimal("10")))
    with pytest.raises(ValueError, match="complete candidate"):
        replace(complete, selected_granularity=ProjectionGranularity.PARTIAL_EVENT)

def test_value_bearing_candidate_requires_evidence_lineage() -> None:
    complete = event_candidate(contribution("complete", Decimal("10")))
    with pytest.raises(ValueError, match="source_lineage"):
        replace(complete, source_lineage=())

def test_candidate_value_must_match_evidence_aggregate() -> None:
    complete = event_candidate(contribution("complete", Decimal("10")))
    with pytest.raises(ValueError, match="candidate value"):
        replace(complete, value=Decimal("999"))

def test_candidate_coverage_must_match_evidence_aggregate() -> None:
    partial = event_candidate(
        contribution("value", Decimal("10")),
        contribution("missing", None),
    )
    with pytest.raises(ValueError, match="candidate coverage"):
        replace(
            partial,
            coverage_state=CoverageState.COMPLETE,
            selected_granularity=ProjectionGranularity.EVENT,
        )


def test_parity_uses_decimal_only_and_does_not_change_selection() -> None:
    diagnostic = compare_decimal_parity(Decimal("100"), Decimal("100.000000000001"), 1)
    assert isinstance(diagnostic, ParityDiagnostic)
    assert diagnostic.delta == Decimal("0.000000000001")
    assert diagnostic.within_tolerance
    assert diagnostic.contribution_count == 1

    outside = compare_decimal_parity(Decimal("100"), Decimal("100.1"), 1)
    assert not outside.within_tolerance
    with pytest.raises(TypeError, match="Decimal"):
        compare_decimal_parity(100.0, Decimal("100"), 1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="contribution_count"):
        compare_decimal_parity(Decimal("1"), Decimal("1"), -1)

    event = event_candidate(contribution("e", Decimal("100"), lineage=LineageState.UNCERTAIN))
    summary = summary_candidate(Decimal("100.1"))
    result = resolve_event_vs_summary(event, summary)
    assert result.selected_granularity is ProjectionGranularity.SUMMARY
    assert result.parity_diagnostic is not None



def test_duplicate_evidence_ids_remain_permutation_deterministic() -> None:
    event = contribution("same", Decimal("1"), evidence_kind=EvidenceKind.EVENT)
    summary = contribution("same", Decimal("1"), evidence_kind=EvidenceKind.SUMMARY)
    assert aggregate_contributions([event, summary]) == aggregate_contributions([summary, event])
@given(st.permutations(["a", "b", "c"]))
def test_contribution_permutation_does_not_change_aggregate(order: list[str]) -> None:
    by_id = {
        "a": contribution("a", Decimal("1")),
        "b": contribution("b", Decimal("0")),
        "c": contribution("c", None, resolution=ResolutionState.UNRESOLVED),
    }
    result = aggregate_contributions([by_id[key] for key in order])
    assert result == aggregate_contributions([by_id["a"], by_id["b"], by_id["c"]])


def test_lineage_and_source_evidence_are_deterministically_sorted() -> None:
    result = aggregate_contributions(
        [contribution("z", Decimal("1")), contribution("a", Decimal("2"))]
    )
    assert [item.evidence_id for item in result.source_lineage] == [
        evidence_uuid("a"),
        evidence_uuid("z"),
    ]
    candidate = event_candidate(contribution("z", Decimal("1")), contribution("a", Decimal("2")))
    assert [item.evidence_id for item in candidate.source_lineage] == [
        evidence_uuid("a"),
        evidence_uuid("z"),
    ]


def test_partial_event_value_is_eligible_without_quality_override() -> None:
    partial = event_candidate(
        contribution("present", Decimal("80")),
        contribution("missing", None, resolution=ResolutionState.UNRESOLVED),
    )
    complete = event_candidate(contribution("complete", Decimal("70")))
    assert is_candidate_eligible(partial)
    assert is_candidate_eligible(complete)
    assert partial.value == Decimal("80")
    assert complete.value == Decimal("70")


def test_unsupported_metric_is_not_a_canonical_candidate() -> None:
    unsupported = SummaryCandidate(
        provider_key="yazio",
        user_id=USER_ID,
        local_date=LOCAL_DATE,
        metric_key="salt",
        value=None,
        unit=None,
        selected_granularity=None,
        presence_state=PresenceState.UNSUPPORTED,
        coverage_state=CoverageState.UNKNOWN,
        resolution_state=ResolutionState.UNRESOLVED,
        lineage_state=LineageState.UNKNOWN,
        source_lineage=(),
        reason_code=ReasonCode.UNSUPPORTED_METRIC,
    )
    assert not is_summary_usable(unsupported)
    assert not is_candidate_eligible(unsupported)


def test_event_candidate_keeps_provider_scope_and_metric_identity() -> None:
    candidate = event_candidate(contribution("e", Decimal("1")))
    assert candidate.provider_key == "yazio"
    assert candidate.user_id == USER_ID
    assert candidate.local_date == LOCAL_DATE
    assert candidate.metric_key == "protein_g"
