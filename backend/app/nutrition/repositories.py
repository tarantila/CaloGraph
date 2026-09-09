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
    NutritionFoodSnapshot,
    NutritionIngestionRun,
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
def _require_identity_target_compatibility(
    db: Session,
    *,
    identity: NutritionExternalIdentity,
    user_id: UUID,
    consumption_event_id: UUID | None,
    food_profile_id: UUID | None,
    source_observation_id: UUID | None,
) -> None:
    target_id = consumption_event_id or food_profile_id or source_observation_id
    if target_id is None:
        return
    if consumption_event_id is not None:
        target = db.scalar(
            select(NutritionConsumptionEvent).where(
                NutritionConsumptionEvent.id == target_id,
                NutritionConsumptionEvent.user_id == user_id,
            )
        )
    elif food_profile_id is not None:
        target = db.scalar(
            select(NutritionFoodProfile).where(
                NutritionFoodProfile.id == target_id,
                NutritionFoodProfile.user_id == user_id,
            )
        )
    else:
        target = db.scalar(
            select(NutritionSourceObservation).where(
                NutritionSourceObservation.id == target_id,
                NutritionSourceObservation.user_id == user_id,
            )
        )
    if target is None:
        return
    if target.provider_key != identity.provider_key:
        raise ValueError("identity target provider must match external identity")
    if hasattr(target, "source_instance_id"):
        validate_source_instance(
            db,
            user_id=user_id,
            provider_key=target.provider_key,
            source_instance_id=target.source_instance_id,
        )


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
    _require_identity_target_compatibility(
        db,
        identity=identity,
        user_id=user_id,
        consumption_event_id=consumption_event_id,
        food_profile_id=food_profile_id,
        source_observation_id=source_observation_id,
    )

    latest = current_identity_link(db, user_id, external_identity_id, link_role)
    requested_target = (
        consumption_event_id,
        food_profile_id,
        source_observation_id,
    )
    if latest is not None and (
        latest.consumption_event_id,
        latest.food_profile_id,
        latest.source_observation_id,
    ) == requested_target and supersedes_link_id is None:
        return latest
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


def get_or_create_identity_link(
    db: Session,
    *,
    user_id: UUID,
    external_identity_id: UUID,
    link_role: str,
    consumption_event_id: UUID | None = None,
    food_profile_id: UUID | None = None,
    source_observation_id: UUID | None = None,
) -> NutritionExternalIdentityLink:
    """Return the current link, appending a revision only when its target changes."""

    return append_identity_link(
        db,
        user_id=user_id,
        external_identity_id=external_identity_id,
        link_role=link_role,
        consumption_event_id=consumption_event_id,
        food_profile_id=food_profile_id,
        source_observation_id=source_observation_id,
    )


def _validate_ingestion_run(
    db: Session,
    *,
    user_id: UUID,
    ingestion_run_id: UUID,
    provider_key: str,
    source_instance_id: UUID,
) -> None:
    run = db.scalar(
        select(NutritionIngestionRun).where(
            NutritionIngestionRun.id == ingestion_run_id,
            NutritionIngestionRun.user_id == user_id,
        )
    )
    if run is None:
        raise ValueError("ingestion run must belong to the same user")
    if run.provider_key != provider_key or run.source_instance_id != source_instance_id:
        raise ValueError("ingestion run provider and source instance must match")


def create_ingestion_run(
    db: Session,
    *,
    user_id: UUID,
    provider_key: str,
    source_instance_id: UUID,
    connector_variant: str | None = None,
    requested_start_date: date | None = None,
    requested_end_date: date | None = None,
    covered_start_date: date | None = None,
    covered_end_date: date | None = None,
    status: str = "pending",
    coverage_state: str = "unknown",
    provider_metadata: dict[str, Any] | None = None,
) -> NutritionIngestionRun:

    validate_source_instance(
        db,
        user_id=user_id,
        provider_key=provider_key,
        source_instance_id=source_instance_id,
    )
    if (
        requested_start_date is not None
        and requested_end_date is not None
        and requested_start_date > requested_end_date
    ):
        raise ValueError("requested_start_date must not be after requested_end_date")
    run = NutritionIngestionRun(
        user_id=user_id,
        provider_key=provider_key,
        source_instance_id=source_instance_id,
        connector_variant=connector_variant,
        requested_start_date=requested_start_date,
        requested_end_date=requested_end_date,
        covered_start_date=covered_start_date,
        covered_end_date=covered_end_date,
        status=status,
        coverage_state=coverage_state,
        provider_metadata=provider_metadata or {},
    )
    db.add(run)
    db.flush()
    return run


def get_source_observation(
    db: Session,
    *,
    user_id: UUID,
    source_instance_id: UUID,
    provider_key: str,
    source_namespace: str,
    source_record_id: str | None,
    source_revision: int,
    observation_fingerprint: str,
) -> NutritionSourceObservation | None:
    """Look up the A1 source identity without crossing a user boundary."""

    validate_source_instance(
        db,
        user_id=user_id,
        provider_key=provider_key,
        source_instance_id=source_instance_id,
    )
    validate_source_record_id(source_record_id)
    validate_source_revision(source_revision)
    validate_observation_fingerprint(observation_fingerprint)
    identity_filters = [
        NutritionSourceObservation.user_id == user_id,
        NutritionSourceObservation.source_instance_id == source_instance_id,
        NutritionSourceObservation.provider_key == provider_key,
        NutritionSourceObservation.source_namespace == source_namespace,
    ]
    if source_record_id is None:
        identity_filters.extend(
            [
                NutritionSourceObservation.source_record_id.is_(None),
                NutritionSourceObservation.observation_fingerprint == observation_fingerprint,
            ]
        )
    else:
        identity_filters.extend(
            [
                NutritionSourceObservation.source_record_id == source_record_id,
                NutritionSourceObservation.source_revision == source_revision,
                NutritionSourceObservation.observation_fingerprint == observation_fingerprint,
            ]
        )
    return db.scalar(select(NutritionSourceObservation).where(*identity_filters))


def _latest_source_observation(
    db: Session,
    *,
    user_id: UUID,
    source_instance_id: UUID,
    provider_key: str,
    source_namespace: str,
    source_record_id: str,
) -> NutritionSourceObservation | None:
    return db.scalar(
        select(NutritionSourceObservation)
        .where(
            NutritionSourceObservation.user_id == user_id,
            NutritionSourceObservation.source_instance_id == source_instance_id,
            NutritionSourceObservation.provider_key == provider_key,
            NutritionSourceObservation.source_namespace == source_namespace,
            NutritionSourceObservation.source_record_id == source_record_id,
        )
        .order_by(NutritionSourceObservation.source_revision.desc())
        .limit(1)
    )


def get_or_create_source_observation(
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
    _validate_ingestion_run(
        db,
        user_id=user_id,
        ingestion_run_id=ingestion_run_id,
        provider_key=provider_key,
        source_instance_id=source_instance_id,
    )
    existing = get_source_observation(
        db,
        user_id=user_id,
        source_instance_id=source_instance_id,
        provider_key=provider_key,
        source_namespace=source_namespace,
        source_record_id=source_record_id,
        source_revision=source_revision,
        observation_fingerprint=observation_fingerprint,
    )
    if existing is not None and existing.observation_fingerprint == observation_fingerprint:
        return existing
    if source_record_id is not None:
        latest = _latest_source_observation(
            db,
            user_id=user_id,
            source_instance_id=source_instance_id,
            provider_key=provider_key,
            source_namespace=source_namespace,
            source_record_id=source_record_id,
        )
        if latest is not None:
            if latest.observation_fingerprint == observation_fingerprint:
                return latest
            source_revision = latest.source_revision + 1
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
    """Backward-compatible name for idempotent source observation persistence."""

    return get_or_create_source_observation(
        db,
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


def get_or_create_external_identity(
    db: Session,
    *,
    user_id: UUID,
    provider_key: str,
    namespace: str,
    identity_value: str,
    identity_kind: str,
    source_instance_id: UUID,
    provider_metadata: dict[str, Any] | None = None,
    last_seen_at: datetime | None = None,
) -> NutritionExternalIdentity:
    validate_source_instance(
        db,
        user_id=user_id,
        provider_key=provider_key,
        source_instance_id=source_instance_id,
    )
    if not provider_key or not namespace or not identity_value or not identity_kind:
        raise ValueError("external identity provider, namespace, value, and kind are required")
    identity = db.scalar(
        select(NutritionExternalIdentity)
        .where(
            NutritionExternalIdentity.user_id == user_id,
            NutritionExternalIdentity.provider_key == provider_key,
            NutritionExternalIdentity.namespace == namespace,
            NutritionExternalIdentity.identity_value == identity_value,
        )
        .with_for_update()
    )
    if identity is None:
        identity = NutritionExternalIdentity(
            user_id=user_id,
            provider_key=provider_key,
            namespace=namespace,
            identity_value=identity_value,
            identity_kind=identity_kind,
            provider_metadata=dict(provider_metadata or {}),
        )
        if last_seen_at is not None:
            identity.last_seen_at = last_seen_at
        db.add(identity)
        db.flush()
        return identity
    if provider_metadata is not None:
        identity.provider_metadata = dict(provider_metadata)
    if last_seen_at is not None:
        identity.last_seen_at = last_seen_at
    db.flush()
    return identity


def get_or_create_food_profile(
    db: Session,
    *,
    user_id: UUID,
    provider_key: str,
    source_instance_id: UUID,
    external_identity_id: UUID,
    profile_status: str = "active",
) -> NutritionFoodProfile:
    validate_source_instance(
        db,
        user_id=user_id,
        provider_key=provider_key,
        source_instance_id=source_instance_id,
    )
    identity = db.scalar(
        select(NutritionExternalIdentity).where(
            NutritionExternalIdentity.id == external_identity_id,
            NutritionExternalIdentity.user_id == user_id,
        )
    )
    if identity is None:
        raise ValueError("external identity must belong to the same user")
    if identity.provider_key != provider_key:
        raise ValueError("external identity provider must match the food profile")
    link = current_identity_link(db, user_id, external_identity_id, "profile_identity")
    if link is not None:
        profile = db.scalar(
            select(NutritionFoodProfile).where(
                NutritionFoodProfile.id == link.food_profile_id,
                NutritionFoodProfile.user_id == user_id,
            )
        )
        if profile is None:
            raise ValueError("food profile link must belong to the same user")
        if profile.provider_key != provider_key or profile.source_instance_id != source_instance_id:
            raise ValueError("food profile source instance must match")
        return profile
    profile = NutritionFoodProfile(
        user_id=user_id,
        provider_key=provider_key,
        source_instance_id=source_instance_id,
        profile_status=profile_status,
    )
    db.add(profile)
    db.flush()
    get_or_create_identity_link(
        db,
        user_id=user_id,
        external_identity_id=external_identity_id,
        link_role="profile_identity",
        food_profile_id=profile.id,
    )
    return profile


def _validate_content_hash(value: str) -> str:
    if len(value) != 64 or value.lower() != value or any(char not in "0123456789abcdef" for char in value):
        raise ValueError("content_hash must be a lowercase SHA-256 value")
    return value


def set_current_food_snapshot(
    db: Session,
    *,
    user_id: UUID,
    food_profile_id: UUID,
    food_snapshot_id: UUID,
) -> NutritionFoodProfile:
    profile = db.scalar(
        select(NutritionFoodProfile)
        .where(
            NutritionFoodProfile.id == food_profile_id,
            NutritionFoodProfile.user_id == user_id,
        )
        .with_for_update()
    )
    if profile is None:
        raise ValueError("food profile must belong to the same user")
    snapshot = db.scalar(
        select(NutritionFoodSnapshot).where(
            NutritionFoodSnapshot.id == food_snapshot_id,
            NutritionFoodSnapshot.user_id == user_id,
            NutritionFoodSnapshot.food_profile_id == food_profile_id,
        )
    )
    if snapshot is None:
        raise ValueError("food snapshot must belong to the same user and profile")
    profile.current_snapshot_id = snapshot.id
    db.flush()
    return profile


def get_or_create_food_snapshot(
    db: Session,
    *,
    user_id: UUID,
    food_profile_id: UUID,
    source_observation_id: UUID,
    content_hash: str,
    provider_revision: str | None = None,
    name: str | None = None,
    producer: str | None = None,
    category: str | None = None,
    base_unit: str | None = None,
    language: str | None = None,
    is_verified: bool | None = None,
    is_private: bool | None = None,
    is_deleted: bool | None = None,
    provider_updated_at: datetime | None = None,
    provider_metadata: dict[str, Any] | None = None,
) -> NutritionFoodSnapshot:
    _validate_content_hash(content_hash)
    profile = db.scalar(
        select(NutritionFoodProfile).where(
            NutritionFoodProfile.id == food_profile_id,
            NutritionFoodProfile.user_id == user_id,
        )
    )
    if profile is None:
        raise ValueError("food profile must belong to the same user")
    observation = db.scalar(
        select(NutritionSourceObservation).where(
            NutritionSourceObservation.id == source_observation_id,
            NutritionSourceObservation.user_id == user_id,
        )
    )
    if observation is None:
        raise ValueError("source observation must belong to the same user")
    if (
        observation.provider_key != profile.provider_key
        or observation.source_instance_id != profile.source_instance_id
    ):
        raise ValueError("food snapshot source must match the food profile")
    snapshot = db.scalar(
        select(NutritionFoodSnapshot)
        .where(
            NutritionFoodSnapshot.user_id == user_id,
            NutritionFoodSnapshot.food_profile_id == food_profile_id,
            NutritionFoodSnapshot.content_hash == content_hash,
        )
        .with_for_update()
    )
    created = snapshot is None
    if created:
        snapshot = NutritionFoodSnapshot(
            user_id=user_id,
            food_profile_id=food_profile_id,
            source_observation_id=source_observation_id,
            provider_revision=provider_revision,
            content_hash=content_hash,
            name=name,
            producer=producer,
            category=category,
            base_unit=base_unit,
            language=language,
            is_verified=is_verified,
            is_private=is_private,
            is_deleted=is_deleted,
            provider_updated_at=provider_updated_at,
            provider_metadata=dict(provider_metadata or {}),
        )
        db.add(snapshot)
        db.flush()
    if created or profile.current_snapshot_id is None:
        set_current_food_snapshot(
            db,
            user_id=user_id,
            food_profile_id=food_profile_id,
            food_snapshot_id=snapshot.id,
        )
    return snapshot


def get_current_consumption_event(
    db: Session,
    *,
    user_id: UUID,
    provider_key: str,
    source_instance_id: UUID,
    logical_event_key: str,
) -> NutritionConsumptionEvent | None:
    validate_source_instance(
        db,
        user_id=user_id,
        provider_key=provider_key,
        source_instance_id=source_instance_id,
    )
    return db.scalar(
        select(NutritionConsumptionEvent)
        .where(
            NutritionConsumptionEvent.user_id == user_id,
            NutritionConsumptionEvent.provider_key == provider_key,
            NutritionConsumptionEvent.source_instance_id == source_instance_id,
            NutritionConsumptionEvent.logical_event_key == logical_event_key,
        )
        .order_by(NutritionConsumptionEvent.revision.desc())
        .limit(1)
    )


def _event_content_matches(
    latest: NutritionConsumptionEvent,
    *,
    observation: NutritionSourceObservation,
    content_hash: str | None,
    provider_metadata: dict[str, Any],
    latest_source_fingerprint: str | None,
    event_kind: str,
    food_snapshot_id: UUID | None,
    provider_civil_datetime: datetime | None,
    provider_timezone: str | None,
    canonical_start_at: datetime | None,
    canonical_end_at: datetime | None,
    local_date: date | None,
    daytime: str | None,
    amount: Decimal | None,
    amount_unit: str | None,
    presence_state: str,
    coverage_state: str,
    resolution_state: str,
    lineage_state: str,
) -> bool:
    latest_hash = (latest.provider_metadata or {}).get("content_hash")
    requested_hash = content_hash or provider_metadata.get("content_hash")
    if requested_hash is not None and latest_hash is not None:
        if requested_hash != latest_hash:
            return False
    elif requested_hash is not None and latest_hash is None:
        return False
    if latest.source_observation_id != observation.id and latest_source_fingerprint != observation.observation_fingerprint:
        return False
    comparisons = (
        ("event_kind", event_kind),
        ("food_snapshot_id", food_snapshot_id),
        ("provider_civil_datetime", provider_civil_datetime),
        ("provider_timezone", provider_timezone),
        ("canonical_start_at", canonical_start_at),
        ("canonical_end_at", canonical_end_at),
        ("local_date", local_date),
        ("daytime", daytime),
        ("amount", amount),
        ("amount_unit", amount_unit),
        ("presence_state", presence_state),
        ("coverage_state", coverage_state),
        ("resolution_state", resolution_state),
        ("lineage_state", lineage_state),
    )
    return all(value is None or getattr(latest, field) == value for field, value in comparisons)

def get_or_create_consumption_event(
    db: Session,
    *,
    user_id: UUID,
    source_observation_id: UUID,
    provider_key: str,
    source_instance_id: UUID,
    event_kind: str,
    logical_event_key: str,
    supersedes_event_id: UUID | None = None,
    food_snapshot_id: UUID | None = None,
    provider_civil_datetime: datetime | None = None,
    provider_timezone: str | None = None,
    canonical_start_at: datetime | None = None,
    canonical_end_at: datetime | None = None,
    local_date: date | None = None,
    daytime: str | None = None,
    amount: Decimal | None = None,
    amount_unit: str | None = None,
    presence_state: str,
    coverage_state: str,
    resolution_state: str,
    lineage_state: str,
    content_hash: str | None = None,
    provider_metadata: dict[str, Any] | None = None,
) -> NutritionConsumptionEvent:
    validate_source_instance(
        db,
        user_id=user_id,
        provider_key=provider_key,
        source_instance_id=source_instance_id,
    )
    if not logical_event_key:
        raise ValueError("logical_event_key must be non-empty")
    observation = db.scalar(
        select(NutritionSourceObservation).where(
            NutritionSourceObservation.id == source_observation_id,
            NutritionSourceObservation.user_id == user_id,
        )
    )
    if observation is None:
        raise ValueError("source observation must belong to the same user")
    if observation.provider_key != provider_key or observation.source_instance_id != source_instance_id:
        raise ValueError("source observation provider and source instance must match")
    if food_snapshot_id is not None:
        snapshot = db.scalar(
            select(NutritionFoodSnapshot).where(
                NutritionFoodSnapshot.id == food_snapshot_id,
                NutritionFoodSnapshot.user_id == user_id,
            )
        )
        if snapshot is None:
            raise ValueError("food snapshot must belong to the same user")
        profile = db.scalar(
            select(NutritionFoodProfile).where(
                NutritionFoodProfile.id == snapshot.food_profile_id,
                NutritionFoodProfile.user_id == user_id,
            )
        )
        if profile is None:
            raise ValueError("food snapshot profile must belong to the same user")
        if profile.provider_key != provider_key or profile.source_instance_id != source_instance_id:
            raise ValueError("food snapshot source instance must match")
    latest = get_current_consumption_event(
        db,
        user_id=user_id,
        provider_key=provider_key,
        source_instance_id=source_instance_id,
        logical_event_key=logical_event_key,
    )
    requested_metadata = dict(provider_metadata or {})
    if content_hash is not None:
        _validate_content_hash(content_hash)
        requested_metadata["content_hash"] = content_hash
    latest_source_fingerprint = None
    if latest is not None:
        latest_source_fingerprint = db.scalar(
            select(NutritionSourceObservation.observation_fingerprint).where(
                NutritionSourceObservation.id == latest.source_observation_id,
                NutritionSourceObservation.user_id == user_id,
            )
        )
    supersedes = None
    if supersedes_event_id is not None:
        supersedes = db.scalar(
            select(NutritionConsumptionEvent).where(
                NutritionConsumptionEvent.id == supersedes_event_id,
                NutritionConsumptionEvent.user_id == user_id,
                NutritionConsumptionEvent.provider_key == provider_key,
                NutritionConsumptionEvent.source_instance_id == source_instance_id,
                NutritionConsumptionEvent.logical_event_key == logical_event_key,
            )
        )
        if supersedes is None:
            raise ValueError("superseded event must belong to the same user and logical event")
        if latest is not None and supersedes.id != latest.id:
            raise ValueError("superseded event must be the current revision")
    if latest is not None and _event_content_matches(
        latest,
        observation=observation,
        content_hash=content_hash,
        provider_metadata=requested_metadata,
        latest_source_fingerprint=latest_source_fingerprint,
        event_kind=event_kind,
        food_snapshot_id=food_snapshot_id,
        provider_civil_datetime=provider_civil_datetime,
        provider_timezone=provider_timezone,
        canonical_start_at=canonical_start_at,
        canonical_end_at=canonical_end_at,
        local_date=local_date,
        daytime=daytime,
        amount=amount,
        amount_unit=amount_unit,
        presence_state=presence_state,
        coverage_state=coverage_state,
        resolution_state=resolution_state,
        lineage_state=lineage_state,
    ):
        return latest
    revision = 1 if latest is None else latest.revision + 1
    if supersedes is None:
        supersedes = latest
    if supersedes is not None:
        revision = supersedes.revision + 1
    event = NutritionConsumptionEvent(
        user_id=user_id,
        source_observation_id=source_observation_id,
        provider_key=provider_key,
        source_instance_id=source_instance_id,
        event_kind=event_kind,
        logical_event_key=logical_event_key,
        supersedes_event_id=supersedes.id if supersedes is not None else None,
        revision=revision,
        supersedes_revision=supersedes.revision if supersedes is not None else None,
        food_snapshot_id=food_snapshot_id,
        provider_civil_datetime=provider_civil_datetime,
        provider_timezone=provider_timezone,
        canonical_start_at=canonical_start_at,
        canonical_end_at=canonical_end_at,
        local_date=local_date,
        daytime=daytime,
        amount=amount,
        amount_unit=amount_unit,
        presence_state=presence_state,
        coverage_state=coverage_state,
        resolution_state=resolution_state,
        lineage_state=lineage_state,
        provider_metadata=requested_metadata,
    )
    db.add(event)
    db.flush()
    return event



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
