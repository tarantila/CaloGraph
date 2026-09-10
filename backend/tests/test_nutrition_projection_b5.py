from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.models import (
    NutritionDailyProjection,
    NutritionDailyProjectionFact,
    NutritionDailyProjectionLineage,
    NutritionIngestionRun,
    NutritionProjectionHead,
    NutritionSourceObservation,
    User,
)
from app.nutrition.enums import (
    CoverageState,
    LineageState,
    ObservationKind,
    PresenceState,
    ProjectionGranularity,
    ResolutionState,
)
from app.nutrition.projection import (
    CANONICAL_METRIC_KEYS,
    DailyProjectionBuildInput,
    ProjectionInputManifest,
    ProjectionPersistenceStatus,
    SourceObservationToken,
    persist_daily_projection,
)
from app.nutrition.resolution import (
    EvidenceKind,
    MetricContribution,
    ProviderCandidate,
    ReasonCode,
)
from app.source_priority.application import create_policy_with_rules
from app.source_priority.contracts import (
    AppliedPriorityScope,
    PriorityPolicySnapshot,
    PriorityReasonCode,
    PriorityRuleSpec,
    PrioritySelection,
    PrioritySelectionRole,
    ProviderDisposition,
)

USER_ID = UUID("11111111-1111-1111-1111-111111111111")
LOCAL_DATE = date(2026, 9, 11)
ALGORITHM = "nutrition-daily-v1"
METRIC_REGISTRY = "nutrition-metrics-v1"
WATERMARK_FORMAT = "nutrition-watermark-v2"


def _source_observation(
    db,
    user: User,
    *,
    provider_key: str = "yazio",
    source_observation_id: UUID | None = None,
    fingerprint: str = "a" * 64,
    local_date: date = LOCAL_DATE,
) -> NutritionSourceObservation:
    run = NutritionIngestionRun(
        user_id=user.id,
        provider_key=provider_key,
        source_instance_id=uuid4(),
        status="completed",
        coverage_state=CoverageState.COMPLETE.value,
    )
    db.add(run)
    db.flush()
    observation = NutritionSourceObservation(
        id=source_observation_id or uuid4(),
        user_id=user.id,
        ingestion_run_id=run.id,
        provider_key=provider_key,
        source_instance_id=run.source_instance_id,
        observation_kind=ObservationKind.DAILY_SUMMARY.value,
        source_namespace="b5",
        source_revision=1,
        observation_fingerprint=fingerprint,
        local_date=local_date,
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
        lineage_state=LineageState.CONFIRMED.value,
    )
    db.add(observation)
    db.flush()
    return observation
def _source_token(observation: NutritionSourceObservation) -> SourceObservationToken:
    return SourceObservationToken(
        source_observation_id=observation.id,
        source_revision=observation.source_revision,
        observation_fingerprint=observation.observation_fingerprint,
        payload_hash=observation.payload_hash,
    )




def _candidate(
    user: User,
    metric_key: str,
    observation: NutritionSourceObservation,
    *,
    value: Decimal | None = Decimal("10"),
    provider_key: str = "yazio",
    evidence_id: UUID | None = None,
    granularity: ProjectionGranularity = ProjectionGranularity.SUMMARY,
) -> ProviderCandidate:
    if value is None:
        presence = PresenceState.MISSING
        coverage = CoverageState.UNKNOWN
        resolution = ResolutionState.UNRESOLVED
        lineage = LineageState.UNKNOWN
        evidence_value = None
        evidence_kind = EvidenceKind.SOURCE_OBSERVATION
        reason = ReasonCode.ALL_SOURCES_MISSING
    else:
        presence = PresenceState.EXPLICIT_ZERO if value == Decimal("0") else PresenceState.SUPPLIED
        coverage = CoverageState.COMPLETE
        resolution = ResolutionState.RESOLVED
        lineage = LineageState.CONFIRMED
        evidence_value = value
        evidence_kind = EvidenceKind.FIELD_OBSERVATION
        reason = ReasonCode.SUMMARY_ONLY
    contribution = MetricContribution(
        evidence_id=evidence_id or uuid4(),
        source_observation_id=observation.id,
        metric_key=metric_key,
        value=evidence_value,
        unit="kcal" if metric_key == "dietary_energy_kcal" else "g",
        presence_state=presence,
        resolution_state=resolution,
        lineage_state=lineage,
        evidence_kind=evidence_kind,
        reason_code=reason,
    )
    return ProviderCandidate(
        provider_key=provider_key,
        user_id=user.id,
        local_date=LOCAL_DATE,
        metric_key=metric_key,
        value=value,
        unit=contribution.unit,
        selected_granularity=granularity if value is not None else None,
        presence_state=presence,
        coverage_state=coverage,
        resolution_state=resolution,
        lineage_state=lineage,
        source_lineage=(contribution,),
        reason_code=reason,
    )


def _policy(
    db,
    user: User,
    *,
    providers: tuple[tuple[str, str | None, int], ...] = (("yazio", None, 1),),
    version: int = 1,
    effective_from: datetime = datetime(2026, 1, 1, tzinfo=UTC),
) -> PriorityPolicySnapshot:
    return create_policy_with_rules(
        db,
        user.id,
        version,
        effective_from,
        tuple(
            PriorityRuleSpec(
                data_area="nutrition",
                metric_key=metric_key,
                provider_key=provider_key,
                priority_rank=rank,
            )
            for provider_key, metric_key, rank in providers
        ),
    )


def _selection(
    user: User,
    policy: PriorityPolicySnapshot | None,
    metric_key: str,
    *,
    candidate: ProviderCandidate | None,
    provider_key: str = "yazio",
    rule_id: UUID | None = None,
    rank: int | None = 1,
    role: PrioritySelectionRole | None = None,
    reason: PriorityReasonCode = PriorityReasonCode.METRIC_SPECIFIC_PRIORITY,
    dispositions: tuple[ProviderDisposition, ...] | None = None,
) -> PrioritySelection:
    if policy is None:
        rule_id = None
        if dispositions is None:
            dispositions = ()
    else:
        if rule_id is None:
            rule_id = next(
                (rule.rule_id for rule in policy.rules if rule.provider_key == provider_key),
                None,
            )
        if dispositions is None:
            disposition_role = role
            if disposition_role is None and rule_id is not None:
                disposition_role = PrioritySelectionRole.DIAGNOSTIC
            dispositions = (
                ProviderDisposition(
                    provider_key=provider_key,
                    rule_id=rule_id,
                    priority_rank=rank,
                    candidate_present=candidate is not None,
                    eligible=True if candidate is not None else None,
                    role=disposition_role,
                    reason_code=reason,
                    candidate=candidate,
                ),
            )
    return PrioritySelection(
        user_id=user.id,
        local_date=LOCAL_DATE,
        metric_key=metric_key,
        policy=policy,
        applied_scope=AppliedPriorityScope.METRIC if rule_id is not None else AppliedPriorityScope.NONE,
        selected_candidate=candidate if role in {PrioritySelectionRole.SELECTED, PrioritySelectionRole.FALLBACK} else None,
        selected_role=role if candidate is not None else None,
        reason_code=reason,
        dispositions=dispositions,
    )


def _input(
    user: User,
    selections: dict[str, PrioritySelection],
    policy: PriorityPolicySnapshot | None,
    *,
    tokens: tuple[object, ...] = (),
) -> DailyProjectionBuildInput:
    rules = () if policy is None else policy.rules
    return DailyProjectionBuildInput(
        user_id=user.id,
        local_date=LOCAL_DATE,
        selections=tuple(selections[key] for key in reversed(CANONICAL_METRIC_KEYS)),
        input_manifest=ProjectionInputManifest(
            user_id=user.id,
            local_date=LOCAL_DATE,
            projection_algorithm_version=ALGORITHM,
            metric_registry_version=METRIC_REGISTRY,
            watermark_format_version=WATERMARK_FORMAT,
            policy=policy,
            relevant_rules=rules,
            technical_evidence=tokens,
        ),
    )


def _all_no_winner(user: User, policy: PriorityPolicySnapshot | None) -> DailyProjectionBuildInput:
    selections = {
        metric: _selection(
            user,
            policy,
            metric,
            candidate=None,
            role=None,
            reason=(
                PriorityReasonCode.PRIORITY_POLICY_MISSING
                if policy is None
                else PriorityReasonCode.NO_ELIGIBLE_PROVIDER
            ),
        )
        for metric in CANONICAL_METRIC_KEYS
    }
    return _input(user, selections, policy)


def _all_values(db, user: User, policy: PriorityPolicySnapshot, *, value: Decimal = Decimal("10")):
    selections: dict[str, PrioritySelection] = {}
    tokens = []
    for metric in CANONICAL_METRIC_KEYS:
        observation = _source_observation(db, user)
        candidate = _candidate(user, metric, observation, value=value)
        selections[metric] = _selection(
            user,
            policy,
            metric,
            candidate=candidate,

            role=PrioritySelectionRole.SELECTED,
        )
        tokens.append(
            SourceObservationToken(
                source_observation_id=observation.id,
                source_revision=observation.source_revision,
                observation_fingerprint=observation.observation_fingerprint,
            )
        )
    return _input(user, selections, policy, tokens=tuple(tokens))
def _input_with_candidate(
    user: User,
    policy: PriorityPolicySnapshot,
    candidate: ProviderCandidate,
    *,
    tokens: tuple[SourceObservationToken, ...],
) -> DailyProjectionBuildInput:
    selections = {
        metric: (
            _selection(user, policy, metric, candidate=candidate, role=PrioritySelectionRole.SELECTED)
            if metric == candidate.metric_key
            else _selection(
                user,
                policy,
                metric,
                candidate=None,
                role=None,
                reason=PriorityReasonCode.NO_ELIGIBLE_PROVIDER,
            )
        )
        for metric in CANONICAL_METRIC_KEYS
    }
    return _input(user, selections, policy, tokens=tokens)


def test_first_projection_persists_version_one_seven_facts_and_head(db, user):
    policy = _policy(db, user)
    build_input = _all_values(db, user, policy)

    result = persist_daily_projection(db, build_input=build_input)
    db.commit()

    assert result.created is True
    assert result.status is ProjectionPersistenceStatus.CREATED
    assert result.projection_version == 1
    projection = db.get(NutritionDailyProjection, result.projection_id)
    assert projection is not None
    assert projection.projection_status == "ready"
    assert db.scalar(
        select(NutritionProjectionHead.current_projection_id).where(
            NutritionProjectionHead.user_id == user.id,
            NutritionProjectionHead.local_date == LOCAL_DATE,
        )
    ) == projection.id
    assert db.scalar(
        select(NutritionDailyProjectionFact.projection_id)
        .where(NutritionDailyProjectionFact.projection_id == projection.id)
    ) is not None
    assert db.scalar(
        select(NutritionDailyProjectionFact.metric_key)
        .where(NutritionDailyProjectionFact.projection_id == projection.id)
    ) is not None
    assert db.query(NutritionDailyProjectionFact).filter_by(projection_id=projection.id).count() == 7


def test_identical_input_is_noop_after_lock(db, user):
    policy = _policy(db, user)
    build_input = _all_values(db, user, policy)
    first = persist_daily_projection(db, build_input=build_input)
    db.commit()

    second = persist_daily_projection(db, build_input=build_input)
    db.commit()

    assert first.created is True
    assert second.created is False
    assert second.status is ProjectionPersistenceStatus.UNCHANGED
    assert second.projection_id is None
    assert db.query(NutritionDailyProjection).count() == 1
    assert db.query(NutritionProjectionHead).count() == 1


def test_changed_watermark_creates_v2_and_leaves_v1_graph_immutable(db, user):
    policy = _policy(db, user)
    first_input = _all_values(db, user, policy, value=Decimal("10"))
    first = persist_daily_projection(db, build_input=first_input)
    db.commit()
    first_snapshot = (
        db.get(NutritionDailyProjection, first.projection_id).input_watermark,
        tuple(
            (fact.metric_key, fact.value, fact.diagnostic_metadata)
            for fact in db.scalars(
                select(NutritionDailyProjectionFact).where(
                    NutritionDailyProjectionFact.projection_id == first.projection_id
                )
            )
        ),
    )

    second_input = _all_values(db, user, policy, value=Decimal("11"))
    second = persist_daily_projection(db, build_input=second_input)
    db.commit()

    assert second.projection_version == 2
    assert db.get(NutritionDailyProjection, first.projection_id).input_watermark == first_snapshot[0]
    assert tuple(
        (fact.metric_key, fact.value, fact.diagnostic_metadata)
        for fact in db.scalars(
            select(NutritionDailyProjectionFact).where(
                NutritionDailyProjectionFact.projection_id == first.projection_id
            )
        )
    ) == first_snapshot[1]
    assert db.get(NutritionProjectionHead, (user.id, LOCAL_DATE)).current_projection_id == second.projection_id


def test_policy_missing_returns_no_mutation(db, user):
    result = persist_daily_projection(db, build_input=_all_no_winner(user, None))

    assert result.created is False
    assert result.status is ProjectionPersistenceStatus.POLICY_MISSING
    assert db.query(NutritionDailyProjection).count() == 0
    assert db.query(NutritionProjectionHead).count() == 0


def test_no_winner_is_ready_with_canonical_no_value_fact(db, user):
    policy = _policy(db, user)
    result = persist_daily_projection(db, build_input=_all_no_winner(user, policy))
    db.commit()

    fact = db.scalar(
        select(NutritionDailyProjectionFact).where(
            NutritionDailyProjectionFact.projection_id == result.projection_id,
            NutritionDailyProjectionFact.metric_key == "protein_g",
        )
    )
    assert result.created is True
    assert fact is not None
    assert fact.value is None
    assert fact.unit == "g"
    assert fact.selected_provider_key is None
    assert fact.selected_granularity is None
    assert fact.presence_state == PresenceState.UNKNOWN.value
    assert fact.coverage_state == CoverageState.UNKNOWN.value
    assert fact.resolution_state == ResolutionState.UNRESOLVED.value
    assert fact.lineage_state == LineageState.UNKNOWN.value


def test_selected_zero_and_exact_decimal_contributions_are_persisted(db, user):
    policy = _policy(db, user)
    build_input = _all_values(db, user, policy, value=Decimal("0"))
    result = persist_daily_projection(db, build_input=build_input)
    db.commit()

    fact = db.scalar(
        select(NutritionDailyProjectionFact).where(
            NutritionDailyProjectionFact.projection_id == result.projection_id,
            NutritionDailyProjectionFact.metric_key == "protein_g",
        )
    )
    assert fact is not None and fact.value == Decimal("0")
    assert fact.presence_state == PresenceState.EXPLICIT_ZERO.value
    lineage = db.scalars(
        select(NutritionDailyProjectionLineage).where(
            NutritionDailyProjectionLineage.projection_fact_id == fact.id
        )
    ).all()
    assert len(lineage) == 1
    assert lineage[0].contribution_value == Decimal("0")
    assert lineage[0].role == PrioritySelectionRole.SELECTED.value


def test_rejected_and_diagnostic_lineage_never_persist_contribution_values(db, user):
    policy = _policy(
        db,
        user,
        providers=(("google_health", None, 1), ("yazio", None, 2)),
    )
    google_observation = _source_observation(db, user, provider_key="google_health")
    yazio_observation = _source_observation(db, user, provider_key="yazio")
    google = _candidate(user, "protein_g", google_observation, provider_key="google_health")
    yazio = _candidate(user, "protein_g", yazio_observation, provider_key="yazio")
    rules = {rule.provider_key: rule for rule in policy.rules}
    selection = _selection(
        user,
        policy,
        "protein_g",
        candidate=yazio,
        provider_key="yazio",
        role=PrioritySelectionRole.FALLBACK,
        dispositions=(
            ProviderDisposition(
                provider_key="google_health",
                rule_id=rules["google_health"].rule_id,
                priority_rank=1,
                candidate_present=True,
                eligible=False,
                role=PrioritySelectionRole.DIAGNOSTIC,
                reason_code=PriorityReasonCode.CANDIDATE_INELIGIBLE,
                candidate=google,
            ),
            ProviderDisposition(
                provider_key="yazio",
                rule_id=rules["yazio"].rule_id,
                priority_rank=2,
                candidate_present=True,
                eligible=True,
                role=PrioritySelectionRole.FALLBACK,
                reason_code=PriorityReasonCode.METRIC_SPECIFIC_PRIORITY,
                candidate=yazio,
            ),
        ),
    )
    selections = {
        metric: selection if metric == "protein_g" else _selection(
            user, policy, metric, candidate=None, role=None, reason=PriorityReasonCode.NO_ELIGIBLE_PROVIDER
        )
        for metric in CANONICAL_METRIC_KEYS
    }
    build_input = _input(user, selections, policy, tokens=(_source_token(google_observation), _source_token(yazio_observation)))
    result = persist_daily_projection(db, build_input=build_input)
    db.commit()

    fact = db.scalar(
        select(NutritionDailyProjectionFact).where(
            NutritionDailyProjectionFact.projection_id == result.projection_id,
            NutritionDailyProjectionFact.metric_key == "protein_g",
        )
    )
    assert fact is not None
    lineages = db.scalars(
        select(NutritionDailyProjectionLineage).where(
            NutritionDailyProjectionLineage.projection_fact_id == fact.id
        )
    ).all()
    assert {item.role for item in lineages} == {
        PrioritySelectionRole.FALLBACK.value,
        PrioritySelectionRole.DIAGNOSTIC.value,
    }
    assert all(item.contribution_value is None for item in lineages if item.role != "fallback")
    assert sum(item.contribution_value for item in lineages if item.role == "fallback") == fact.value


def test_missing_source_observation_rolls_back_without_projection(db, user):
    policy = _policy(db, user)
    fake_observations: list[NutritionSourceObservation] = []
    selections = {}
    for metric in CANONICAL_METRIC_KEYS:
        fake_observation = NutritionSourceObservation(
            id=uuid4(),
            user_id=user.id,
            ingestion_run_id=uuid4(),
            provider_key="yazio",
            source_instance_id=uuid4(),
            observation_kind=ObservationKind.DAILY_SUMMARY.value,
            source_namespace="b5",
            source_revision=1,
            observation_fingerprint="d" * 64,
            local_date=LOCAL_DATE,
            presence_state=PresenceState.SUPPLIED.value,
            coverage_state=CoverageState.COMPLETE.value,
            resolution_state=ResolutionState.RESOLVED.value,
            lineage_state=LineageState.CONFIRMED.value,
        )
        fake_observations.append(fake_observation)
        candidate = _candidate(user, metric, fake_observation)
        selections[metric] = _selection(
            user, policy, metric, candidate=candidate, role=PrioritySelectionRole.SELECTED
        )
    with pytest.raises(ValueError, match="source observation"):
        persist_daily_projection(
            db,
            build_input=_input(
                user,
                selections,
                policy,
                tokens=tuple(_source_token(item) for item in fake_observations),
            ),
        )
    db.rollback()
    assert db.query(NutritionDailyProjection).count() == 0
    assert db.query(NutritionDailyProjectionFact).count() == 0
    assert db.query(NutritionDailyProjectionLineage).count() == 0
    assert db.query(NutritionProjectionHead).count() == 0


def test_head_history_corruption_fails_closed(db, user):
    policy = _policy(db, user)
    first = persist_daily_projection(db, build_input=_all_values(db, user, policy))
    db.commit()
    db.delete(db.get(NutritionProjectionHead, (user.id, LOCAL_DATE)))
    db.commit()

    with pytest.raises(ValueError, match="head"):
        persist_daily_projection(db, build_input=_all_values(db, user, policy, value=Decimal("11")))
    db.rollback()
    assert db.query(NutritionDailyProjection).count() == 1
    assert db.get(NutritionDailyProjection, first.projection_id) is not None


def test_mixed_policy_selection_fails_before_mutation(db, user):
    first = _policy(db, user, version=1, effective_from=datetime(2026, 1, 1, tzinfo=UTC))
    second = _policy(db, user, version=2, effective_from=datetime(2026, 2, 1, tzinfo=UTC))
    selections = {
        metric: _selection(
            user,
            first if index else second,
            metric,
            candidate=None,
            role=None,
            reason=PriorityReasonCode.NO_ELIGIBLE_PROVIDER,
        )
        for index, metric in enumerate(CANONICAL_METRIC_KEYS)
    }
    with pytest.raises(ValueError, match="same policy"):
        persist_daily_projection(db, build_input=_input(user, selections, first))
    assert db.query(NutritionDailyProjection).count() == 0


def test_same_source_observation_contributions_aggregate_exactly_once(db, user):
    policy = _policy(db, user)
    observation = _source_observation(db, user)
    first = MetricContribution(
        evidence_id=uuid4(),
        source_observation_id=observation.id,
        metric_key="protein_g",
        value=Decimal("3"),
        unit="g",
        presence_state=PresenceState.SUPPLIED,
        resolution_state=ResolutionState.RESOLVED,
        lineage_state=LineageState.CONFIRMED,
        evidence_kind=EvidenceKind.FIELD_OBSERVATION,
        reason_code=ReasonCode.SUMMARY_ONLY,
    )
    second = MetricContribution(
        evidence_id=uuid4(),
        source_observation_id=observation.id,
        metric_key="protein_g",
        value=Decimal("7"),
        unit="g",
        presence_state=PresenceState.SUPPLIED,
        resolution_state=ResolutionState.RESOLVED,
        lineage_state=LineageState.CONFIRMED,
        evidence_kind=EvidenceKind.FIELD_OBSERVATION,
        reason_code=ReasonCode.EVENT_COMPLETE_CONFIRMED,
    )
    candidate = ProviderCandidate(
        provider_key="yazio",
        user_id=user.id,
        local_date=LOCAL_DATE,
        metric_key="protein_g",
        value=Decimal("10"),
        unit="g",
        selected_granularity=ProjectionGranularity.SUMMARY,
        presence_state=PresenceState.SUPPLIED,
        coverage_state=CoverageState.COMPLETE,
        resolution_state=ResolutionState.RESOLVED,
        lineage_state=LineageState.CONFIRMED,
        source_lineage=(first, second),
        reason_code=ReasonCode.SUMMARY_ONLY,
    )
    selections = {
        metric: (
            _selection(
                user,
                policy,
                metric,
                candidate=candidate,
                role=PrioritySelectionRole.SELECTED,
            )
            if metric == "protein_g"
            else _selection(
                user,
                policy,
                metric,
                candidate=None,
                role=None,
                reason=PriorityReasonCode.NO_ELIGIBLE_PROVIDER,
            )
        )
        for metric in CANONICAL_METRIC_KEYS
    }
    result = persist_daily_projection(db, build_input=_input(user, selections, policy, tokens=(_source_token(observation),)))
    db.commit()

    fact = db.scalar(
        select(NutritionDailyProjectionFact).where(
            NutritionDailyProjectionFact.projection_id == result.projection_id,
            NutritionDailyProjectionFact.metric_key == "protein_g",
        )
    )
    assert fact is not None and fact.value == Decimal("10")
    lineage = db.scalars(
        select(NutritionDailyProjectionLineage).where(
            NutritionDailyProjectionLineage.projection_fact_id == fact.id,
            NutritionDailyProjectionLineage.role == PrioritySelectionRole.SELECTED.value,
        )
    ).all()
    assert len(lineage) == 1
    assert lineage[0].contribution_value == Decimal("10")
    assert len(lineage[0].lineage_metadata["evidence"]) == 2
    assert lineage[0].reason_code == "multiple_evidence_reasons"
    assert lineage[0].lineage_metadata["reason_codes"] == sorted(
        ["metric_specific_priority", "summary_only", "event_complete_confirmed"]
    )


def test_source_observation_provider_mismatch_rolls_back(db, user):
    policy = _policy(db, user)
    observation = _source_observation(db, user, provider_key="google_health")
    selections = {}
    for metric in CANONICAL_METRIC_KEYS:
        selections[metric] = _selection(
            user,
            policy,
            metric,
            candidate=_candidate(user, metric, observation, provider_key="yazio"),
            role=PrioritySelectionRole.SELECTED,
        )
    with pytest.raises(ValueError, match="provider"):
        persist_daily_projection(db, build_input=_input(user, selections, policy, tokens=(_source_token(observation),)))
    db.rollback()
    assert db.query(NutritionDailyProjection).count() == 0


def test_candidate_scope_and_date_are_validated_before_persistence(db, user):
    policy = _policy(db, user)
    observation = _source_observation(db, user)
    candidate = replace(_candidate(user, "protein_g", observation), user_id=uuid4())
    with pytest.raises(ValueError, match="candidate user"):
        persist_daily_projection(
            db,
            build_input=_input_with_candidate(user, policy, candidate, tokens=(_source_token(observation),)),
        )
    db.rollback()

    policy = _policy(db, user, version=2, effective_from=datetime(2026, 2, 1, tzinfo=UTC))
    observation = _source_observation(db, user)
    candidate = replace(_candidate(user, "protein_g", observation), local_date=date(2026, 9, 10))
    with pytest.raises(ValueError, match="candidate local_date"):
        persist_daily_projection(
            db,
            build_input=_input_with_candidate(user, policy, candidate, tokens=(_source_token(observation),)),
        )
    db.rollback()


def test_source_observation_date_and_token_are_bound(db, user):
    policy = _policy(db, user)
    observation = _source_observation(db, user, local_date=date(2026, 9, 10))
    candidate = _candidate(user, "protein_g", observation)
    token = _source_token(observation)
    with pytest.raises(ValueError, match="local_date"):
        persist_daily_projection(
            db,
            build_input=_input_with_candidate(user, policy, candidate, tokens=(token,)),
        )
    db.rollback()

    policy = _policy(db, user, version=2, effective_from=datetime(2026, 2, 1, tzinfo=UTC))
    current = _source_observation(db, user, fingerprint="e" * 64)
    current_candidate = _candidate(user, "protein_g", current)
    stale_token = replace(_source_token(current), source_revision=2)
    with pytest.raises(ValueError, match="token"):
        persist_daily_projection(
            db,
            build_input=_input_with_candidate(user, policy, current_candidate, tokens=(stale_token,)),
        )
    db.rollback()

    policy = _policy(db, user, version=3, effective_from=datetime(2026, 3, 1, tzinfo=UTC))
    current = _source_observation(db, user, fingerprint="f" * 64)
    current_candidate = _candidate(user, "protein_g", current)
    with pytest.raises(ValueError, match="token"):
        persist_daily_projection(
            db,
            build_input=_input_with_candidate(user, policy, current_candidate, tokens=()),
        )
    db.rollback()


def test_projection_rejects_unrepresentable_decimal_precision(db, user):
    policy = _policy(db, user)
    observation = _source_observation(db, user)
    candidate = _candidate(user, "protein_g", observation, value=Decimal("1.0000000000001"))
    with pytest.raises(ValueError, match="12 fractional"):
        persist_daily_projection(
            db,
            build_input=_input_with_candidate(user, policy, candidate, tokens=(_source_token(observation),)),
        )
    db.rollback()


def test_rule_manifest_mismatch_fails_closed_before_persistence(db, user):
    policy = _policy(db, user)
    build_input = _all_no_winner(user, policy)
    invalid_manifest = replace(build_input.input_manifest, relevant_rules=())
    with pytest.raises(ValueError, match="relevant rules"):
        replace(build_input, input_manifest=invalid_manifest)
    assert db.query(NutritionDailyProjection).count() == 0


def test_lineage_insert_failure_rolls_back_entire_projection_graph(db, user, monkeypatch):
    policy = _policy(db, user)
    build_input = _all_values(db, user, policy)
    import app.nutrition.projection.persistence as persistence_module

    def fail_lineage(*args, **kwargs):
        raise RuntimeError("injected lineage failure")

    monkeypatch.setattr(persistence_module, "create_projection_lineage", fail_lineage)
    with pytest.raises(RuntimeError, match="injected lineage failure"):
        persist_daily_projection(db, build_input=build_input)
    db.rollback()
    assert db.query(NutritionDailyProjection).count() == 0
    assert db.query(NutritionDailyProjectionFact).count() == 0
    assert db.query(NutritionDailyProjectionLineage).count() == 0
    assert db.query(NutritionProjectionHead).count() == 0


def test_head_must_point_to_highest_version(db, user):
    policy = _policy(db, user)
    first = persist_daily_projection(db, build_input=_all_values(db, user, policy, value=Decimal("10")))
    db.commit()
    second = persist_daily_projection(db, build_input=_all_values(db, user, policy, value=Decimal("11")))
    db.commit()
    head = db.get(NutritionProjectionHead, (user.id, LOCAL_DATE))
    assert head is not None
    head.current_projection_id = first.projection_id
    db.commit()

    with pytest.raises(ValueError, match="highest version"):
        persist_daily_projection(db, build_input=_all_values(db, user, policy, value=Decimal("12")))
    db.rollback()
    assert db.get(NutritionProjectionHead, (user.id, LOCAL_DATE)).current_projection_id == first.projection_id
    assert db.get(NutritionDailyProjection, second.projection_id) is not None
