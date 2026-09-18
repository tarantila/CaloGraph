"""Read-only Apple Health daily nutrient resolution."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import exists, not_, or_, select
from sqlalchemy.orm import Session, aliased

from app.micronutrients import MICRONUTRIENT_METRIC_TYPES
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
    EventReconstructionCandidate,
    MetricContribution,
    ProviderCandidate,
    build_event_candidate,
)
from app.nutrition.resolution.metrics import CANONICAL_NUTRITION_METRICS, canonical_unit
from app.nutrition.resolution.read_context import NutritionEvidenceIndex
from app.nutrition.resolution.reasons import EvidenceKind, ReasonCode
from app.services.apple_health_nutrition_ingestion import apple_health_source_instance_id

_PROVIDER = "apple_health"
_SOURCE_NAMESPACE = "apple_health.food_correlation"


@dataclass(frozen=True, slots=True)
class _AppleReadScope:
    sources: Mapping[UUID, NutritionSourceObservation]
    fields: Mapping[tuple[UUID, str], tuple[NutritionFieldObservation, ...]]
    tombstones: tuple[NutritionSourceTombstone, ...]
    identity_ids: Mapping[UUID, frozenset[UUID]]


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
    identity_ids: Mapping[UUID, frozenset[UUID]],
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
    *,
    by_id: Mapping[UUID, NutritionConsumptionEvent] | None = None,
) -> bool:
    event_by_id = {event.id: event for event in events} if by_id is None else by_id
    cursor = current
    visited: set[UUID] = set()
    while cursor.revision > 1:
        if cursor.id in visited:
            return False
        visited.add(cursor.id)
        if cursor.supersedes_event_id is None or cursor.supersedes_revision != cursor.revision - 1:
            return False
        previous = event_by_id.get(cursor.supersedes_event_id)
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


def _revision_groups_by_date(
    revision_groups: Sequence[tuple[NutritionConsumptionEvent, ...]],
) -> Mapping[date, tuple[tuple[NutritionConsumptionEvent, ...], ...]]:
    grouped: dict[date, list[tuple[NutritionConsumptionEvent, ...]]] = defaultdict(list)
    for revision_group in revision_groups:
        for local_date in {event.local_date for event in revision_group if event.local_date is not None}:
            grouped[local_date].append(revision_group)
    return {local_date: tuple(groups) for local_date, groups in grouped.items()}


def _revision_chain_validity(
    events: Sequence[NutritionConsumptionEvent],
    revision_groups: Sequence[tuple[NutritionConsumptionEvent, ...]],
) -> Mapping[UUID, bool]:
    event_by_id = {event.id: event for event in events}
    return {
        event.id: _revision_chain_is_valid(events, event, by_id=event_by_id)
        for revision_group in revision_groups
        for event in revision_group
    }




def _latest_apple_evidence_observed_at(
    scope: _AppleReadScope,
    *,
    events: Sequence[NutritionConsumptionEvent],
    start: date,
    end: date,
    revision_chain_validity: Mapping[UUID, bool],
) -> datetime | None:
    max_source_revision: dict[tuple[str, str], int] = {}
    source_ids_by_identity: dict[tuple[str, str], set[UUID]] = defaultdict(set)
    for candidate_source in scope.sources.values():
        if candidate_source.source_record_id is not None:
            identity = (candidate_source.source_namespace, candidate_source.source_record_id)
            max_source_revision[identity] = max(
                max_source_revision.get(identity, candidate_source.source_revision),
                candidate_source.source_revision,
            )
            source_ids_by_identity[identity].add(candidate_source.id)
    current_source_ids = {
        source.id
        for source in scope.sources.values()
        if source.source_record_id is None
        or source.source_revision
        == max_source_revision[(source.source_namespace, source.source_record_id)]
    }
    qualifying_field_source_ids: set[UUID] = set()
    for (source_id, _), fields in scope.fields.items():
        if any(
            field.metric_key is not None and field.metric_key in MICRONUTRIENT_METRIC_TYPES
            and field.canonical_value is not None
            and field.canonical_unit == canonical_unit(field.metric_key)
            and field.observation_role == ObservationRole.CANONICAL.value
            and field.presence_state
            in (PresenceState.SUPPLIED.value, PresenceState.EXPLICIT_ZERO.value)
            and field.coverage_state
            in (CoverageState.COMPLETE.value, CoverageState.PARTIAL.value)
            and field.resolution_state == ResolutionState.RESOLVED.value
            and field.lineage_state in (LineageState.CONFIRMED.value, LineageState.UNCERTAIN.value)
            for field in fields
        ):
            qualifying_field_source_ids.add(source_id)
    tombstoned_source_ids: set[UUID] = set()
    for tombstone in scope.tombstones:
        if tombstone.source_observation_id is not None:
            tombstoned_source_ids.add(tombstone.source_observation_id)
        elif tombstone.source_record_id is not None:
            tombstoned_source_ids.update(
                source_ids_by_identity.get(
                    (tombstone.source_namespace, tombstone.source_record_id),
                    set(),
                )
            )
    tombstoned_identity_ids = {
        tombstone.external_identity_id
        for tombstone in scope.tombstones
        if tombstone.source_namespace == _SOURCE_NAMESPACE
        and tombstone.external_identity_id is not None
    }
    tombstoned_event_ids = {
        event.id
        for event in events
        if scope.identity_ids.get(event.id, frozenset()) & tombstoned_identity_ids
    }

    observed_at: list[datetime] = []
    for revision_group in _current_event_groups(events):
        if len(revision_group) != 1:
            continue
        event = revision_group[0]
        if event.local_date is None or not start <= event.local_date <= end:
            continue
        source = scope.sources.get(event.source_observation_id)
        if source is None:
            continue
        if (
            source.user_id != event.user_id
            or source.provider_key != _PROVIDER
            or source.source_instance_id != event.source_instance_id
            or source.observation_kind != ObservationKind.CONSUMPTION_EVENT.value
            or source.source_namespace != _SOURCE_NAMESPACE
            or source.local_date != event.local_date
            or source.presence_state
            not in (PresenceState.SUPPLIED.value, PresenceState.EXPLICIT_ZERO.value)
            or source.coverage_state
            not in (CoverageState.COMPLETE.value, CoverageState.PARTIAL.value)
            or source.resolution_state != ResolutionState.RESOLVED.value
            or source.lineage_state
            not in (LineageState.CONFIRMED.value, LineageState.UNCERTAIN.value)
            or source.id not in current_source_ids
            or event.event_kind != ConsumptionEventKind.PRODUCT.value
            or event.presence_state
            not in (PresenceState.SUPPLIED.value, PresenceState.EXPLICIT_ZERO.value)
            or event.coverage_state != CoverageState.COMPLETE.value
            or event.resolution_state != ResolutionState.RESOLVED.value
            or event.lineage_state
            not in (LineageState.CONFIRMED.value, LineageState.UNCERTAIN.value)
            or not revision_chain_validity[event.id]
            or source.id not in qualifying_field_source_ids
            or source.id in tombstoned_source_ids
            or event.id in tombstoned_event_ids
        ):
            continue
        observed_at.append(source.observed_at)
    return max(observed_at, default=None)


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


def _query_events_for_period(
    db: Session,
    *,
    user_id: UUID,
    source_instance_id: UUID,
    start: date,
    end: date,
) -> tuple[tuple[NutritionConsumptionEvent, ...], tuple[NutritionConsumptionEvent, ...]]:
    ranged_events = tuple(
        db.scalars(
            select(NutritionConsumptionEvent).where(
                NutritionConsumptionEvent.user_id == user_id,
                NutritionConsumptionEvent.provider_key == _PROVIDER,
                NutritionConsumptionEvent.source_instance_id == source_instance_id,
                NutritionConsumptionEvent.local_date >= start,
                NutritionConsumptionEvent.local_date <= end,
            )
        ).all()
    )
    logical_keys = tuple(
        sorted(
            {
                event.logical_event_key
                for event in ranged_events
                if event.logical_event_key is not None
            }
        )
    )
    if not logical_keys:
        return ranged_events, ranged_events
    expanded_events = tuple(
        db.scalars(
            select(NutritionConsumptionEvent).where(
                NutritionConsumptionEvent.user_id == user_id,
                NutritionConsumptionEvent.provider_key == _PROVIDER,
                NutritionConsumptionEvent.source_instance_id == source_instance_id,
                NutritionConsumptionEvent.logical_event_key.in_(logical_keys),
            )
        ).all()
    )
    by_id = {event.id: event for event in ranged_events}
    by_id.update({event.id: event for event in expanded_events})
    return ranged_events, tuple(by_id.values())


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


def _load_scope(
    db: Session,
    *,
    user_id: UUID,
    source_instance_id: UUID,
    events: Sequence[NutritionConsumptionEvent],
    metric_keys: Sequence[str],
    expand_source_identities: bool = False,
) -> _AppleReadScope:
    source_ids = {event.source_observation_id for event in events}
    source_rows = (
        db.scalars(
            select(NutritionSourceObservation).where(
                NutritionSourceObservation.user_id == user_id,
                NutritionSourceObservation.provider_key == _PROVIDER,
                NutritionSourceObservation.source_instance_id == source_instance_id,
                NutritionSourceObservation.id.in_(source_ids),
            )
        ).all()
        if source_ids
        else ()
    )
    if expand_source_identities and source_ids:
        source_identity_scope = (
            select(
                NutritionSourceObservation.source_namespace.label("source_namespace"),
                NutritionSourceObservation.source_record_id.label("source_record_id"),
            )
            .where(
                NutritionSourceObservation.user_id == user_id,
                NutritionSourceObservation.provider_key == _PROVIDER,
                NutritionSourceObservation.source_instance_id == source_instance_id,
                NutritionSourceObservation.id.in_(source_ids),
                NutritionSourceObservation.source_record_id.is_not(None),
            )
            .distinct()
            .subquery()
        )
        superseding_source = aliased(NutritionSourceObservation)
        expanded_source_rows = db.scalars(
            select(NutritionSourceObservation)
            .join(
                source_identity_scope,
                (
                    NutritionSourceObservation.source_namespace
                    == source_identity_scope.c.source_namespace
                )
                & (
                    NutritionSourceObservation.source_record_id
                    == source_identity_scope.c.source_record_id
                ),
            )
            .where(
                NutritionSourceObservation.user_id == user_id,
                NutritionSourceObservation.provider_key == _PROVIDER,
                NutritionSourceObservation.source_instance_id == source_instance_id,
                not_(
                    exists(
                        select(1).where(
                            superseding_source.user_id == user_id,
                            superseding_source.provider_key == _PROVIDER,
                            superseding_source.source_instance_id == source_instance_id,
                            superseding_source.source_namespace
                            == NutritionSourceObservation.source_namespace,
                            superseding_source.source_record_id
                            == NutritionSourceObservation.source_record_id,
                            superseding_source.source_record_id.is_not(None),
                            superseding_source.source_revision
                            > NutritionSourceObservation.source_revision,
                        )
                    )
                ),
            )
        ).all()
        source_rows = tuple(
            {source.id: source for source in (*source_rows, *expanded_source_rows)}.values()
        )
    sources = {source.id: source for source in source_rows}
    source_scope_ids = tuple(sources)

    requested_metrics = tuple(metric_keys)
    field_rows = (
        db.scalars(
            select(NutritionFieldObservation)
            .where(
                NutritionFieldObservation.user_id == user_id,
                NutritionFieldObservation.source_observation_id.in_(source_scope_ids),
                NutritionFieldObservation.metric_key.in_(requested_metrics),
                NutritionFieldObservation.observation_role == ObservationRole.CANONICAL.value,
            )
            .order_by(NutritionFieldObservation.provider_field_path, NutritionFieldObservation.id)
        ).all()
        if source_scope_ids and requested_metrics
        else ()
    )
    fields: dict[tuple[UUID, str], list[NutritionFieldObservation]] = defaultdict(list)
    for field in field_rows:
        if field.metric_key is not None:
            fields[(field.source_observation_id, field.metric_key)].append(field)
    identity_ids = _identity_links(db, user_id=user_id, event_ids=[event.id for event in events])
    source_record_ids = {
        source.source_record_id
        for source in sources.values()
        if source.source_record_id is not None
    }
    identity_id_values = {
        identity_id
        for event_identity_ids in identity_ids.values()
        for identity_id in event_identity_ids
    }
    tombstone_scope = [
        NutritionSourceTombstone.source_observation_id.in_(source_scope_ids),
    ]
    if source_record_ids:
        tombstone_scope.append(NutritionSourceTombstone.source_record_id.in_(source_record_ids))
    if identity_id_values:
        tombstone_scope.append(NutritionSourceTombstone.external_identity_id.in_(identity_id_values))
    tombstones = tuple(
        db.scalars(
            select(NutritionSourceTombstone).where(
                NutritionSourceTombstone.user_id == user_id,
                NutritionSourceTombstone.provider_key == _PROVIDER,
                NutritionSourceTombstone.source_instance_id == source_instance_id,
                or_(*tombstone_scope),
            )
        ).all()
    )
    return _AppleReadScope(
        sources=sources,
        fields={key: tuple(values) for key, values in fields.items()},
        tombstones=tombstones,
        identity_ids=identity_ids,
    )


def _event_contributions_from_scope(
    scope: _AppleReadScope,
    *,
    events: Sequence[NutritionConsumptionEvent],
    user_id: UUID,
    source_instance_id: UUID,
    local_date: date,
    metric_key: str,
    revision_groups: Sequence[tuple[NutritionConsumptionEvent, ...]] | None = None,
    revision_chain_validity: Mapping[UUID, bool] | None = None,
) -> list[MetricContribution]:
    contributions: list[MetricContribution] = []

    groups = _current_event_groups(events) if revision_groups is None else revision_groups
    for revision_group in groups:
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
        source = scope.sources.get(event.source_observation_id)
        if _event_tombstoned(event, source, scope.tombstones, scope.identity_ids):
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
            or not (
                revision_chain_validity[event.id]
                if revision_chain_validity is not None
                else _revision_chain_is_valid(events, event)
            )
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

        event_fields = scope.fields.get((event.source_observation_id, metric_key), ())
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


def _event_contributions(
    db: Session,
    *,
    events: Sequence[NutritionConsumptionEvent],
    user_id: UUID,
    source_instance_id: UUID,
    local_date: date,
    metric_key: str,
) -> list[MetricContribution]:
    scope = _load_scope(
        db,
        user_id=user_id,
        source_instance_id=source_instance_id,
        events=events,
        metric_keys=(metric_key,),
    )
    return _event_contributions_from_scope(
        scope,
        events=events,
        user_id=user_id,
        source_instance_id=source_instance_id,
        local_date=local_date,
        metric_key=metric_key,
    )


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


def _provider_candidate(event_candidate: EventReconstructionCandidate) -> ProviderCandidate:
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
    return _provider_candidate(event_candidate)


def resolve_apple_health_period(
    db: Session,
    *,
    user_id: UUID,
    source_instance_id: UUID,
    start: date,
    end: date,
    metric_keys: Sequence[str],
    skip_source_instance_validation: bool = False,
    read_context: NutritionEvidenceIndex | None = None,
) -> Mapping[date, Mapping[str, ProviderCandidate]]:
    """Resolve all requested Apple nutrients for every date in one bounded read."""
    if type(start) is not date or type(end) is not date or start > end:
        raise ValueError("period range must contain dates in ascending order")
    requested_metrics = tuple(metric_keys)
    if len(requested_metrics) != len(set(requested_metrics)):
        raise ValueError("duplicate metric key")
    if any(metric_key not in CANONICAL_NUTRITION_METRICS for metric_key in requested_metrics):
        raise ValueError("metric key must be a canonical nutrition metric")
    expected_source_instance_id = apple_health_source_instance_id(user_id)
    if source_instance_id != expected_source_instance_id:
        raise ValueError("source_instance_id must belong to the same user")
    if read_context is not None and not read_context.contains(
        user_id=user_id,
        provider_key=_PROVIDER,
        source_instance_id=source_instance_id,
        start=start,
        end=end,
    ):
        raise ValueError("read_context scope does not contain Apple period")

    days = tuple(start + timedelta(days=offset) for offset in range((end - start).days + 1))
    if not requested_metrics:
        if read_context is not None:
            read_context.mark_evidence_ready()
        return {local_date: {} for local_date in days}

    ranged_events, events = _query_events_for_period(
        db,
        user_id=user_id,
        source_instance_id=source_instance_id,
        start=start,
        end=end,
    )
    event_dates = {event.local_date for event in ranged_events if event.local_date is not None}
    if not ranged_events:
        if read_context is not None:
            read_context.mark_evidence_ready()
        return {
            local_date: {
                metric_key: _empty_candidate(
                    user_id=user_id,
                    local_date=local_date,
                    metric_key=metric_key,
                )
                for metric_key in requested_metrics
            }
            for local_date in days
        }

    scope = _load_scope(
        db,
        user_id=user_id,
        source_instance_id=source_instance_id,
        events=events,
        metric_keys=requested_metrics,
        expand_source_identities=read_context is not None,
    )
    revision_groups = _current_event_groups(events)
    revision_groups_by_date = _revision_groups_by_date(revision_groups)
    revision_chain_validity = _revision_chain_validity(events, revision_groups)
    if read_context is not None:
        read_context.record_latest_evidence_observed_at(
            _latest_apple_evidence_observed_at(
                scope,
                events=events,
                start=start,
                end=end,
                revision_chain_validity=revision_chain_validity,
            )
        )
        read_context.mark_evidence_ready()
    result: dict[date, Mapping[str, ProviderCandidate]] = {}
    for local_date in days:
        if local_date not in event_dates:
            result[local_date] = {
                metric_key: _empty_candidate(
                    user_id=user_id,
                    local_date=local_date,
                    metric_key=metric_key,
                )
                for metric_key in requested_metrics
            }
            continue
        result[local_date] = {
            metric_key: _provider_candidate(
                build_event_candidate(
                    provider_key=_PROVIDER,
                    user_id=user_id,
                    local_date=local_date,
                    metric_key=metric_key,
                    contributions=_event_contributions_from_scope(
                        scope,
                        events=events,
                        user_id=user_id,
                        source_instance_id=source_instance_id,
                        local_date=local_date,
                        metric_key=metric_key,
                        revision_groups=revision_groups_by_date.get(local_date, ()),
                        revision_chain_validity=revision_chain_validity,
                    ),
                    event_set_known=True,
                )
            )
            for metric_key in requested_metrics
        }
    return result


__all__ = ["resolve_apple_health_metric", "resolve_apple_health_period"]
