from __future__ import annotations

from datetime import date
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
    NutritionExternalIdentity,
    NutritionExternalIdentityLink,
    NutritionFoodProfile,
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
