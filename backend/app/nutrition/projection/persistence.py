from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User
from app.nutrition.models import (
    NutritionDailyProjection,
    NutritionProjectionHead,
    NutritionSourceObservation,
)
from app.nutrition.repositories import (
    create_projection,
    create_projection_fact,
    create_projection_lineage,
    set_projection_head,
)
from app.source_priority.models import SourcePriorityPolicy, SourcePriorityRule

from .contracts import (
    DailyProjectionBuildInput,
    ProjectionContractError,
    ProjectionPersistenceResult,
    ProjectionPersistenceStatus,
)
from .mapping import ProjectionFactPayload, map_build_input_to_facts
from .tokens import SourceObservationToken
from .watermark import compute_input_watermark


def _normalize_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _validate_policy_scope(db: Session, build_input: DailyProjectionBuildInput) -> UUID:
    policy_snapshot = build_input.input_manifest.policy
    if policy_snapshot is None:
        raise ProjectionContractError("policy is required for persistence")
    policy = db.scalar(
        select(SourcePriorityPolicy).where(
            SourcePriorityPolicy.id == policy_snapshot.policy_id,
            SourcePriorityPolicy.user_id == build_input.user_id,
        )
    )
    if policy is None:
        raise ValueError("priority policy must belong to the same user")
    if policy.version != policy_snapshot.version or _normalize_utc(policy.effective_from) != policy_snapshot.effective_from:
        raise ValueError("priority policy snapshot does not match persisted policy")

    persisted_rules = {
        rule.id: rule
        for rule in db.scalars(
            select(SourcePriorityRule).where(
                SourcePriorityRule.policy_id == policy_snapshot.policy_id,
                SourcePriorityRule.user_id == build_input.user_id,
            )
        )
    }
    for rule_snapshot in build_input.input_manifest.relevant_rules:
        rule = persisted_rules.get(rule_snapshot.rule_id)
        if rule is None or (
            rule.data_area,
            rule.metric_key,
            rule.provider_key,
            rule.priority_rank,
        ) != (
            rule_snapshot.data_area,
            rule_snapshot.metric_key,
            rule_snapshot.provider_key,
            rule_snapshot.priority_rank,
        ):
            raise ValueError("priority rule snapshot does not match persisted rule")
    return policy_snapshot.policy_id


def _validate_lineage_sources(
    db: Session,
    build_input: DailyProjectionBuildInput,
    facts: tuple[ProjectionFactPayload, ...],
) -> None:
    source_tokens = {
        token.source_observation_id: token
        for token in build_input.input_manifest.technical_evidence
        if isinstance(token, SourceObservationToken)
    }
    for fact in facts:
        for lineage in fact.lineage:
            token = source_tokens.get(lineage.source_observation_id)
            if token is None:
                raise ValueError("lineage source observation requires a technical evidence token")
            observation = db.scalar(
                select(NutritionSourceObservation).where(
                    NutritionSourceObservation.id == lineage.source_observation_id,
                    NutritionSourceObservation.user_id == build_input.user_id,
                )
            )
            if observation is None:
                raise ValueError("source observation must exist for the same user")
            if observation.local_date != build_input.local_date:
                raise ValueError("source observation local_date does not match projection")
            if observation.provider_key != lineage.provider_key:
                raise ValueError("source observation provider does not match lineage provider")
            if token.source_revision != observation.source_revision:
                raise ValueError("source observation token revision does not match persisted observation")
            if token.observation_fingerprint != observation.observation_fingerprint:
                raise ValueError("source observation token fingerprint does not match persisted observation")
            if token.payload_hash is not None and token.payload_hash != observation.payload_hash:
                raise ValueError("source observation token payload hash does not match persisted observation")
            if token.provider_key is not None and token.provider_key != lineage.provider_key:
                raise ValueError("source observation token provider does not match lineage provider")
            if token.metric_key is not None and token.metric_key != fact.metric_key:
                raise ValueError("source observation token metric does not match projection fact")

def _lock_user(db: Session, user_id: UUID) -> None:
    owner = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if owner is None:
        raise ValueError("projection user does not exist")


def _read_history_and_head(
    db: Session,
    *,
    user_id: UUID,
    local_date: date,
) -> tuple[list[NutritionDailyProjection], NutritionProjectionHead | None]:
    projections = list(
        db.scalars(
            select(NutritionDailyProjection)
            .where(
                NutritionDailyProjection.user_id == user_id,
                NutritionDailyProjection.local_date == local_date,
            )
            .order_by(NutritionDailyProjection.projection_version)
        )
    )
    head = db.scalar(
        select(NutritionProjectionHead)
        .where(
            NutritionProjectionHead.user_id == user_id,
            NutritionProjectionHead.local_date == local_date,
        )
        .with_for_update()
    )
    if not projections:
        if head is not None:
            raise ValueError("projection head exists without projection history")
        return projections, None
    if head is None:
        raise ValueError("projection history exists without a head")
    current = next((projection for projection in projections if projection.id == head.current_projection_id), None)
    if current is None:
        raise ValueError("projection head points outside its scope")
    if current.projection_version != projections[-1].projection_version:
        raise ValueError("projection head does not point to the highest version")
    return projections, head


def _persist_facts_and_lineage(
    db: Session,
    *,
    build_input: DailyProjectionBuildInput,
    projection: NutritionDailyProjection,
    facts: tuple[ProjectionFactPayload, ...],
) -> None:
    for payload in facts:
        fact = create_projection_fact(
            db,
            user_id=build_input.user_id,
            projection_id=projection.id,
            metric_key=payload.metric_key,
            value=payload.value,
            unit=payload.unit,
            selected_provider_key=payload.selected_provider_key,
            selected_granularity=payload.selected_granularity,
            presence_state=payload.presence_state,
            coverage_state=payload.coverage_state,
            resolution_state=payload.resolution_state,
            lineage_state=payload.lineage_state,
            diagnostic_metadata=payload.diagnostic_metadata,
        )
        for lineage in payload.lineage:
            create_projection_lineage(
                db,
                user_id=build_input.user_id,
                projection_fact_id=fact.id,
                source_observation_id=lineage.source_observation_id,
                provider_key=lineage.provider_key,
                role=lineage.role.value,
                granularity=lineage.granularity,
                contribution_value=lineage.contribution_value,
                presence_state=lineage.presence_state,
                coverage_state=lineage.coverage_state,
                reason_code=lineage.reason_code,
                metadata=lineage.metadata,
            )


def persist_daily_projection(
    db: Session,
    *,
    build_input: DailyProjectionBuildInput,
) -> ProjectionPersistenceResult:
    if not isinstance(build_input, DailyProjectionBuildInput):
        raise TypeError("build_input must be DailyProjectionBuildInput")
    watermark = compute_input_watermark(build_input.input_manifest)
    if build_input.input_manifest.policy is None:
        return ProjectionPersistenceResult(
            user_id=build_input.user_id,
            local_date=build_input.local_date,
            projection_id=None,
            projection_version=None,
            input_watermark=watermark,
            created=False,
            status=ProjectionPersistenceStatus.POLICY_MISSING,
        )

    facts = map_build_input_to_facts(build_input)
    with db.begin_nested():
        _lock_user(db, build_input.user_id)
        policy_id = _validate_policy_scope(db, build_input)
        _validate_lineage_sources(db, build_input, facts)
        history, head = _read_history_and_head(
            db,
            user_id=build_input.user_id,
            local_date=build_input.local_date,
        )
        current = history[-1] if history else None
        if (
            current is not None
            and head is not None
            and current.input_watermark == watermark
            and current.projection_algorithm_version
            == build_input.input_manifest.projection_algorithm_version
            and current.priority_policy_id == policy_id
        ):
            return ProjectionPersistenceResult(
                user_id=build_input.user_id,
                local_date=build_input.local_date,
                projection_id=None,
                projection_version=None,
                input_watermark=watermark,
                created=False,
                status=ProjectionPersistenceStatus.UNCHANGED,
            )

        version = 1 if current is None else current.projection_version + 1
        projection = create_projection(
            db,
            user_id=build_input.user_id,
            local_date=build_input.local_date,
            projection_version=version,
            projection_algorithm_version=build_input.input_manifest.projection_algorithm_version,
            priority_policy_id=policy_id,
            input_watermark=watermark,
            projection_status="ready",
        )
        _persist_facts_and_lineage(
            db,
            build_input=build_input,
            projection=projection,
            facts=facts,
        )
        set_projection_head(
            db,
            build_input.user_id,
            build_input.local_date,
            projection.id,
        )
        return ProjectionPersistenceResult(
            user_id=build_input.user_id,
            local_date=build_input.local_date,
            projection_id=projection.id,
            projection_version=version,
            input_watermark=watermark,
            created=True,
            status=ProjectionPersistenceStatus.CREATED,
        )
