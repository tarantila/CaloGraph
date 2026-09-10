from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.nutrition.enums import (
    CoverageState,
    LineageState,
    PresenceState,
    ResolutionState,
)
from app.nutrition.projection import ProjectionPersistenceStatus
from app.nutrition.projection.orchestration import rebuild_nutrition_day
from app.nutrition.resolution.contracts import MetricContribution, ProviderCandidate
from app.nutrition.resolution.providers import (
    PROVIDER_RESOLVERS,
    ProviderCandidateContractError,
    ProviderNotAvailableError,
    ProviderSourceBindings,
    ProviderSourceInstance,
    collect_provider_candidates,
)
from app.nutrition.resolution.reasons import EvidenceKind, ReasonCode
from app.source_priority.application import create_policy_with_rules
from app.source_priority.contracts import (
    PriorityPolicySnapshot,
    PriorityReasonCode,
    PriorityRuleSnapshot,
    PriorityRuleSpec,
    PrioritySelectionRole,
)
from app.source_priority.selection import select_by_source_priority

USER_ID = UUID("11111111-1111-1111-1111-111111111111")
LOCAL_DATE = date(2026, 9, 12)


def _candidate(provider_key: str) -> ProviderCandidate:
    contribution = MetricContribution(
        evidence_id=uuid4(),
        source_observation_id=uuid4(),
        metric_key="protein_g",
        value=Decimal("10"),
        unit="g",
        presence_state=PresenceState.SUPPLIED,
        resolution_state=ResolutionState.RESOLVED,
        lineage_state=LineageState.CONFIRMED,
        evidence_kind=EvidenceKind.EVENT,
    )
    return ProviderCandidate(
        provider_key=provider_key,
        user_id=USER_ID,
        local_date=LOCAL_DATE,
        metric_key="protein_g",
        value=Decimal("10"),
        unit="g",
        selected_granularity=None,
        presence_state=PresenceState.SUPPLIED,
        coverage_state=CoverageState.COMPLETE,
        resolution_state=ResolutionState.RESOLVED,
        lineage_state=LineageState.CONFIRMED,
        source_lineage=(contribution,),
        reason_code=ReasonCode.EVENT_COMPLETE_CONFIRMED,
    )


class EmptyResolver:
    def __init__(self, provider_key: str) -> None:
        self.provider_key = provider_key
        self.calls: list[UUID] = []

    def resolve_metric(self, db, *, user_id, source_instance_id, local_date, metric_key):
        self.calls.append(source_instance_id)
        return ProviderCandidate(
            provider_key=self.provider_key,
            user_id=user_id,
            local_date=local_date,
            metric_key=metric_key,
            value=None,
            unit=None,
            selected_granularity=None,
            presence_state=PresenceState.MISSING,
            coverage_state=CoverageState.UNKNOWN,
            resolution_state=ResolutionState.UNRESOLVED,
            lineage_state=LineageState.UNKNOWN,
            source_lineage=(),
            reason_code=ReasonCode.ALL_SOURCES_MISSING,
        )

    def owns_source_instance_id(self, db, *, user_id, source_instance_id):
        return True


class SpyResolver:
    def __init__(self, provider_key: str) -> None:
        self.provider_key = provider_key
        self.calls: list[UUID] = []

    def resolve_metric(self, db, *, user_id, source_instance_id, local_date, metric_key):
        self.calls.append(source_instance_id)
        return _candidate(self.provider_key)

    def owns_source_instance_id(self, db, *, user_id, source_instance_id):
        return True


def test_provider_source_bindings_are_sorted_and_immutable() -> None:
    yazio_source = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    test_source = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

    bindings = ProviderSourceBindings(
        (
            ProviderSourceInstance("yazio", yazio_source),
            ProviderSourceInstance("test_provider", test_source),
        )
    )

    assert tuple(binding.provider_key for binding in bindings) == ("test_provider", "yazio")
    assert bindings.for_provider("yazio").source_instance_id == yazio_source
    with pytest.raises(AttributeError):
        bindings.bindings += (ProviderSourceInstance("other", uuid4()),)  # type: ignore[misc]


def test_provider_source_bindings_reject_duplicate_provider_and_zero_uuid() -> None:
    source = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    with pytest.raises(ValueError, match="duplicate provider"):
        ProviderSourceBindings(
            (
                ProviderSourceInstance("yazio", source),
                ProviderSourceInstance("yazio", UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")),
            )
        )
    with pytest.raises(ValueError, match="non-zero"):
        ProviderSourceInstance("test_provider", UUID(int=0))


def test_custom_provider_binding_requires_source_ownership_contract(db, user) -> None:
    user_id = user.id
    db.rollback()
    with pytest.raises(ValueError, match="must validate ownership"):
        rebuild_nutrition_day(
            db,
            user_id=user_id,
            local_date=LOCAL_DATE,
            policy_at=datetime(2026, 9, 12, 12, tzinfo=UTC),
            provider_sources=ProviderSourceBindings(
                (ProviderSourceInstance("test_provider", uuid4()),)
            ),
            resolver_registry={"test_provider": object()},
        )


def test_collection_routes_each_provider_to_its_own_source_instance() -> None:
    yazio_source = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    test_source = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    yazio = SpyResolver("yazio")
    test_provider = SpyResolver("test_provider")

    result = collect_provider_candidates(
        object(),
        provider_sources=ProviderSourceBindings(
            (
                ProviderSourceInstance("yazio", yazio_source),
                ProviderSourceInstance("test_provider", test_source),
            )
        ),
        user_id=USER_ID,
        local_date=LOCAL_DATE,
        metric_key="protein_g",
        resolver_registry={"yazio": yazio, "test_provider": test_provider},
    )

    assert tuple(result) == ("test_provider", "yazio")
    assert yazio.calls == [yazio_source]
    assert test_provider.calls == [test_source]


def test_legacy_single_source_adapter_cannot_share_source_across_providers() -> None:
    resolver = SpyResolver("yazio")
    with pytest.raises(ValueError, match="one provider"):
        collect_provider_candidates(
            object(),
            provider_keys=("yazio", "test_provider"),
            source_instance_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            user_id=USER_ID,
            local_date=LOCAL_DATE,
            metric_key="protein_g",
            resolver_registry={"yazio": resolver, "test_provider": resolver},
        )


def test_resolver_candidate_provider_scope_remains_fail_closed() -> None:
    resolver = SpyResolver("wrong_provider")
    with pytest.raises(ProviderCandidateContractError, match="provider_key"):
        collect_provider_candidates(
            object(),
            provider_sources=ProviderSourceBindings(
                (ProviderSourceInstance("test_provider", uuid4()),)
            ),
            user_id=USER_ID,
            local_date=LOCAL_DATE,
            metric_key="protein_g",
            resolver_registry={"test_provider": resolver},
        )


def test_unregistered_provider_remains_explicitly_unavailable() -> None:
    with pytest.raises(ProviderNotAvailableError):
        collect_provider_candidates(
            object(),
            provider_sources=ProviderSourceBindings(
                (ProviderSourceInstance("not_registered", uuid4()),)
            ),
            user_id=USER_ID,
            local_date=LOCAL_DATE,
            metric_key="protein_g",
        )


def test_priority_fallback_keeps_b4_selection_semantics() -> None:
    empty = EmptyResolver("test_provider").resolve_metric(
        object(),
        user_id=USER_ID,
        source_instance_id=uuid4(),
        local_date=LOCAL_DATE,
        metric_key="protein_g",
    )
    policy = PriorityPolicySnapshot(
        policy_id=uuid4(),
        user_id=USER_ID,
        version=1,
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        rules=(
            PriorityRuleSnapshot(
                rule_id=uuid4(),
                data_area="nutrition",
                metric_key=None,
                provider_key="test_provider",
                priority_rank=1,
            ),
            PriorityRuleSnapshot(
                rule_id=uuid4(),
                data_area="nutrition",
                metric_key=None,
                provider_key="yazio",
                priority_rank=2,
            ),
        ),
    )

    selection = select_by_source_priority(
        user_id=USER_ID,
        local_date=LOCAL_DATE,
        metric_key="protein_g",
        candidates={"test_provider": empty, "yazio": _candidate("yazio")},
        policy=policy,
    )

    assert selection.selected_candidate is not None
    assert selection.selected_candidate.provider_key == "yazio"
    assert selection.selected_role is PrioritySelectionRole.FALLBACK
    assert selection.reason_code is PriorityReasonCode.WILDCARD_PRIORITY


def test_b6_watermark_contains_only_decision_relevant_provider_sources(db, user) -> None:
    assert "test_provider" not in PROVIDER_RESOLVERS
    test_source = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    unused_source = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    changed_unused_source = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
    changed_test_source = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
    test_resolver = EmptyResolver("test_provider")
    unused_resolver = EmptyResolver("unused_provider")
    resolver_registry = {
        "test_provider": test_resolver,
        "unused_provider": unused_resolver,
    }
    create_policy_with_rules(
        db,
        user.id,
        1,
        datetime(2026, 1, 1, tzinfo=UTC),
        (PriorityRuleSpec("nutrition", None, "test_provider", 1),),
    )
    db.commit()

    first = rebuild_nutrition_day(
        db,
        user_id=user.id,
        local_date=LOCAL_DATE,
        policy_at=datetime(2026, 9, 12, 12, tzinfo=UTC),
        provider_sources=ProviderSourceBindings(
            (
                ProviderSourceInstance("test_provider", test_source),
                ProviderSourceInstance("unused_provider", unused_source),
            )
        ),
        resolver_registry=resolver_registry,
    )
    second = rebuild_nutrition_day(
        db,
        user_id=user.id,
        local_date=LOCAL_DATE,
        policy_at=datetime(2026, 9, 12, 12, tzinfo=UTC),
        provider_sources=ProviderSourceBindings(
            (
                ProviderSourceInstance("test_provider", test_source),
                ProviderSourceInstance("unused_provider", unused_source),
            )
        ),
        resolver_registry=resolver_registry,
    )
    third = rebuild_nutrition_day(
        db,
        user_id=user.id,
        local_date=LOCAL_DATE,
        policy_at=datetime(2026, 9, 12, 12, tzinfo=UTC),
        provider_sources=ProviderSourceBindings(
            (
                ProviderSourceInstance("test_provider", test_source),
                ProviderSourceInstance("unused_provider", changed_unused_source),
            )
        ),
        resolver_registry=resolver_registry,
    )
    fourth = rebuild_nutrition_day(
        db,
        user_id=user.id,
        local_date=LOCAL_DATE,
        policy_at=datetime(2026, 9, 12, 12, tzinfo=UTC),
        provider_sources=ProviderSourceBindings(
            (
                ProviderSourceInstance("test_provider", changed_test_source),
                ProviderSourceInstance("unused_provider", changed_unused_source),
            )
        ),
        resolver_registry=resolver_registry,
    )

    assert first.status is ProjectionPersistenceStatus.CREATED
    assert second.status is ProjectionPersistenceStatus.UNCHANGED
    assert third.status is ProjectionPersistenceStatus.UNCHANGED
    assert fourth.status is ProjectionPersistenceStatus.CREATED
    assert fourth.input_watermark != first.input_watermark
    assert test_resolver.calls == [test_source] * 21 + [changed_test_source] * 7
    assert unused_resolver.calls == [unused_source] * 14 + [changed_unused_source] * 14
