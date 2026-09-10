from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.models import User
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
)
from app.source_priority.application import (
    create_policy_with_rules,
    get_effective_policy_snapshot,
    select_nutrition_candidate,
)
from app.source_priority.contracts import (
    AppliedPriorityScope,
    PriorityContractError,
    PriorityPolicySnapshot,
    PriorityReasonCode,
    PriorityRuleSnapshot,
    PriorityRuleSpec,
    PrioritySelectionRole,
)
from app.source_priority.models import SourcePriorityPolicy, SourcePriorityRule
from app.source_priority.selection import select_by_source_priority

USER_ID = UUID("11111111-1111-1111-1111-111111111111")
OTHER_USER_ID = UUID("22222222-2222-2222-2222-222222222222")
LOCAL_DATE = date(2026, 9, 10)
METRIC_KEY = "protein_g"
FIRST_EFFECTIVE = datetime(2026, 9, 1, 12, tzinfo=UTC)
SECOND_EFFECTIVE = datetime(2026, 9, 3, 12, tzinfo=UTC)


def _rule(
    provider_key: str,
    priority_rank: int,
    *,
    metric_key: str | None = None,
    data_area: str = "nutrition",
    rule_id: UUID | None = None,
) -> PriorityRuleSnapshot:
    return PriorityRuleSnapshot(
        rule_id=rule_id or uuid4(),
        data_area=data_area,
        metric_key=metric_key,
        provider_key=provider_key,
        priority_rank=priority_rank,
    )


def _policy(
    *rules: PriorityRuleSnapshot,
    user_id: UUID = USER_ID,
    version: int = 1,
    effective_from: datetime = FIRST_EFFECTIVE,
    policy_id: UUID | None = None,
) -> PriorityPolicySnapshot:
    return PriorityPolicySnapshot(
        policy_id=policy_id or uuid4(),
        user_id=user_id,
        version=version,
        effective_from=effective_from,
        rules=rules,
    )


def _candidate(
    provider_key: str,
    *,
    user_id: UUID = USER_ID,
    local_date: date = LOCAL_DATE,
    metric_key: str = METRIC_KEY,
    value: Decimal | None = Decimal("10"),
    presence_state: PresenceState = PresenceState.SUPPLIED,
    coverage_state: CoverageState = CoverageState.COMPLETE,
    resolution_state: ResolutionState = ResolutionState.RESOLVED,
    lineage_state: LineageState = LineageState.CONFIRMED,
) -> ProviderCandidate:
    if value is None:
        return ProviderCandidate(
            provider_key=provider_key,
            user_id=user_id,
            local_date=local_date,
            metric_key=metric_key,
            value=None,
            unit=None,
            selected_granularity=None,
            presence_state=PresenceState.MISSING,
            coverage_state=coverage_state,
            resolution_state=resolution_state,
            lineage_state=lineage_state,
            source_lineage=(),
            reason_code=ReasonCode.ALL_SOURCES_MISSING,
        )

    contribution = MetricContribution(
        evidence_id=uuid4(),
        source_observation_id=uuid4(),
        metric_key=metric_key,
        value=value,
        unit="g",
        presence_state=presence_state,
        resolution_state=resolution_state,
        lineage_state=lineage_state,
        evidence_kind=EvidenceKind.EVENT,
    )
    source_lineage = (contribution,)
    selected_granularity = ProjectionGranularity.EVENT
    candidate_resolution = resolution_state
    if coverage_state is CoverageState.PARTIAL:
        source_lineage = (
            contribution,
            MetricContribution(
                evidence_id=uuid4(),
                source_observation_id=uuid4(),
                metric_key=metric_key,
                value=None,
                unit="g",
                presence_state=PresenceState.MISSING,
                resolution_state=ResolutionState.UNRESOLVED,
                lineage_state=LineageState.UNKNOWN,
                evidence_kind=EvidenceKind.EVENT,
            ),
        )
        selected_granularity = ProjectionGranularity.PARTIAL_EVENT
        candidate_resolution = ResolutionState.UNRESOLVED
    return ProviderCandidate(
        provider_key=provider_key,
        user_id=user_id,
        local_date=local_date,
        metric_key=metric_key,
        value=value,
        unit="g",
        selected_granularity=selected_granularity,
        presence_state=presence_state,
        coverage_state=coverage_state,
        resolution_state=candidate_resolution,
        lineage_state=lineage_state,
        source_lineage=source_lineage,
        reason_code=(
            ReasonCode.PARTIAL_EVENTS_NO_SUMMARY
            if coverage_state is CoverageState.PARTIAL
            else ReasonCode.EVENT_COMPLETE_CONFIRMED
        ),
    )


def _other_user(db, user: User) -> User:
    other = User(
        username="source-priority-b4-other",
        password_hash=user.password_hash,
        timezone="UTC",
    )
    db.add(other)
    db.flush()
    return other


def test_priority_policy_snapshot_is_frozen_and_normalizes_utc() -> None:
    local_effective = datetime(2026, 9, 1, 14, tzinfo=timezone(timedelta(hours=2)))
    rule = _rule("yazio", 1)
    snapshot = _policy(rule, effective_from=local_effective)

    assert snapshot.effective_from == datetime(2026, 9, 1, 12, tzinfo=UTC)
    assert snapshot.rules == (rule,)
    with pytest.raises(AttributeError):
        snapshot.version = 2  # type: ignore[misc]
    with pytest.raises(AttributeError):
        snapshot.rules += (rule,)  # type: ignore[misc]


    with pytest.raises(ValueError, match=r"timezone|aware|naive"):
        _policy(_rule("yazio", 1), effective_from=datetime(2026, 9, 1, 12))


def test_priority_policy_snapshot_rejects_duplicate_rank_and_provider() -> None:
    with pytest.raises(PriorityContractError, match="priority_rank_conflict"):
        _policy(_rule("google_health", 1), _rule("yazio", 1))
    with pytest.raises(PriorityContractError, match="priority_rank_conflict"):
        _policy(_rule("yazio", 1), _rule("yazio", 2))


def test_create_policy_with_rules_creates_complete_immutable_version(db, user) -> None:
    rules = (
        PriorityRuleSpec("nutrition", None, "google_health", 1),
        PriorityRuleSpec("nutrition", None, "yazio", 2),
        PriorityRuleSpec("nutrition", METRIC_KEY, "yazio", 1),
    )

    snapshot = create_policy_with_rules(db, user.id, 1, FIRST_EFFECTIVE, rules)

    assert snapshot.user_id == user.id
    assert snapshot.version == 1
    assert len(snapshot.rules) == 3
    assert db.get(SourcePriorityPolicy, snapshot.policy_id) is not None
    assert len(
        db.scalars(
            select(SourcePriorityRule).where(SourcePriorityRule.policy_id == snapshot.policy_id)
        ).all()
    ) == 3


def test_create_policy_with_rules_does_not_commit_and_rollback_removes_full_set(db, user) -> None:
    create_policy_with_rules(
        db,
        user.id,
        1,
        FIRST_EFFECTIVE,
        (PriorityRuleSpec("nutrition", None, "yazio", 1),),
    )
    db.rollback()
    rows = db.scalars(select(SourcePriorityPolicy)).all()

    assert rows == []


def test_create_policy_with_rules_rejects_duplicate_provider_before_write(db, user) -> None:
    with pytest.raises(PriorityContractError, match="priority_rank_conflict"):
        create_policy_with_rules(
            db,
            user.id,
            1,
            FIRST_EFFECTIVE,
            (
                PriorityRuleSpec("nutrition", None, "yazio", 1),
                PriorityRuleSpec("nutrition", None, "yazio", 2),
            ),
        )

    assert db.scalars(select(SourcePriorityPolicy)).all() == []
    assert db.scalars(select(SourcePriorityRule)).all() == []


def test_create_policy_with_rules_preserves_v1_when_creating_v2(db, user) -> None:
    v1 = create_policy_with_rules(
        db,
        user.id,
        1,
        FIRST_EFFECTIVE,
        (PriorityRuleSpec("nutrition", None, "yazio", 1),),
    )
    db.commit()

    v2 = create_policy_with_rules(
        db,
        user.id,
        2,
        SECOND_EFFECTIVE,
        (PriorityRuleSpec("nutrition", None, "google_health", 1),),
    )

    assert v1.policy_id != v2.policy_id
    assert v1.rules[0].provider_key == "yazio"
    assert v2.rules[0].provider_key == "google_health"
    assert [item.provider_key for item in db.scalars(select(SourcePriorityRule)).all()] == [
        "yazio",
        "google_health",
    ]


def test_effective_policy_snapshot_uses_explicit_policy_at_and_full_rules(db, user) -> None:
    v1 = create_policy_with_rules(
        db,
        user.id,
        1,
        FIRST_EFFECTIVE,
        (
            PriorityRuleSpec("nutrition", None, "google_health", 1),
            PriorityRuleSpec("nutrition", None, "yazio", 2),
        ),
    )
    db.commit()
    v2 = create_policy_with_rules(
        db,
        user.id,
        2,
        SECOND_EFFECTIVE,
        (PriorityRuleSpec("nutrition", None, "yazio", 1),),
    )
    db.commit()

    before_v2 = get_effective_policy_snapshot(
        db, user.id, datetime(2026, 9, 2, 12, tzinfo=UTC)
    )
    after_v2 = get_effective_policy_snapshot(
        db,
        user.id,
        datetime(2026, 9, 4, 12, tzinfo=UTC),
    )

    assert before_v2 is not None and before_v2.policy_id == v1.policy_id
    assert [item.provider_key for item in before_v2.rules] == ["google_health", "yazio"]
    assert after_v2 is not None and after_v2.policy_id == v2.policy_id
    assert [item.provider_key for item in after_v2.rules] == ["yazio"]


def test_effective_policy_snapshot_rejects_naive_policy_at_and_is_user_scoped(db, user) -> None:
    other = _other_user(db, user)
    create_policy_with_rules(
        db,
        other.id,
        1,
        FIRST_EFFECTIVE,
        (PriorityRuleSpec("nutrition", None, "yazio", 1),),
    )
    db.commit()

    with pytest.raises(ValueError, match=r"timezone|aware|naive"):
        get_effective_policy_snapshot(db, user.id, datetime(2026, 9, 2, 12))
    assert get_effective_policy_snapshot(db, user.id, datetime(2026, 9, 2, 12, tzinfo=UTC)) is None


def test_wildcard_priority_selects_highest_eligible_and_rejects_lower_candidate() -> None:
    selection = select_by_source_priority(
        user_id=USER_ID,
        local_date=LOCAL_DATE,
        metric_key=METRIC_KEY,
        candidates={
            "yazio": _candidate("yazio", value=Decimal("20")),
            "google_health": _candidate("google_health", value=Decimal("10")),
        },
        policy=_policy(_rule("google_health", 1), _rule("yazio", 2)),
    )

    assert selection.selected_candidate is not None
    assert selection.selected_candidate.provider_key == "google_health"
    assert selection.selected_role is PrioritySelectionRole.SELECTED
    assert selection.reason_code is PriorityReasonCode.WILDCARD_PRIORITY
    assert selection.dispositions[1].role is PrioritySelectionRole.REJECTED
    assert selection.dispositions[1].reason_code is PriorityReasonCode.LOWER_PRIORITY_PROVIDER


def test_missing_higher_provider_yields_fallback_without_fake_candidate() -> None:
    selection = select_by_source_priority(
        user_id=USER_ID,
        local_date=LOCAL_DATE,
        metric_key=METRIC_KEY,
        candidates={"yazio": _candidate("yazio")},
        policy=_policy(_rule("google_health", 1), _rule("yazio", 2)),
    )

    assert selection.selected_candidate is not None
    assert selection.selected_candidate.provider_key == "yazio"
    assert selection.selected_role is PrioritySelectionRole.FALLBACK
    assert selection.dispositions[0].candidate_present is False
    assert selection.dispositions[0].reason_code is PriorityReasonCode.PROVIDER_NOT_AVAILABLE


def test_metric_specific_rules_replace_wildcard_rules_completely() -> None:
    selection = select_by_source_priority(
        user_id=USER_ID,
        local_date=LOCAL_DATE,
        metric_key=METRIC_KEY,
        candidates={
            "google_health": _candidate("google_health"),
            "yazio": _candidate("yazio"),
        },
        policy=_policy(
            _rule("google_health", 1),
            _rule("yazio", 2),
            _rule("yazio", 1, metric_key=METRIC_KEY),
        ),
    )

    assert selection.applied_scope is AppliedPriorityScope.METRIC
    assert selection.selected_candidate is not None
    assert selection.selected_candidate.provider_key == "yazio"
    assert [item.provider_key for item in selection.dispositions] == ["yazio", "google_health"]
    assert selection.dispositions[1].reason_code is PriorityReasonCode.PROVIDER_NOT_CONFIGURED_FOR_METRIC


def test_metric_specific_scope_does_not_fallback_to_wildcard_candidate() -> None:
    selection = select_by_source_priority(
        user_id=USER_ID,
        local_date=LOCAL_DATE,
        metric_key=METRIC_KEY,
        candidates={"google_health": _candidate("google_health")},
        policy=_policy(
            _rule("google_health", 1),
            _rule("yazio", 1, metric_key=METRIC_KEY),
        ),
    )

    assert selection.selected_candidate is None
    assert selection.reason_code is PriorityReasonCode.NO_ELIGIBLE_PROVIDER
    assert selection.dispositions[0].provider_key == "yazio"
    assert selection.dispositions[0].candidate_present is False
    assert selection.dispositions[1].provider_key == "google_health"
    assert selection.dispositions[1].reason_code is PriorityReasonCode.PROVIDER_NOT_CONFIGURED_FOR_METRIC

def test_higher_uncertain_candidate_beats_lower_confirmed_candidate() -> None:
    selection = select_by_source_priority(
        user_id=USER_ID,
        local_date=LOCAL_DATE,
        metric_key=METRIC_KEY,
        candidates={
            "google_health": _candidate("google_health", lineage_state=LineageState.UNCERTAIN),
            "yazio": _candidate("yazio", lineage_state=LineageState.CONFIRMED),
        },
        policy=_policy(_rule("google_health", 1), _rule("yazio", 2)),
    )

    assert selection.selected_candidate is not None
    assert selection.selected_candidate.provider_key == "google_health"


def test_higher_partial_event_beats_lower_complete_candidate() -> None:
    selection = select_by_source_priority(
        user_id=USER_ID,
        local_date=LOCAL_DATE,
        metric_key=METRIC_KEY,
        candidates={
            "google_health": _candidate(
                "google_health",
                coverage_state=CoverageState.PARTIAL,
            ),
            "yazio": _candidate("yazio"),
        },
        policy=_policy(_rule("google_health", 1), _rule("yazio", 2)),
    )

    assert selection.selected_candidate is not None
    assert selection.selected_candidate.provider_key == "google_health"


def test_explicit_zero_candidate_beats_lower_positive_candidate() -> None:
    selection = select_by_source_priority(
        user_id=USER_ID,
        local_date=LOCAL_DATE,
        metric_key=METRIC_KEY,
        candidates={
            "google_health": _candidate(
                "google_health",
                value=Decimal("0"),
                presence_state=PresenceState.EXPLICIT_ZERO,
            ),
            "yazio": _candidate("yazio", value=Decimal("20")),
        },
        policy=_policy(_rule("google_health", 1), _rule("yazio", 2)),
    )

    assert selection.selected_candidate is not None
    assert selection.selected_candidate.provider_key == "google_health"
    assert selection.selected_candidate.value == Decimal("0")


def test_missing_policy_has_no_default_provider() -> None:
    selection = select_by_source_priority(
        user_id=USER_ID,
        local_date=LOCAL_DATE,
        metric_key=METRIC_KEY,
        candidates={"yazio": _candidate("yazio")},
        policy=None,
    )

    assert selection.selected_candidate is None
    assert selection.selected_role is None
    assert selection.reason_code is PriorityReasonCode.PRIORITY_POLICY_MISSING
    assert selection.applied_scope is AppliedPriorityScope.NONE


def test_policy_without_applicable_rules_is_not_default_priority() -> None:
    selection = select_by_source_priority(
        user_id=USER_ID,
        local_date=LOCAL_DATE,
        metric_key=METRIC_KEY,
        candidates={"yazio": _candidate("yazio")},
        policy=_policy(_rule("yazio", 1, metric_key="fat_g")),
    )

    assert selection.selected_candidate is None
    assert selection.reason_code is PriorityReasonCode.NO_APPLICABLE_PRIORITY_RULE
    assert selection.applied_scope is AppliedPriorityScope.NONE


def test_rules_with_only_ineligible_candidates_have_no_winner() -> None:
    selection = select_by_source_priority(
        user_id=USER_ID,
        local_date=LOCAL_DATE,
        metric_key=METRIC_KEY,
        candidates={"google_health": _candidate("google_health", value=None)},
        policy=_policy(_rule("google_health", 1)),
    )

    assert selection.selected_candidate is None
    assert selection.reason_code is PriorityReasonCode.NO_ELIGIBLE_PROVIDER
    assert selection.dispositions[0].eligible is False
    assert selection.dispositions[0].role is PrioritySelectionRole.DIAGNOSTIC


def test_candidate_without_rule_is_diagnostic_and_not_contributing() -> None:
    selection = select_by_source_priority(
        user_id=USER_ID,
        local_date=LOCAL_DATE,
        metric_key=METRIC_KEY,
        candidates={"google_health": _candidate("google_health")},
        policy=_policy(_rule("yazio", 1)),
    )

    assert selection.selected_candidate is None
    assert selection.dispositions[0].provider_key == "yazio"
    assert selection.dispositions[0].reason_code is PriorityReasonCode.PROVIDER_NOT_AVAILABLE
    assert selection.dispositions[1].provider_key == "google_health"
    assert selection.dispositions[1].reason_code is PriorityReasonCode.PROVIDER_NOT_CONFIGURED_FOR_METRIC
    assert selection.dispositions[1].role is PrioritySelectionRole.DIAGNOSTIC


def test_candidate_scope_mismatch_fails_closed() -> None:
    with pytest.raises(PriorityContractError, match="user_id"):
        select_by_source_priority(
            user_id=USER_ID,
            local_date=LOCAL_DATE,
            metric_key=METRIC_KEY,
            candidates={"yazio": _candidate("yazio", user_id=OTHER_USER_ID)},
            policy=_policy(_rule("yazio", 1)),
        )

    with pytest.raises(PriorityContractError, match="local_date"):
        select_by_source_priority(
            user_id=USER_ID,
            local_date=LOCAL_DATE,
            metric_key=METRIC_KEY,
            candidates={"yazio": _candidate("yazio", local_date=date(2026, 9, 11))},
            policy=_policy(_rule("yazio", 1)),
        )

    with pytest.raises(PriorityContractError, match="metric_key"):
        select_by_source_priority(
            user_id=USER_ID,
            local_date=LOCAL_DATE,
            metric_key=METRIC_KEY,
            candidates={"yazio": _candidate("yazio", metric_key="fat_g")},
            policy=_policy(_rule("yazio", 1)),
        )


def test_duplicate_provider_and_rank_in_pure_policy_fail_closed() -> None:
    with pytest.raises(PriorityContractError, match="priority_rank_conflict"):
        _policy(_rule("yazio", 1), _rule("google_health", 1))
    with pytest.raises(PriorityContractError, match="priority_rank_conflict"):
        _policy(_rule("yazio", 1), _rule("yazio", 2))


def test_selection_is_independent_of_rule_and_candidate_input_order() -> None:
    first_rule_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    second_rule_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    first = _candidate("google_health")
    second = _candidate("yazio")
    policy_a = _policy(
        _rule("google_health", 1, rule_id=first_rule_id),
        _rule("yazio", 2, rule_id=second_rule_id),
    )
    policy_b = _policy(
        _rule("yazio", 2, rule_id=second_rule_id),
        _rule("google_health", 1, rule_id=first_rule_id),
        policy_id=policy_a.policy_id,
    )

    selection_a = select_by_source_priority(
        user_id=USER_ID,
        local_date=LOCAL_DATE,
        metric_key=METRIC_KEY,
        candidates={"google_health": first, "yazio": second},
        policy=policy_a,
    )
    selection_b = select_by_source_priority(
        user_id=USER_ID,
        local_date=LOCAL_DATE,
        metric_key=METRIC_KEY,
        candidates={"yazio": second, "google_health": first},
        policy=policy_b,
    )

    assert selection_a == selection_b


def test_db_selection_requires_explicit_policy_at_and_does_not_use_local_date(db, user) -> None:
    create_policy_with_rules(
        db,
        user.id,
        1,
        FIRST_EFFECTIVE,
        (PriorityRuleSpec("nutrition", None, "yazio", 1),),
    )
    db.commit()

    result = select_nutrition_candidate(
        db,
        user_id=user.id,
        local_date=date(1990, 1, 1),
        metric_key=METRIC_KEY,
        candidates={
            "yazio": _candidate(
                "yazio",
                user_id=user.id,
                local_date=date(1990, 1, 1),
            )
        },
        policy_at=datetime(2026, 9, 2, 12, tzinfo=UTC),
    )

    assert result.selected_candidate is not None
    assert result.selected_candidate.provider_key == "yazio"
