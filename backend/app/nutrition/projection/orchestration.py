from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime
from typing import Final
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.models import YazioConnection
from app.nutrition.enums import ObservationKind
from app.nutrition.models import (
    NutritionConsumptionEvent,
    NutritionExternalIdentityLink,
    NutritionFieldObservation,
    NutritionFoodSnapshot,
    NutritionSourceObservation,
    NutritionSourceTombstone,
)
from app.nutrition.resolution.contracts import MetricContribution, ProviderCandidate
from app.nutrition.resolution.metrics import CANONICAL_METRICS
from app.nutrition.resolution.providers import collect_provider_candidates
from app.nutrition.resolution.reasons import EvidenceKind
from app.source_priority.application import get_effective_policy_snapshot
from app.source_priority.contracts import PriorityPolicySnapshot, PrioritySelection
from app.source_priority.selection import select_by_source_priority

from .contracts import (
    METRIC_REGISTRY_VERSION,
    PROJECTION_ALGORITHM_VERSION,
    WATERMARK_FORMAT_VERSION,
    DailyProjectionBuildInput,
    ProjectionInputManifest,
    ProjectionPersistenceResult,
)
from .persistence import persist_daily_projection
from .tokens import (
    ConsumptionEventToken,
    FieldObservationToken,
    FoodSnapshotToken,
    IdentityLinkToken,
    SourceObservationToken,
    TechnicalEvidenceToken,
    TombstoneToken,
)

_PROVIDER_KEY: Final = "yazio"
_PROVIDER_KEYS: Final = (_PROVIDER_KEY,)
_MAX_ATTEMPTS: Final = 3
_PROJECTION_VERSION_CONSTRAINT: Final = "uq_nutrition_projections_user_date_version"


class NutritionProjectionTransactionError(RuntimeError):
    """B6 requires a session without a pre-existing transaction."""


class NutritionProjectionConcurrencyError(RuntimeError):
    """A bounded B6 whole-transaction retry budget was exhausted."""


def _normalize_policy_at(policy_at: datetime) -> datetime:
    if policy_at.tzinfo is None or policy_at.utcoffset() is None:
        raise ValueError("policy_at must be timezone-aware")
    return policy_at.astimezone(UTC)


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _source_instance_id(
    db: Session,
    *,
    user_id: UUID,
    explicit_source_instance_id: UUID | None,
) -> UUID:
    source_instance_id = explicit_source_instance_id or db.scalar(
        select(YazioConnection.id).where(YazioConnection.user_id == user_id)
    )
    if source_instance_id is None:
        raise ValueError("YAZIO source instance is not configured")
    owned = db.scalar(
        select(YazioConnection.id).where(
            YazioConnection.id == source_instance_id,
            YazioConnection.user_id == user_id,
        )
    )
    if owned is None:
        raise ValueError("source_instance_id must belong to the same user")
    return source_instance_id


def _collect_selections(
    db: Session,
    *,
    user_id: UUID,
    source_instance_id: UUID,
    local_date: date,
    policy: PriorityPolicySnapshot | None,
) -> tuple[PrioritySelection, ...]:
    selections: list[PrioritySelection] = []
    for metric_key in CANONICAL_METRICS:
        candidates = collect_provider_candidates(
            db,
            provider_keys=_PROVIDER_KEYS,
            user_id=user_id,
            source_instance_id=source_instance_id,
            local_date=local_date,
            metric_key=metric_key,
        )
        selections.append(
            select_by_source_priority(
                user_id=user_id,
                local_date=local_date,
                metric_key=metric_key,
                candidates=candidates,
                policy=policy,
            )
        )
    return tuple(selections)


def _path_contributions(
    selections: Iterable[PrioritySelection],
) -> tuple[MetricContribution, ...]:
    contributions: list[MetricContribution] = []
    seen: set[tuple[UUID, UUID, str]] = set()
    for selection in selections:
        for disposition in selection.dispositions:
            if disposition.rule_id is None or disposition.candidate is None:
                continue
            candidate: ProviderCandidate = disposition.candidate
            candidate_evidence = (*candidate.source_lineage, *candidate.diagnostic_evidence)
            for contribution in candidate_evidence:
                identity = (contribution.evidence_id, contribution.source_observation_id, contribution.metric_key)
                if identity not in seen:
                    seen.add(identity)
                    contributions.append(contribution)
    return tuple(contributions)


def _load_relevant_tokens(
    db: Session,
    *,
    user_id: UUID,
    local_date: date,
    source_instance_id: UUID,
    contributions: tuple[MetricContribution, ...],
) -> tuple[TechnicalEvidenceToken, ...]:
    source_ids = {contribution.source_observation_id for contribution in contributions}
    event_ids = {
        contribution.evidence_id
        for contribution in contributions
        if contribution.evidence_kind is EvidenceKind.CONSUMPTION_EVENT
    }
    field_ids = {
        contribution.evidence_id
        for contribution in contributions
        if contribution.evidence_kind is EvidenceKind.FIELD_OBSERVATION
    }
    source_rows = (
        list(
            db.scalars(
                select(NutritionSourceObservation).where(
                    NutritionSourceObservation.user_id == user_id,
                    NutritionSourceObservation.id.in_(source_ids),
                )
            )
        )
        if source_ids
        else []
    )
    source_by_id = {source.id: source for source in source_rows}
    if set(source_by_id) != source_ids:
        raise ValueError("manifest source evidence is outside the user scope")
    if any(
        source.source_instance_id != source_instance_id
        or source.provider_key != _PROVIDER_KEY
        or source.local_date != local_date
        for source in source_by_id.values()
    ):
        raise ValueError("manifest source evidence is outside the date/provider scope")

    event_filters = []
    if event_ids:
        event_filters.append(NutritionConsumptionEvent.id.in_(event_ids))
    if source_ids:
        event_filters.append(NutritionConsumptionEvent.source_observation_id.in_(source_ids))
    event_rows = (
        list(
            db.scalars(
                select(NutritionConsumptionEvent).where(
                    NutritionConsumptionEvent.user_id == user_id,
                    NutritionConsumptionEvent.provider_key == _PROVIDER_KEY,
                    NutritionConsumptionEvent.source_instance_id == source_instance_id,
                    NutritionConsumptionEvent.local_date == local_date,
                    or_(*event_filters),
                )
            )
        )
        if event_filters
        else []
    )
    event_keys = {
        event.logical_event_key
        for event in event_rows
        if event.logical_event_key is not None
    }
    if event_keys:
        event_rows = list(
            db.scalars(
                select(NutritionConsumptionEvent).where(
                    NutritionConsumptionEvent.user_id == user_id,
                    NutritionConsumptionEvent.provider_key == _PROVIDER_KEY,
                    NutritionConsumptionEvent.source_instance_id == source_instance_id,
                    NutritionConsumptionEvent.local_date == local_date,
                    NutritionConsumptionEvent.logical_event_key.in_(event_keys),
                )
            )
        )
    events_by_id = {event.id: event for event in event_rows}
    if not event_ids.issubset(events_by_id):
        raise ValueError("manifest event evidence is outside the source scope")
    source_ids.update(event.source_observation_id for event in events_by_id.values())

    fields = (
        list(
            db.scalars(
                select(NutritionFieldObservation).where(
                    NutritionFieldObservation.user_id == user_id,
                    NutritionFieldObservation.id.in_(field_ids),
                )
            )
        )
        if field_ids
        else []
    )
    parent_ids = {
        field.derived_from_field_observation_id
        for field in fields
        if field.derived_from_field_observation_id is not None
    }
    parents = (
        list(
            db.scalars(
                select(NutritionFieldObservation).where(
                    NutritionFieldObservation.user_id == user_id,
                    NutritionFieldObservation.id.in_(parent_ids),
                )
            )
        )
        if parent_ids
        else []
    )
    fields_by_id = {field.id: field for field in (*fields, *parents)}
    if not field_ids.issubset(fields_by_id):
        raise ValueError("manifest field evidence is outside the user scope")
    source_ids.update(field.source_observation_id for field in fields_by_id.values())

    snapshot_ids = {
        event.food_snapshot_id
        for event in events_by_id.values()
        if event.food_snapshot_id is not None
    }
    snapshots = (
        list(
            db.scalars(
                select(NutritionFoodSnapshot).where(
                    NutritionFoodSnapshot.user_id == user_id,
                    NutritionFoodSnapshot.id.in_(snapshot_ids),
                )
            )
        )
        if snapshot_ids
        else []
    )
    snapshots_by_id = {snapshot.id: snapshot for snapshot in snapshots}
    if not snapshot_ids.issubset(snapshots_by_id):
        raise ValueError("manifest snapshot evidence is outside the user scope")
    source_ids.update(snapshot.source_observation_id for snapshot in snapshots)
    global_reference_source_ids = {
        snapshot.source_observation_id
        for snapshot in snapshots_by_id.values()
    }

    if source_ids != set(source_by_id):
        extra_sources = list(
            db.scalars(
                select(NutritionSourceObservation).where(
                    NutritionSourceObservation.user_id == user_id,
                    NutritionSourceObservation.id.in_(source_ids - set(source_by_id)),
                )
            )
        ) if source_ids - set(source_by_id) else []
        source_rows.extend(extra_sources)
        source_by_id = {source.id: source for source in source_rows}
    if set(source_by_id) != source_ids:
        raise ValueError("manifest derived source evidence is outside the user scope")
    if any(
        source.source_instance_id != source_instance_id
        or source.provider_key != _PROVIDER_KEY
        or (
            (
                source.id in global_reference_source_ids
                and (
                    source.observation_kind != ObservationKind.PRODUCT_PROFILE.value
                    or source.local_date is not None
                )
            )
            or (
                source.id not in global_reference_source_ids
                and source.local_date != local_date
            )
        )
        for source in source_by_id.values()
    ):
        raise ValueError("manifest derived source evidence is outside the date/provider scope")

    links = (
        list(
            db.scalars(
                select(NutritionExternalIdentityLink).where(
                    NutritionExternalIdentityLink.user_id == user_id,
                    NutritionExternalIdentityLink.consumption_event_id.in_(set(events_by_id)),
                )
            )
        )
        if events_by_id
        else []
    )
    identity_ids = {link.external_identity_id for link in links}
    tombstones = list(
        db.scalars(
            select(NutritionSourceTombstone).where(
                NutritionSourceTombstone.user_id == user_id,
                NutritionSourceTombstone.provider_key == _PROVIDER_KEY,
                NutritionSourceTombstone.source_instance_id == source_instance_id,
            )
        )
    )
    relevant_tombstones = [
        tombstone
        for tombstone in tombstones
        if tombstone.source_observation_id in source_ids
        or tombstone.external_identity_id in identity_ids
        or (
            tombstone.source_observation_id is None
            and any(
                source.source_namespace == tombstone.source_namespace
                and source.source_record_id is not None
                and source.source_record_id == tombstone.source_record_id
                for source in source_by_id.values()
            )
        )
    ]

    tokens: list[TechnicalEvidenceToken] = [
        SourceObservationToken(
            source_observation_id=source.id,
            source_revision=source.source_revision,
            observation_fingerprint=source.observation_fingerprint,
            payload_hash=source.payload_hash,
            provider_key=source.provider_key,
        )
        for source in source_by_id.values()
    ]
    tokens.extend(
        ConsumptionEventToken(
            event_id=event.id,
            revision=event.revision,
            provider_key=event.provider_key,
        )
        for event in events_by_id.values()
    )
    tokens.extend(
        FieldObservationToken(field_observation_id=field.id, provider_key=_PROVIDER_KEY)
        for field in fields_by_id.values()
    )
    tokens.extend(
        FoodSnapshotToken(
            snapshot_id=snapshot.id,
            content_hash=snapshot.content_hash,
            provider_key=_PROVIDER_KEY,
        )
        for snapshot in snapshots_by_id.values()
    )
    tokens.extend(
        IdentityLinkToken(
            link_id=link.id,
            link_revision=link.link_revision,
            provider_key=_PROVIDER_KEY,
        )
        for link in links
    )
    tokens.extend(
        TombstoneToken(
            tombstone_id=tombstone.id,
            observed_at=_utc_datetime(tombstone.observed_at),
            provider_key=_PROVIDER_KEY,
        )
        for tombstone in relevant_tombstones
    )
    return tuple(tokens)


def _build_manifest(
    db: Session,
    *,
    user_id: UUID,
    local_date: date,
    source_instance_id: UUID,
    policy: PriorityPolicySnapshot | None,
    selections: tuple[PrioritySelection, ...],
) -> ProjectionInputManifest:
    expected_rule_ids = {
        disposition.rule_id
        for selection in selections
        for disposition in selection.dispositions
        if disposition.rule_id is not None
    }
    relevant_rules = (
        tuple(rule for rule in policy.rules if rule.rule_id in expected_rule_ids)
        if policy is not None
        else ()
    )
    contributions = _path_contributions(selections)
    tokens = _load_relevant_tokens(
        db,
        user_id=user_id,
        local_date=local_date,
        source_instance_id=source_instance_id,
        contributions=contributions,
    )
    source_token_ids = {
        token.source_observation_id
        for token in tokens
        if isinstance(token, SourceObservationToken)
    }
    required_source_ids = {contribution.source_observation_id for contribution in contributions}
    if not required_source_ids.issubset(source_token_ids):
        raise ValueError("manifest is missing candidate lineage source evidence")
    return ProjectionInputManifest(
        user_id=user_id,
        local_date=local_date,
        projection_algorithm_version=PROJECTION_ALGORITHM_VERSION,
        metric_registry_version=METRIC_REGISTRY_VERSION,
        watermark_format_version=WATERMARK_FORMAT_VERSION,
        policy=policy,
        relevant_rules=relevant_rules,
        technical_evidence=tokens,
    )


def _run_attempt(
    db: Session,
    *,
    user_id: UUID,
    local_date: date,
    policy_at: datetime,
    source_instance_id: UUID | None,
) -> ProjectionPersistenceResult:
    resolved_source_instance_id = _source_instance_id(
        db,
        user_id=user_id,
        explicit_source_instance_id=source_instance_id,
    )
    policy = get_effective_policy_snapshot(db, user_id, policy_at)
    selections = _collect_selections(
        db,
        user_id=user_id,
        source_instance_id=resolved_source_instance_id,
        local_date=local_date,
        policy=policy,
    )
    manifest = _build_manifest(
        db,
        user_id=user_id,
        local_date=local_date,
        source_instance_id=resolved_source_instance_id,
        policy=policy,
        selections=selections,
    )
    return persist_daily_projection(
        db,
        build_input=DailyProjectionBuildInput(
            user_id=user_id,
            local_date=local_date,
            selections=selections,
            input_manifest=manifest,
        ),
    )


def _dbapi_sqlstate(error: BaseException) -> str | None:
    original = getattr(error, "orig", None)
    for attribute in ("sqlstate", "pgcode"):
        value = getattr(original, attribute, None)
        if isinstance(value, str):
            return value
    return None


def _dbapi_constraint_name(error: BaseException) -> str | None:
    original = getattr(error, "orig", None)
    diagnostic = getattr(original, "diag", None)
    value = getattr(diagnostic, "constraint_name", None)
    return value if isinstance(value, str) else None


def _is_retryable_database_error(db: Session, error: BaseException) -> bool:
    if db.get_bind().dialect.name != "postgresql":
        return False
    if not isinstance(error, DBAPIError):
        return False
    return _dbapi_sqlstate(error) in {"40001", "40P01"} or (
        isinstance(error, IntegrityError)
        and _dbapi_constraint_name(error) == _PROJECTION_VERSION_CONSTRAINT
    )


def _begin_attempt(db: Session) -> None:
    if db.get_bind().dialect.name == "postgresql":
        db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    else:
        db.connection()


def rebuild_nutrition_day(
    db: Session,
    *,
    user_id: UUID,
    local_date: date,
    policy_at: datetime,
    source_instance_id: UUID | None = None,
) -> ProjectionPersistenceResult:
    """Build and persist exactly one user's canonical nutrition day."""
    if db.in_transaction():
        raise NutritionProjectionTransactionError("B6 requires a clean input session")
    normalized_policy_at = _normalize_policy_at(policy_at)

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            _begin_attempt(db)
            result = _run_attempt(
                db,
                user_id=user_id,
                local_date=local_date,
                policy_at=normalized_policy_at,
                source_instance_id=source_instance_id,
            )
            db.commit()
            return result
        except Exception as error:
            db.rollback()
            if not _is_retryable_database_error(db, error):
                raise
            if attempt == _MAX_ATTEMPTS:
                raise NutritionProjectionConcurrencyError(
                    "nutrition projection concurrency retry budget exhausted"
                ) from error

    raise AssertionError("unreachable")
