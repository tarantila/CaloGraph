"""Read-only Apple Health daily nutrient resolution."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.nutrition.enums import (
    ConsumptionEventKind,
    CoverageState,
    LineageState,
    ObservationKind,
    ObservationRole,
    PresenceState,
    ResolutionState,
)
from app.nutrition.models import (
    NutritionConsumptionEvent,
    NutritionExternalIdentityLink,
    NutritionFieldObservation,
    NutritionSourceObservation,
    NutritionSourceTombstone,
)
from app.nutrition.resolution.contracts import (
    MetricContribution,
    ProviderCandidate,
    build_event_candidate,
)
from app.nutrition.resolution.metrics import CANONICAL_NUTRITION_METRICS, canonical_unit
from app.nutrition.resolution.reasons import EvidenceKind, ReasonCode
from app.services.apple_health_nutrition_ingestion import apple_health_source_instance_id

_PROVIDER = "apple_health"
_SOURCE_NAMESPACE = "apple_health.food_correlation"


def _enum[EnumValue](enum_type: type[EnumValue], value: str | None, fallback: EnumValue) -> EnumValue:
    try:
        return enum_type(value)  # type: ignore[call-arg]
    except (TypeError, ValueError):
        return fallback


def _missing_contribution(
    evidence_id: UUID,
    source_observation_id: UUID,
    metric_key: str,
    *,
    resolution_state: ResolutionState = ResolutionState.UNRESOLVED,
    lineage_state: LineageState = LineageState.UNKNOWN,
    reason_code: ReasonCode | None = None,
    evidence_kind: EvidenceKind = EvidenceKind.CONSUMPTION_EVENT,
) -> MetricContribution:
    return MetricContribution(
        evidence_id=evidence_id,
        source_observation_id=source_observation_id,
        metric_key=metric_key,
        value=None,
        unit=None,
        presence_state=PresenceState.MISSING,
        resolution_state=resolution_state,
        lineage_state=lineage_state,
        evidence_kind=evidence_kind,
        reason_code=reason_code,
    )


def _field_contribution(field: NutritionFieldObservation, metric_key: str) -> MetricContribution:
    expected_unit = canonical_unit(metric_key)
    presence = _enum(PresenceState, field.presence_state, PresenceState.UNKNOWN)
    resolution = _enum(ResolutionState, field.resolution_state, ResolutionState.UNRESOLVED)
    lineage = _enum(LineageState, field.lineage_state, LineageState.UNKNOWN)
    value = field.canonical_value
    unit = field.canonical_unit
    reason: ReasonCode | None = None

    if value is None:
        presence = PresenceState.MISSING
        unit = None
    elif not isinstance(value, Decimal) or not value.is_finite() or unit != expected_unit:
        value = None
        unit = None
        presence = PresenceState.MISSING
        resolution = ResolutionState.UNRESOLVED
        reason = ReasonCode.INVALID_UNIT
    elif (value == Decimal("0") and presence is not PresenceState.EXPLICIT_ZERO) or (value != Decimal("0") and presence is not PresenceState.SUPPLIED):
        value = None
        presence = PresenceState.MISSING
        unit = None
        resolution = ResolutionState.UNRESOLVED
        reason = ReasonCode.UNRESOLVED_PRODUCT

    return MetricContribution(
        evidence_id=field.id,
        source_observation_id=field.source_observation_id,
        metric_key=metric_key,
        value=value,
        unit=unit,
        presence_state=presence,
        resolution_state=resolution,
        lineage_state=lineage,
        evidence_kind=EvidenceKind.FIELD_OBSERVATION,
        reason_code=reason,
    )


def _source_tombstoned(
    source: NutritionSourceObservation | None,
    tombstones: Sequence[NutritionSourceTombstone],
) -> bool:
    if source is None:
        return True
    return any(
        tombstone.source_observation_id == source.id
        or (
            tombstone.source_observation_id is None
            and tombstone.source_namespace == source.source_namespace
            and tombstone.source_record_id is not None
            and tombstone.source_record_id == source.source_record_id
        )
        for tombstone in tombstones
    )


def _event_tombstoned(
    event: NutritionConsumptionEvent,
    source: NutritionSourceObservation | None,
    tombstones: Sequence[NutritionSourceTombstone],
    identity_ids: dict[UUID, frozenset[UUID]],
) -> bool:
    if _source_tombstoned(source, tombstones):
        return True
    event_identity_ids = identity_ids.get(event.id, frozenset())
    return any(
        tombstone.source_namespace == _SOURCE_NAMESPACE
        and tombstone.external_identity_id in event_identity_ids
        for tombstone in tombstones
    )


def _revision_chain_is_valid(
    events: Sequence[NutritionConsumptionEvent],
    current: NutritionConsumptionEvent,
) -> bool:
    by_id = {event.id: event for event in events}
    cursor = current
    visited: set[UUID] = set()
    while cursor.revision > 1:
        if cursor.id in visited:
            return False
        visited.add(cursor.id)
        if cursor.supersedes_event_id is None or cursor.supersedes_revision != cursor.revision - 1:
            return False
        previous = by_id.get(cursor.supersedes_event_id)
        if previous is None or previous.revision != cursor.revision - 1:
            return False
        cursor = previous
    return cursor.supersedes_event_id is None and cursor.supersedes_revision is None


def _current_event_groups(
    events: Sequence[NutritionConsumptionEvent],
) -> tuple[tuple[NutritionConsumptionEvent, ...], ...]:
    grouped: dict[tuple[str, str | UUID], list[NutritionConsumptionEvent]] = defaultdict(list)
    for event in events:
        if event.logical_event_key:
            key: tuple[str, str | UUID] = ("logical", event.logical_event_key)
        else:
            key = ("id", event.id)
        grouped[key].append(event)

    current: list[tuple[NutritionConsumptionEvent, ...]] = []
    for key in sorted(grouped, key=lambda item: (item[0], str(item[1]))):
        candidates = grouped[key]
        highest_revision = max(event.revision for event in candidates)
        current.append(tuple(event for event in candidates if event.revision == highest_revision))
    return tuple(current)


def _query_events_for_day(
    db: Session,
    *,
    user_id: UUID,
    source_instance_id: UUID,
    local_date: date,
) -> tuple[NutritionConsumptionEvent, ...]:
    day_events = tuple(
        db.scalars(
            select(NutritionConsumptionEvent).where(
                NutritionConsumptionEvent.user_id == user_id,
                NutritionConsumptionEvent.provider_key == _PROVIDER,
                NutritionConsumptionEvent.source_instance_id == source_instance_id,
                NutritionConsumptionEvent.local_date == local_date,
            )
        ).all()
    )
    logical_keys = tuple(
        sorted(
            {
                event.logical_event_key
                for event in day_events
                if event.logical_event_key is not None
            }
        )
    )
    if not logical_keys:
        return day_events
    return tuple(
        db.scalars(
            select(NutritionConsumptionEvent).where(
                NutritionConsumptionEvent.user_id == user_id,
                NutritionConsumptionEvent.provider_key == _PROVIDER,
                NutritionConsumptionEvent.source_instance_id == source_instance_id,
                NutritionConsumptionEvent.logical_event_key.in_(logical_keys),
            )
        ).all()
    )


def _identity_links(
    db: Session,
    *,
    user_id: UUID,
    event_ids: Sequence[UUID],
) -> dict[UUID, frozenset[UUID]]:
    if not event_ids:
        return {}
    links = db.scalars(
        select(NutritionExternalIdentityLink).where(
            NutritionExternalIdentityLink.user_id == user_id,
            NutritionExternalIdentityLink.consumption_event_id.in_(event_ids),
        )
    ).all()
    grouped: dict[UUID, set[UUID]] = defaultdict(set)
    for link in links:
        if link.consumption_event_id is not None:
            grouped[link.consumption_event_id].add(link.external_identity_id)
    return {event_id: frozenset(identity_ids) for event_id, identity_ids in grouped.items()}


def _event_contributions(
    db: Session,
    *,
    events: Sequence[NutritionConsumptionEvent],
    user_id: UUID,
    source_instance_id: UUID,
    local_date: date,
    metric_key: str,
) -> list[MetricContribution]:
    source_ids = {event.source_observation_id for event in events}
    sources = (
        {
            source.id: source
            for source in db.scalars(
                select(NutritionSourceObservation).where(
                    NutritionSourceObservation.user_id == user_id,
                    NutritionSourceObservation.id.in_(source_ids),
                )
            ).all()
        }
        if source_ids
        else {}
    )
    field_rows = (
        db.scalars(
            select(NutritionFieldObservation).where(
                NutritionFieldObservation.user_id == user_id,
                NutritionFieldObservation.source_observation_id.in_(source_ids),
                NutritionFieldObservation.metric_key == metric_key,
                NutritionFieldObservation.observation_role == ObservationRole.CANONICAL.value,
            ).order_by(NutritionFieldObservation.provider_field_path, NutritionFieldObservation.id)
        ).all()
        if source_ids
        else []
    )
    fields: dict[UUID, list[NutritionFieldObservation]] = defaultdict(list)
    for field in field_rows:
        fields[field.source_observation_id].append(field)
    tombstones = tuple(
        db.scalars(
            select(NutritionSourceTombstone).where(
                NutritionSourceTombstone.user_id == user_id,
                NutritionSourceTombstone.provider_key == _PROVIDER,
                NutritionSourceTombstone.source_instance_id == source_instance_id,
            )
        ).all()
    )
    identity_ids = _identity_links(db, user_id=user_id, event_ids=[event.id for event in events])
    contributions: list[MetricContribution] = []

    for revision_group in _current_event_groups(events):
        if len(revision_group) != 1:
            contributions.extend(
                _missing_contribution(
                    event.id,
                    event.source_observation_id,
                    metric_key,
                    resolution_state=ResolutionState.DUPLICATE_CANDIDATE,
                    reason_code=ReasonCode.SUMMARY_FALLBACK_DUPLICATE_CANDIDATE,
                )
                for event in revision_group
                if event.local_date == local_date
            )
            continue

        event = revision_group[0]
        if event.local_date != local_date:
            continue
        source = sources.get(event.source_observation_id)
        if _event_tombstoned(event, source, tombstones, identity_ids):
            if source is not None:
                contributions.append(
                    _missing_contribution(
                        event.id,
                        event.source_observation_id,
                        metric_key,
                        resolution_state=ResolutionState.RESOLVED,
                        lineage_state=LineageState.CONFIRMED,
                        reason_code=ReasonCode.UNRESOLVED_PRODUCT,
                    )
                )
            continue
        if (
            source is None
            or source.user_id != user_id
            or source.provider_key != _PROVIDER
            or source.source_instance_id != source_instance_id
            or source.source_namespace != _SOURCE_NAMESPACE
            or source.observation_kind != ObservationKind.CONSUMPTION_EVENT.value
            or source.local_date != local_date
            or event.event_kind != ConsumptionEventKind.PRODUCT.value
            or not _revision_chain_is_valid(events, event)
        ):
            continue

        event_state = _enum(ResolutionState, event.resolution_state, ResolutionState.UNRESOLVED)
        event_lineage = _enum(LineageState, event.lineage_state, LineageState.UNKNOWN)
        if event_state is not ResolutionState.RESOLVED:
            contributions.append(
                _missing_contribution(
                    event.id,
                    event.source_observation_id,
                    metric_key,
                    resolution_state=event_state,
                    lineage_state=event_lineage,
                    reason_code=ReasonCode.UNRESOLVED_PRODUCT,
                )
            )
            continue

        event_fields = fields.get(event.source_observation_id, [])
        if len(event_fields) != 1:
            if event_fields:
                contributions.extend(
                    _missing_contribution(
                        field.id,
                        field.source_observation_id,
                        metric_key,
                        resolution_state=ResolutionState.DUPLICATE_CANDIDATE,
                        lineage_state=event_lineage,
                        reason_code=ReasonCode.SUMMARY_FALLBACK_DUPLICATE_CANDIDATE,
                        evidence_kind=EvidenceKind.FIELD_OBSERVATION,
                    )
                    for field in event_fields
                )
            else:
                contributions.append(
                    _missing_contribution(
                        event.id,
                        event.source_observation_id,
                        metric_key,
                        resolution_state=ResolutionState.UNRESOLVED,
                        lineage_state=event_lineage,
                        reason_code=ReasonCode.UNRESOLVED_PRODUCT,
                    )
                )
            continue

        contribution = _field_contribution(event_fields[0], metric_key)
        contributions.append(contribution)
        if (
            contribution.value is None
            or event.coverage_state != CoverageState.COMPLETE.value
            or source.coverage_state != CoverageState.COMPLETE.value
        ):
            contributions.append(
                _missing_contribution(
                    event.id,
                    event.source_observation_id,
                    metric_key,
                    resolution_state=(
                        ResolutionState.UNRESOLVED
                        if contribution.value is None
                        else ResolutionState.RESOLVED
                    ),
                    lineage_state=contribution.lineage_state,
                    reason_code=ReasonCode.PARTIAL_EVENTS_NO_SUMMARY,
                )
            )
    return contributions


def _empty_candidate(*, user_id: UUID, local_date: date, metric_key: str) -> ProviderCandidate:
    return ProviderCandidate(
        provider_key=_PROVIDER,
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


def resolve_apple_health_metric(
    db: Session,
    *,
    user_id: UUID,
    source_instance_id: UUID,
    local_date: date,
    metric_key: str,
) -> ProviderCandidate:
    """Resolve one Apple canonical nutrient for one user/source/date scope."""
    if metric_key not in CANONICAL_NUTRITION_METRICS:
        raise ValueError("metric_key must be a canonical nutrition metric")
    expected_source_instance_id = apple_health_source_instance_id(user_id)
    if source_instance_id != expected_source_instance_id:
        raise ValueError("source_instance_id must belong to the same user")

    events = _query_events_for_day(
        db,
        user_id=user_id,
        source_instance_id=source_instance_id,
        local_date=local_date,
    )
    if not events:
        return _empty_candidate(user_id=user_id, local_date=local_date, metric_key=metric_key)
    event_candidate = build_event_candidate(
        provider_key=_PROVIDER,
        user_id=user_id,
        local_date=local_date,
        metric_key=metric_key,
        contributions=_event_contributions(
            db,
            events=events,
            user_id=user_id,
            source_instance_id=source_instance_id,
            local_date=local_date,
            metric_key=metric_key,
        ),
        event_set_known=True,
    )
    return ProviderCandidate(
        provider_key=event_candidate.provider_key,
        user_id=event_candidate.user_id,
        local_date=event_candidate.local_date,
        metric_key=event_candidate.metric_key,
        value=event_candidate.value,
        unit=event_candidate.unit,
        selected_granularity=event_candidate.selected_granularity,
        presence_state=event_candidate.presence_state,
        coverage_state=event_candidate.coverage_state,
        resolution_state=event_candidate.resolution_state,
        lineage_state=event_candidate.lineage_state,
        source_lineage=event_candidate.source_lineage,
        reason_code=event_candidate.reason_code,
        diagnostic_codes=event_candidate.diagnostic_codes,
    )


__all__ = ["resolve_apple_health_metric"]
