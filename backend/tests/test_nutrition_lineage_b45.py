from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.nutrition.enums import (
    CoverageState,
    LineageState,
    PresenceState,
    ProjectionGranularity,
    ResolutionState,
)
from app.nutrition.resolution import (
    EvidenceKind,
    MetricContribution,
    ProviderCandidate,
    ReasonCode,
    build_event_candidate,
    build_summary_candidate,
    resolve_event_vs_summary,
)
from app.source_priority.contracts import (
    AppliedPriorityScope,
    PriorityPolicySnapshot,
    PriorityReasonCode,
    PriorityRuleSnapshot,
    PrioritySelectionRole,
)
from app.source_priority.selection import select_by_source_priority

USER_ID = UUID("11111111-1111-1111-1111-111111111111")
LOCAL_DATE = date(2026, 9, 10)
METRIC_KEY = "protein_g"
POLICY_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
GOOGLE_RULE_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
YAZIO_RULE_ID = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")


def _contribution(
    evidence_id: str,
    source_observation_id: str,
    value: Decimal | None,
    *,
    evidence_kind: EvidenceKind = EvidenceKind.FIELD_OBSERVATION,
    lineage_state: LineageState = LineageState.CONFIRMED,
    presence_state: PresenceState | None = None,
    resolution_state: ResolutionState = ResolutionState.RESOLVED,
) -> MetricContribution:
    if presence_state is None:
        presence_state = (
            PresenceState.EXPLICIT_ZERO if value == Decimal("0") else PresenceState.SUPPLIED
        ) if value is not None else PresenceState.MISSING
    return MetricContribution(
        evidence_id=UUID(evidence_id),
        source_observation_id=UUID(source_observation_id),
        metric_key=METRIC_KEY,
        value=value,
        unit="g" if value is not None else None,
        presence_state=presence_state,
        resolution_state=resolution_state,
        lineage_state=lineage_state,
        evidence_kind=evidence_kind,
        reason_code=None,
    )


def _summary(value: Decimal, source_observation_id: str):
    return build_summary_candidate(
        provider_key="yazio",
        user_id=USER_ID,
        local_date=LOCAL_DATE,
        metric_key=METRIC_KEY,
        value=value,
        unit="g",
        presence_state=PresenceState.SUPPLIED,
        coverage_state=CoverageState.COMPLETE,
        resolution_state=ResolutionState.RESOLVED,
        lineage_state=LineageState.CONFIRMED,
        evidence_id=UUID(source_observation_id),
        source_observation_id=UUID(source_observation_id),
    )


def _policy(*rules: PriorityRuleSnapshot) -> PriorityPolicySnapshot:
    return PriorityPolicySnapshot(
        policy_id=POLICY_ID,
        user_id=USER_ID,
        version=1,
        effective_from=datetime(2026, 9, 1, tzinfo=UTC),
        rules=rules,
    )


def _rule(provider_key: str, rule_id: UUID, rank: int) -> PriorityRuleSnapshot:
    return PriorityRuleSnapshot(
        rule_id=rule_id,
        data_area="nutrition",
        metric_key=None,
        provider_key=provider_key,
        priority_rank=rank,
    )


def _candidate(
    provider_key: str,
    value: Decimal | None,
    *,
    source_observation_id: str,
    presence_state: PresenceState | None = None,
) -> ProviderCandidate:
    contribution = _contribution(
        f"{source_observation_id[:8]}-0000-0000-0000-000000000001",
        source_observation_id,
        value,
        presence_state=presence_state,
    )
    return ProviderCandidate(
        provider_key=provider_key,
        user_id=USER_ID,
        local_date=LOCAL_DATE,
        metric_key=METRIC_KEY,
        value=value,
        unit=contribution.unit,
        selected_granularity=(ProjectionGranularity.EVENT if value is not None else None),
        presence_state=contribution.presence_state,
        coverage_state=CoverageState.COMPLETE if value is not None else CoverageState.UNKNOWN,
        resolution_state=contribution.resolution_state,
        lineage_state=contribution.lineage_state,
        source_lineage=(contribution,),
        reason_code=(
            ReasonCode.EVENT_COMPLETE_CONFIRMED
            if value is not None
            else ReasonCode.ALL_SOURCES_MISSING
        ),
    )


def test_missing_source_observation_reference_fails_closed() -> None:
    with pytest.raises(TypeError, match="source_observation_id"):
        MetricContribution(
            evidence_id=UUID("00000000-0000-0000-0000-000000000001"),
            source_observation_id=None,  # type: ignore[arg-type]
            metric_key=METRIC_KEY,
            value=None,
            unit=None,
            presence_state=PresenceState.MISSING,
            resolution_state=ResolutionState.UNRESOLVED,
            lineage_state=LineageState.UNKNOWN,
            evidence_kind=EvidenceKind.CONSUMPTION_EVENT,
        )


def test_metric_contribution_requires_explicit_source_observation_reference() -> None:
    contribution = _contribution(
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
        Decimal("10"),
    )

    assert contribution.evidence_kind is EvidenceKind.FIELD_OBSERVATION
    assert contribution.evidence_id != contribution.source_observation_id
    assert contribution.value_contributing is True


def test_event_aggregate_preserves_exact_contributing_source_observations() -> None:
    candidate = build_event_candidate(
        provider_key="yazio",
        user_id=USER_ID,
        local_date=LOCAL_DATE,
        metric_key=METRIC_KEY,
        contributions=(
            _contribution(
                "00000000-0000-0000-0000-000000000011",
                "00000000-0000-0000-0000-000000000012",
                Decimal("10"),
                evidence_kind=EvidenceKind.FIELD_OBSERVATION,
            ),
            _contribution(
                "00000000-0000-0000-0000-000000000021",
                "00000000-0000-0000-0000-000000000022",
                Decimal("20"),
                evidence_kind=EvidenceKind.FIELD_OBSERVATION,
            ),
        ),
    )

    assert candidate.value == Decimal("30")
    assert sum(item.value for item in candidate.source_lineage if item.value_contributing) == Decimal("30")
    assert {
        item.source_observation_id
        for item in candidate.source_lineage
        if item.value_contributing
    } == {
        UUID("00000000-0000-0000-0000-000000000012"),
        UUID("00000000-0000-0000-0000-000000000022"),
    }


def test_partial_event_preserves_missing_event_as_diagnostic_evidence() -> None:
    candidate = build_event_candidate(
        provider_key="yazio",
        user_id=USER_ID,
        local_date=LOCAL_DATE,
        metric_key=METRIC_KEY,
        contributions=(
            _contribution(
                "00000000-0000-0000-0000-000000000031",
                "00000000-0000-0000-0000-000000000032",
                Decimal("20"),
            ),
            _contribution(
                "00000000-0000-0000-0000-000000000041",
                "00000000-0000-0000-0000-000000000042",
                None,
                evidence_kind=EvidenceKind.CONSUMPTION_EVENT,
            ),
        ),
    )

    assert candidate.value == Decimal("20")
    assert [
        item.value for item in candidate.source_lineage if item.value_contributing
    ] == [Decimal("20")]
    assert [item.value for item in candidate.source_lineage if not item.value_contributing] == [None]
    assert next(
        item.source_observation_id
        for item in candidate.source_lineage
        if not item.value_contributing
    ) == UUID("00000000-0000-0000-0000-000000000042")


def test_zero_remains_an_exact_contributing_value() -> None:
    candidate = build_event_candidate(
        provider_key="yazio",
        user_id=USER_ID,
        local_date=LOCAL_DATE,
        metric_key=METRIC_KEY,
        contributions=(
            _contribution(
                "00000000-0000-0000-0000-000000000051",
                "00000000-0000-0000-0000-000000000052",
                Decimal("0"),
                presence_state=PresenceState.EXPLICIT_ZERO,
            ),
        ),
    )

    assert candidate.value == Decimal("0")
    assert candidate.source_lineage[0].value == Decimal("0")


def test_event_summary_resolution_preserves_loser_as_diagnostic() -> None:
    event = build_event_candidate(
        provider_key="yazio",
        user_id=USER_ID,
        local_date=LOCAL_DATE,
        metric_key=METRIC_KEY,
        contributions=(
            _contribution(
                "00000000-0000-0000-0000-000000000061",
                "00000000-0000-0000-0000-000000000062",
                Decimal("20"),
                lineage_state=LineageState.CONFIRMED,
            ),
        ),
    )
    summary = _summary(Decimal("20"), "00000000-0000-0000-0000-000000000072")

    result = resolve_event_vs_summary(event, summary)

    assert result.selected_granularity is ProjectionGranularity.EVENT
    assert result.value == Decimal("20")
    assert result.diagnostic_evidence[0].source_observation_id == UUID(
        "00000000-0000-0000-0000-000000000072"
    )
    assert result.diagnostic_evidence[0].value == Decimal("20")
    assert result.diagnostic_evidence[0].value_contributing is False


def test_uncertain_event_and_partial_event_preserve_loser_evidence() -> None:
    uncertain_event = build_event_candidate(
        provider_key="yazio",
        user_id=USER_ID,
        local_date=LOCAL_DATE,
        metric_key=METRIC_KEY,
        contributions=(
            _contribution(
                "00000000-0000-0000-0000-000000000081",
                "00000000-0000-0000-0000-000000000082",
                Decimal("20"),
                lineage_state=LineageState.UNCERTAIN,
            ),
        ),
    )
    summary = _summary(Decimal("20"), "00000000-0000-0000-0000-000000000092")
    uncertain_result = resolve_event_vs_summary(uncertain_event, summary)

    partial_event = build_event_candidate(
        provider_key="yazio",
        user_id=USER_ID,
        local_date=LOCAL_DATE,
        metric_key=METRIC_KEY,
        contributions=(
            _contribution(
                "00000000-0000-0000-0000-0000000000a1",
                "00000000-0000-0000-0000-0000000000a2",
                Decimal("20"),
            ),
            _contribution(
                "00000000-0000-0000-0000-0000000000b1",
                "00000000-0000-0000-0000-0000000000b2",
                None,
                evidence_kind=EvidenceKind.CONSUMPTION_EVENT,
            ),
        ),
    )
    partial_result = resolve_event_vs_summary(partial_event, summary)

    assert uncertain_result.selected_granularity is ProjectionGranularity.SUMMARY
    assert uncertain_result.diagnostic_evidence[0].source_observation_id == UUID(
        "00000000-0000-0000-0000-000000000082"
    )
    assert uncertain_result.diagnostic_evidence[0].value == Decimal("20")
    assert uncertain_result.diagnostic_evidence[0].value_contributing is False
    assert partial_result.selected_granularity is ProjectionGranularity.SUMMARY
    assert {item.source_observation_id for item in partial_result.diagnostic_evidence} >= {
        UUID("00000000-0000-0000-0000-0000000000a2"),
        UUID("00000000-0000-0000-0000-0000000000b2"),
    }


def test_b4_dispositions_preserve_applicable_candidates() -> None:
    google = _candidate(
        "google_health",
        Decimal("10"),
        source_observation_id="00000000-0000-0000-0000-000000000102",
    )
    yazio = _candidate(
        "yazio",
        Decimal("20"),
        source_observation_id="00000000-0000-0000-0000-000000000112",
    )
    selection = select_by_source_priority(
        user_id=USER_ID,
        local_date=LOCAL_DATE,
        metric_key=METRIC_KEY,
        candidates={"yazio": yazio, "google_health": google},
        policy=_policy(_rule("google_health", GOOGLE_RULE_ID, 1), _rule("yazio", YAZIO_RULE_ID, 2)),
    )

    assert selection.selected_candidate == google
    assert selection.dispositions[0].candidate == google
    assert selection.dispositions[1].candidate == yazio
    assert selection.dispositions[0].role is PrioritySelectionRole.SELECTED
    assert selection.dispositions[1].role is PrioritySelectionRole.REJECTED
    assert selection.dispositions[0].rule_id == GOOGLE_RULE_ID


def test_b4_ineligible_and_missing_candidates_are_preserved_without_fakes() -> None:
    ineligible = _candidate(
        "google_health",
        None,
        source_observation_id="00000000-0000-0000-0000-000000000122",
    )
    yazio = _candidate(
        "yazio",
        Decimal("20"),
        source_observation_id="00000000-0000-0000-0000-000000000132",
    )
    selection = select_by_source_priority(
        user_id=USER_ID,
        local_date=LOCAL_DATE,
        metric_key=METRIC_KEY,
        candidates=(yazio, ineligible),
        policy=_policy(_rule("google_health", GOOGLE_RULE_ID, 1), _rule("yazio", YAZIO_RULE_ID, 2)),
    )

    assert selection.dispositions[0].candidate == ineligible
    assert selection.dispositions[0].role is PrioritySelectionRole.DIAGNOSTIC
    assert selection.dispositions[1].candidate == yazio
    assert selection.dispositions[1].role is PrioritySelectionRole.FALLBACK

    missing = select_by_source_priority(
        user_id=USER_ID,
        local_date=LOCAL_DATE,
        metric_key=METRIC_KEY,
        candidates={"yazio": yazio},
        policy=_policy(_rule("google_health", GOOGLE_RULE_ID, 1), _rule("yazio", YAZIO_RULE_ID, 2)),
    )
    assert missing.dispositions[0].candidate is None
    assert missing.dispositions[0].candidate_present is False


def test_candidate_without_applicable_rule_is_diagnostic_only() -> None:
    google = _candidate(
        "google_health",
        Decimal("10"),
        source_observation_id="00000000-0000-0000-0000-000000000142",
    )
    yazio = _candidate(
        "yazio",
        Decimal("20"),
        source_observation_id="00000000-0000-0000-0000-000000000152",
    )
    selection = select_by_source_priority(
        user_id=USER_ID,
        local_date=LOCAL_DATE,
        metric_key=METRIC_KEY,
        candidates={"google_health": google, "yazio": yazio},
        policy=_policy(_rule("yazio", YAZIO_RULE_ID, 1)),
    )

    diagnostic = next(item for item in selection.dispositions if item.provider_key == "google_health")
    assert diagnostic.candidate == google
    assert diagnostic.rule_id is None
    assert diagnostic.reason_code is PriorityReasonCode.PROVIDER_NOT_CONFIGURED_FOR_METRIC
    assert selection.applied_scope is AppliedPriorityScope.WILDCARD
