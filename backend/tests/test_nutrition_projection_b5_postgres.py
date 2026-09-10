from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import SessionLocal, engine
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
from app.nutrition.resolution import EvidenceKind, MetricContribution, ProviderCandidate, ReasonCode
from app.source_priority.application import create_policy_with_rules
from app.source_priority.contracts import (
    AppliedPriorityScope,
    PriorityReasonCode,
    PriorityRuleSpec,
    PrioritySelection,
    PrioritySelectionRole,
    ProviderDisposition,
)

POSTGRES_TESTS_ENABLED = (
    os.environ.get("CALOGRAPH_ALLOW_DESTRUCTIVE_POSTGRES_TESTS") == "1"
    and bool(os.environ.get("CALOGRAPH_POSTGRES_TEST_URL"))
)
LOCAL_DATE = date(2026, 9, 11)


def _build_input(
    db: Session,
    user: User,
    *,
    value_offset: int = 0,
    policy_version: int = 1,
    effective_from: datetime = datetime(2026, 1, 1, tzinfo=UTC),
) -> DailyProjectionBuildInput:
    policy = create_policy_with_rules(
        db,
        user.id,
        version=policy_version,
        effective_from=effective_from,
        rules=(
            PriorityRuleSpec(
                data_area="nutrition",
                metric_key=None,
                provider_key="yazio",
                priority_rank=1,
            ),
        ),
    )
    selections: list[PrioritySelection] = []
    tokens: list[SourceObservationToken] = []
    for index, metric_key in enumerate(CANONICAL_METRIC_KEYS):
        run = NutritionIngestionRun(
            user_id=user.id,
            provider_key="yazio",
            source_instance_id=uuid4(),
            status="completed",
            coverage_state=CoverageState.COMPLETE.value,
        )
        db.add(run)
        db.flush()
        observation = NutritionSourceObservation(
            user_id=user.id,
            ingestion_run_id=run.id,
            provider_key="yazio",
            source_instance_id=run.source_instance_id,
            observation_kind=ObservationKind.DAILY_SUMMARY.value,
            source_namespace="b5-postgres",
            source_revision=1,
            observation_fingerprint=f"{index + value_offset + 1:064x}"[-64:],
            local_date=LOCAL_DATE,
            presence_state=PresenceState.SUPPLIED.value,
            coverage_state=CoverageState.COMPLETE.value,
            resolution_state=ResolutionState.RESOLVED.value,
            lineage_state=LineageState.CONFIRMED.value,
        )
        db.add(observation)
        db.flush()
        value = Decimal(10 + index + value_offset)
        contribution = MetricContribution(
            evidence_id=uuid4(),
            source_observation_id=observation.id,
            metric_key=metric_key,
            value=value,
            unit="kcal" if metric_key == "dietary_energy_kcal" else "g",
            presence_state=PresenceState.SUPPLIED,
            resolution_state=ResolutionState.RESOLVED,
            lineage_state=LineageState.CONFIRMED,
            evidence_kind=EvidenceKind.FIELD_OBSERVATION,
            reason_code=ReasonCode.SUMMARY_ONLY,
        )
        candidate = ProviderCandidate(
            provider_key="yazio",
            user_id=user.id,
            local_date=LOCAL_DATE,
            metric_key=metric_key,
            value=value,
            unit=contribution.unit,
            selected_granularity=ProjectionGranularity.SUMMARY,
            presence_state=PresenceState.SUPPLIED,
            coverage_state=CoverageState.COMPLETE,
            resolution_state=ResolutionState.RESOLVED,
            lineage_state=LineageState.CONFIRMED,
            source_lineage=(contribution,),
            reason_code=ReasonCode.SUMMARY_ONLY,
        )
        rule = policy.rules[0]
        selections.append(
            PrioritySelection(
                user_id=user.id,
                local_date=LOCAL_DATE,
                metric_key=metric_key,
                policy=policy,
                applied_scope=AppliedPriorityScope.WILDCARD,
                selected_candidate=candidate,
                selected_role=PrioritySelectionRole.SELECTED,
                reason_code=PriorityReasonCode.WILDCARD_PRIORITY,
                dispositions=(
                    ProviderDisposition(
                        provider_key="yazio",
                        rule_id=rule.rule_id,
                        priority_rank=rule.priority_rank,
                        candidate_present=True,
                        eligible=True,
                        role=PrioritySelectionRole.SELECTED,
                        reason_code=PriorityReasonCode.WILDCARD_PRIORITY,
                        candidate=candidate,
                    ),
                ),
            )
        )
        tokens.append(
            SourceObservationToken(
                source_observation_id=observation.id,
                source_revision=observation.source_revision,
                observation_fingerprint=observation.observation_fingerprint,
            )
        )
    return DailyProjectionBuildInput(
        user_id=user.id,
        local_date=LOCAL_DATE,
        selections=tuple(selections),
        input_manifest=ProjectionInputManifest(
            user_id=user.id,
            local_date=LOCAL_DATE,
            projection_algorithm_version="nutrition-daily-v1",
            metric_registry_version="nutrition-metrics-v1",
            watermark_format_version="nutrition-watermark-v1",
            policy=policy,
            relevant_rules=policy.rules,
            technical_evidence=tuple(tokens),
        ),
    )


def _create_user() -> User:
    with SessionLocal() as db:
        user = User(username=f"b5-postgres-{uuid4()}", password_hash="hash", timezone="UTC")
        db.add(user)
        db.flush()
        db.commit()
        db.refresh(user)
        return user


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL persistence tests are not explicitly enabled",
)
def test_b5_postgres_uses_existing_schema_and_scope_constraints() -> None:
    assert engine.dialect.name == "postgresql"
    user = _create_user()
    with SessionLocal() as db:
        build_input = _build_input(db, user)
        db.commit()
    with SessionLocal() as db:
        result = persist_daily_projection(db, build_input=build_input)
        db.commit()
        assert result.created is True
        assert db.scalar(select(func.count()).select_from(NutritionDailyProjection)) == 1
        assert db.scalar(select(func.count()).select_from(NutritionDailyProjectionFact)) == 7
        assert db.scalar(select(func.count()).select_from(NutritionDailyProjectionLineage)) == 7
        assert db.scalar(select(func.count()).select_from(NutritionProjectionHead)) == 1


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL persistence tests are not explicitly enabled",
)
def test_b5_postgres_same_input_serializes_to_one_projection() -> None:
    assert engine.dialect.name == "postgresql"
    user = _create_user()
    with SessionLocal() as db:
        build_input = _build_input(db, user)
        db.commit()

    barrier = Barrier(2)

    def write_once() -> tuple[bool, ProjectionPersistenceStatus]:
        with SessionLocal() as db:
            barrier.wait()
            result = persist_daily_projection(db, build_input=build_input)
            db.commit()
            return result.created, result.status

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: write_once(), range(2)))

    assert sorted(results) == [
        (False, ProjectionPersistenceStatus.UNCHANGED),
        (True, ProjectionPersistenceStatus.CREATED),
    ]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(NutritionDailyProjection)) == 1
        assert db.scalar(select(func.count()).select_from(NutritionDailyProjectionFact)) == 7
        assert db.scalar(select(func.count()).select_from(NutritionProjectionHead)) == 1


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL persistence tests are not explicitly enabled",
)
def test_b5_postgres_different_inputs_serialize_to_two_complete_versions() -> None:
    assert engine.dialect.name == "postgresql"
    user = _create_user()
    with SessionLocal() as db:
        first_input = _build_input(db, user, value_offset=0)
        db.commit()
    with SessionLocal() as db:
        second_input = _build_input(
            db,
            user,
            value_offset=100,
            policy_version=2,
            effective_from=datetime(2026, 2, 1, tzinfo=UTC),
        )
        db.commit()

    barrier = Barrier(2)

    def write_once(build_input: DailyProjectionBuildInput) -> tuple[bool, int | None]:
        with SessionLocal() as db:
            barrier.wait()
            result = persist_daily_projection(db, build_input=build_input)
            db.commit()
            return result.created, result.projection_version

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(write_once, (first_input, second_input)))

    assert all(created for created, _ in results)
    assert sorted(version for _, version in results) == [1, 2]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(NutritionDailyProjection)) == 2
        assert db.scalar(select(func.count()).select_from(NutritionDailyProjectionFact)) == 14
        assert db.scalar(select(func.count()).select_from(NutritionDailyProjectionLineage)) == 14
        head = db.scalar(select(NutritionProjectionHead).where(NutritionProjectionHead.user_id == user.id))
        assert head is not None
        current = db.get(NutritionDailyProjection, head.current_projection_id)
        assert current is not None and current.projection_version == 2
