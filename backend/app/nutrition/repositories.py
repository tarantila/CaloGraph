from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.nutrition.contracts import (
    validate_observation_fingerprint,
    validate_source_record_id,
    validate_source_revision,
)
from app.nutrition.models import (
    NutritionConsumptionEvent,
    NutritionDailyProjection,
    NutritionDailyProjectionFact,
    NutritionDailyProjectionLineage,
    NutritionExternalIdentity,
    NutritionExternalIdentityLink,
    NutritionFoodProfile,
    NutritionProjectionHead,
    NutritionSourceObservation,
)


def _target_count(
    *,
    consumption_event_id: UUID | None,
    food_profile_id: UUID | None,
    source_observation_id: UUID | None,
) -> int:
    return sum(
        value is not None
        for value in (consumption_event_id, food_profile_id, source_observation_id)
    )


def _require_same_user_target(
    db: Session,
    user_id: UUID,
    *,
    consumption_event_id: UUID | None,
    food_profile_id: UUID | None,
    source_observation_id: UUID | None,
) -> None:
    if consumption_event_id is not None:

        target = db.scalar(
            select(NutritionConsumptionEvent.id).where(
                NutritionConsumptionEvent.id == consumption_event_id,
                NutritionConsumptionEvent.user_id == user_id,
            )
        )
        if target is None:
            raise ValueError("consumption event target must belong to the same user")
    if food_profile_id is not None:
        target = db.scalar(
            select(NutritionFoodProfile.id).where(
                NutritionFoodProfile.id == food_profile_id,
                NutritionFoodProfile.user_id == user_id,
            )
        )
        if target is None:
            raise ValueError("food profile target must belong to the same user")
    if source_observation_id is not None:
        target = db.scalar(
            select(NutritionSourceObservation.id).where(
                NutritionSourceObservation.id == source_observation_id,
                NutritionSourceObservation.user_id == user_id,
            )
        )
        if target is None:
            raise ValueError("source observation target must belong to the same user")


def append_identity_link(
    db: Session,
    *,
    user_id: UUID,
    external_identity_id: UUID,
    link_role: str,
    consumption_event_id: UUID | None = None,
    food_profile_id: UUID | None = None,
    source_observation_id: UUID | None = None,
    supersedes_link_id: UUID | None = None,
) -> NutritionExternalIdentityLink:
    if _target_count(
        consumption_event_id=consumption_event_id,
        food_profile_id=food_profile_id,
        source_observation_id=source_observation_id,
    ) != 1:
        raise ValueError("identity link must have exactly one target")

    identity = db.scalar(
        select(NutritionExternalIdentity)
        .where(
            NutritionExternalIdentity.id == external_identity_id,
            NutritionExternalIdentity.user_id == user_id,
        )
        .with_for_update()
    )
    if identity is None:
        raise ValueError("external identity must belong to the same user")

    _require_same_user_target(
        db,
        user_id,
        consumption_event_id=consumption_event_id,
        food_profile_id=food_profile_id,
        source_observation_id=source_observation_id,
    )

    latest = current_identity_link(db, user_id, external_identity_id, link_role)
    if supersedes_link_id is not None:
        supersedes = db.scalar(
            select(NutritionExternalIdentityLink).where(
                NutritionExternalIdentityLink.id == supersedes_link_id,
                NutritionExternalIdentityLink.user_id == user_id,
                NutritionExternalIdentityLink.external_identity_id == external_identity_id,
            )
        )
        if supersedes is None:
            raise ValueError("superseded identity link must belong to the same user and identity")
        if supersedes.link_role != link_role:
            raise ValueError("superseded identity link must have the same role")
        if latest is not None and latest.id != supersedes.id:
            raise ValueError("superseded identity link must be the current role revision")
        link_revision = supersedes.link_revision + 1
        supersedes_revision: int | None = supersedes.link_revision
    else:
        link_revision = 1 if latest is None else latest.link_revision + 1
        supersedes_link_id = latest.id if latest is not None else None
        supersedes_revision = latest.link_revision if latest is not None else None

    link = NutritionExternalIdentityLink(
        user_id=user_id,
        external_identity_id=external_identity_id,
        link_role=link_role,
        consumption_event_id=consumption_event_id,
        food_profile_id=food_profile_id,
        source_observation_id=source_observation_id,
        link_revision=link_revision,
        supersedes_link_id=supersedes_link_id,
        supersedes_link_revision=supersedes_revision,
    )
    db.add(link)
    db.flush()
    return link


def current_identity_link(
    db: Session,
    user_id: UUID,
    external_identity_id: UUID,
    link_role: str,
) -> NutritionExternalIdentityLink | None:
    return db.scalar(
        select(NutritionExternalIdentityLink)
        .where(
            NutritionExternalIdentityLink.user_id == user_id,
            NutritionExternalIdentityLink.external_identity_id == external_identity_id,
            NutritionExternalIdentityLink.link_role == link_role,
        )
        .order_by(NutritionExternalIdentityLink.link_revision.desc())
        .limit(1)
    )


def validate_source_instance(
    db: Session,
    *,
    user_id: UUID,
    provider_key: str,
    source_instance_id: UUID,
) -> None:
    """Validate known provider ownership; unknown providers use an internal UUID contract."""

    if provider_key == "yazio":
        from app.models import YazioConnection

        owned = db.scalar(
            select(YazioConnection.id).where(
                YazioConnection.id == source_instance_id,
                YazioConnection.user_id == user_id,
            )
        )
        if owned is None:
            raise ValueError("source_instance_id must belong to the same user")


def create_source_observation(
    db: Session,
    *,
    user_id: UUID,
    ingestion_run_id: UUID,
    provider_key: str,
    source_instance_id: UUID,
    source_namespace: str,
    source_record_id: str | None,
    source_revision: int,
    observation_fingerprint: str,
    observation_kind: str,
    local_date: date | None = None,
    **fields: Any,
) -> NutritionSourceObservation:
    validate_source_instance(
        db,
        user_id=user_id,
        provider_key=provider_key,
        source_instance_id=source_instance_id,
    )
    validate_source_record_id(source_record_id)
    validate_source_revision(source_revision)
    validate_observation_fingerprint(observation_fingerprint)
    observation = NutritionSourceObservation(
        id=uuid4(),
        user_id=user_id,
        ingestion_run_id=ingestion_run_id,
        provider_key=provider_key,
        source_instance_id=source_instance_id,
        source_namespace=source_namespace,
        source_record_id=source_record_id,
        source_revision=source_revision,
        observation_fingerprint=observation_fingerprint,
        observation_kind=observation_kind,
        local_date=local_date,
        **fields,
    )
    db.add(observation)
    db.flush()
    return observation



def _projection_utcnow() -> datetime:
    return datetime.now(UTC)


def _require_non_empty(value: str, field_name: str) -> str:
    if not value:
        raise ValueError(f"{field_name} must be non-empty")
    return value


def _require_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


def create_projection(
    db: Session,
    *,
    user_id: UUID,
    local_date: date,
    projection_version: int,
    projection_algorithm_version: str,
    priority_policy_id: UUID,
    input_watermark: str,
    projection_status: str,
    generated_at: datetime | None = None,
) -> NutritionDailyProjection:
    if projection_version < 1:
        raise ValueError("projection_version must be at least 1")
    _require_non_empty(projection_algorithm_version, "projection_algorithm_version")
    _require_non_empty(input_watermark, "input_watermark")
    if generated_at is not None:
        _require_aware(generated_at, "generated_at")
    projection = NutritionDailyProjection(
        user_id=user_id,
        local_date=local_date,
        projection_version=projection_version,
        projection_algorithm_version=projection_algorithm_version,
        priority_policy_id=priority_policy_id,
        input_watermark=input_watermark,
        projection_status=projection_status,
        generated_at=generated_at or _projection_utcnow(),
    )
    db.add(projection)
    db.flush()
    return projection


def create_projection_fact(
    db: Session,
    *,
    user_id: UUID,
    projection_id: UUID,
    metric_key: str,
    value: Decimal | None,
    unit: str | None,
    selected_provider_key: str | None,
    selected_granularity: str | None,
    presence_state: str,
    coverage_state: str,
    resolution_state: str,
    lineage_state: str,
    diagnostic_metadata: dict[str, Any] | None = None,
) -> NutritionDailyProjectionFact:
    _require_non_empty(metric_key, "metric_key")
    if selected_provider_key is not None:
        _require_non_empty(selected_provider_key, "selected_provider_key")
    fact = NutritionDailyProjectionFact(
        user_id=user_id,
        projection_id=projection_id,
        metric_key=metric_key,
        value=value,
        unit=unit,
        selected_provider_key=selected_provider_key,
        selected_granularity=selected_granularity,
        presence_state=presence_state,
        coverage_state=coverage_state,
        resolution_state=resolution_state,
        lineage_state=lineage_state,
        diagnostic_metadata=diagnostic_metadata or {},
    )
    db.add(fact)
    db.flush()
    return fact


def create_projection_lineage(
    db: Session,
    *,
    user_id: UUID,
    projection_fact_id: UUID,
    source_observation_id: UUID,
    provider_key: str,
    role: str,
    granularity: str | None = None,
    contribution_value: Decimal | None = None,
    presence_state: str | None = None,
    coverage_state: str,
    reason_code: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> NutritionDailyProjectionLineage:
    _require_non_empty(provider_key, "provider_key")
    lineage = NutritionDailyProjectionLineage(
        user_id=user_id,
        projection_fact_id=projection_fact_id,
        source_observation_id=source_observation_id,
        provider_key=provider_key,
        role=role,
        granularity=granularity,
        contribution_value=contribution_value,
        presence_state=presence_state,
        coverage_state=coverage_state,
        reason_code=reason_code,
        lineage_metadata=metadata or {},
    )
    db.add(lineage)
    db.flush()
    return lineage


def get_projection_by_version(
    db: Session,
    user_id: UUID,
    local_date: date,
    projection_version: int,
) -> NutritionDailyProjection | None:
    return db.scalar(
        select(NutritionDailyProjection).where(
            NutritionDailyProjection.user_id == user_id,
            NutritionDailyProjection.local_date == local_date,
            NutritionDailyProjection.projection_version == projection_version,
        )
    )


def get_current_projection(
    db: Session,
    user_id: UUID,
    local_date: date,
) -> NutritionDailyProjection | None:
    return db.scalar(
        select(NutritionDailyProjection)
        .join(
            NutritionProjectionHead,
            NutritionProjectionHead.current_projection_id == NutritionDailyProjection.id,
        )
        .where(
            NutritionProjectionHead.user_id == user_id,
            NutritionProjectionHead.local_date == local_date,
            NutritionDailyProjection.user_id == user_id,
            NutritionDailyProjection.local_date == local_date,
        )
    )


def set_projection_head(
    db: Session,
    user_id: UUID,
    local_date: date,
    projection_id: UUID,
) -> NutritionProjectionHead:
    projection = db.scalar(
        select(NutritionDailyProjection).where(
            NutritionDailyProjection.id == projection_id,
            NutritionDailyProjection.user_id == user_id,
            NutritionDailyProjection.local_date == local_date,
        )
    )
    if projection is None:
        raise ValueError("projection must belong to the same user and local date")

    head = db.scalar(
        select(NutritionProjectionHead)
        .where(
            NutritionProjectionHead.user_id == user_id,
            NutritionProjectionHead.local_date == local_date,
        )
        .with_for_update()
    )
    if head is None:
        head = NutritionProjectionHead(
            user_id=user_id,
            local_date=local_date,
            current_projection_id=projection.id,
            updated_at=_projection_utcnow(),
        )
        db.add(head)
    else:
        head.current_projection_id = projection.id
        head.updated_at = _projection_utcnow()
    db.flush()
    return head
