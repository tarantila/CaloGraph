"""Read-only YAZIO adapter for the B1 nutrition resolution engine."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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
    ProjectionGranularity,
    ResolutionState,
)
from app.nutrition.models import (
    NutritionConsumptionEvent,
    NutritionExternalIdentityLink,
    NutritionFieldObservation,
    NutritionFoodSnapshot,
    NutritionSourceObservation,
    NutritionSourceTombstone,
)
from app.nutrition.repositories import validate_source_instance
from app.nutrition.resolution import (
    CANONICAL_METRICS,
    EvidenceKind,
    MetricContribution,
    ProviderCandidate,
    ReasonCode,
    SummaryCandidate,
    build_event_candidate,
    canonical_unit,
    resolve_event_vs_summary,
)

_PROVIDER = "yazio"
_EVENT_NAMESPACES = frozenset({"yazio.consumed_item", "yazio.simple_product"})
_SUMMARY_NAMESPACE = "yazio.daily_summary"


@dataclass(frozen=True, slots=True)
class _CurrentEvent:
    candidates: tuple[NutritionConsumptionEvent, ...]
    revision_state: ResolutionState


@dataclass(frozen=True, slots=True)
class _ReadScope:
    events: tuple[NutritionConsumptionEvent, ...]
    sources: Mapping[UUID, NutritionSourceObservation]
    fields: Mapping[UUID, tuple[NutritionFieldObservation, ...]]
    parent_fields: Mapping[UUID, NutritionFieldObservation]
    snapshots: Mapping[UUID, NutritionFoodSnapshot]
    tombstones: tuple[NutritionSourceTombstone, ...]
    current_event_identity_ids: Mapping[UUID, frozenset[UUID]]
def _enum[EnumValue](
    enum_type: type[EnumValue], value: str | None, fallback: EnumValue
) -> EnumValue:
    try:
        return enum_type(value)  # type: ignore[call-arg]
    except (TypeError, ValueError):
        return fallback


def _source_tombstoned(
    source: NutritionSourceObservation | None,
    tombstones: Sequence[NutritionSourceTombstone],
) -> bool:
    if source is None:
        return True
    return any(
        (
            tombstone.source_observation_id == source.id
            if tombstone.source_observation_id is not None
            else (
                tombstone.source_namespace == source.source_namespace
                and tombstone.source_record_id is not None
                and tombstone.source_record_id == source.source_record_id
            )
        )
        for tombstone in tombstones
    )
def _event_tombstoned(
    event: NutritionConsumptionEvent,
    source: NutritionSourceObservation | None,
    tombstones: Sequence[NutritionSourceTombstone],
    current_identity_ids: Mapping[UUID, frozenset[UUID]],
) -> bool:
    if _source_tombstoned(source, tombstones):
        return True
    event_identity_ids = current_identity_ids.get(event.id, frozenset())
    return any(
        tombstone.source_namespace in _EVENT_NAMESPACES
        and tombstone.external_identity_id in event_identity_ids
        for tombstone in tombstones
    )


def _scope_source(
    source: NutritionSourceObservation | None,
    *,
    user_id: UUID,
    source_instance_id: UUID,
    local_date: date | None,
    namespace: str | None = None,
) -> bool:
    return bool(
        source is not None
        and source.user_id == user_id
        and source.provider_key == _PROVIDER
        and source.source_instance_id == source_instance_id
        and (local_date is None or source.local_date == local_date)
        and (namespace is None or source.source_namespace == namespace)
    )


def _current_links(
    db: Session,
    *,
    user_id: UUID,
    event_ids: Sequence[UUID],
) -> Mapping[UUID, frozenset[UUID]]:
    if not event_ids:
        return {}
    links = db.scalars(
        select(NutritionExternalIdentityLink)
        .where(
            NutritionExternalIdentityLink.user_id == user_id,
            NutritionExternalIdentityLink.consumption_event_id.in_(event_ids),
            NutritionExternalIdentityLink.link_role == "event_identity",
        )
        .order_by(
            NutritionExternalIdentityLink.external_identity_id,
            NutritionExternalIdentityLink.link_revision.desc(),
            NutritionExternalIdentityLink.id,
        )
    ).all()
    latest: dict[UUID, NutritionExternalIdentityLink] = {}
    for link in links:
        latest.setdefault(link.external_identity_id, link)
    result: dict[UUID, set[UUID]] = defaultdict(set)
    for identity_id, link in latest.items():
        if link.consumption_event_id is not None:
            result[link.consumption_event_id].add(identity_id)
    return {event_id: frozenset(identity_ids) for event_id, identity_ids in result.items()}


def _load_scope(
    db: Session,
    *,
    user_id: UUID,
    source_instance_id: UUID,
    local_date: date,
    metric_key: str,
) -> _ReadScope:
    events = tuple(
        db.scalars(
            select(NutritionConsumptionEvent)
            .where(
                NutritionConsumptionEvent.user_id == user_id,
                NutritionConsumptionEvent.provider_key == _PROVIDER,
                NutritionConsumptionEvent.source_instance_id == source_instance_id,
                NutritionConsumptionEvent.local_date == local_date,
            )
            .order_by(
                NutritionConsumptionEvent.logical_event_key,
                NutritionConsumptionEvent.revision.desc(),
                NutritionConsumptionEvent.id,
            )
        ).all()
    )
    event_source_ids = {event.source_observation_id for event in events}
    snapshots = tuple(
        db.scalars(
            select(NutritionFoodSnapshot).where(
                NutritionFoodSnapshot.user_id == user_id,
                NutritionFoodSnapshot.id.in_({event.food_snapshot_id for event in events if event.food_snapshot_id}),
            )
        ).all()
    ) if any(event.food_snapshot_id for event in events) else ()
    snapshot_source_ids = {snapshot.source_observation_id for snapshot in snapshots}
    summary_sources = tuple(
        db.scalars(
            select(NutritionSourceObservation)
            .where(
                NutritionSourceObservation.user_id == user_id,
                NutritionSourceObservation.provider_key == _PROVIDER,
                NutritionSourceObservation.source_instance_id == source_instance_id,
                NutritionSourceObservation.observation_kind == ObservationKind.DAILY_SUMMARY.value,
                NutritionSourceObservation.source_namespace == _SUMMARY_NAMESPACE,
                NutritionSourceObservation.local_date == local_date,
            )
            .order_by(
                NutritionSourceObservation.source_record_id,
                NutritionSourceObservation.source_revision.desc(),
                NutritionSourceObservation.id,
            )
        ).all()
    )
    source_ids = event_source_ids | snapshot_source_ids | {source.id for source in summary_sources}
    source_rows = (
        db.scalars(
            select(NutritionSourceObservation).where(
                NutritionSourceObservation.user_id == user_id,
                NutritionSourceObservation.id.in_(source_ids),
            )
        ).all()
        if source_ids
        else ()
    )
    sources = {source.id: source for source in source_rows}
    field_source_ids = source_ids
    fields = (
        db.scalars(
            select(NutritionFieldObservation)
            .where(
                NutritionFieldObservation.user_id == user_id,
                NutritionFieldObservation.source_observation_id.in_(field_source_ids),
                NutritionFieldObservation.metric_key == metric_key,
            )
            .order_by(
                NutritionFieldObservation.source_observation_id,
                NutritionFieldObservation.provider_field_path,
                NutritionFieldObservation.observation_role,
                NutritionFieldObservation.id,
            )
        ).all()
        if field_source_ids
        else []
    )
    field_map: dict[UUID, list[NutritionFieldObservation]] = defaultdict(list)
    parent_ids = {
        field.derived_from_field_observation_id
        for field in fields
        if field.derived_from_field_observation_id is not None
    }
    for field in fields:
        field_map[field.source_observation_id].append(field)
    parent_fields = (
        {
            field.id: field
            for field in db.scalars(
                select(NutritionFieldObservation).where(
                    NutritionFieldObservation.user_id == user_id,
                    NutritionFieldObservation.id.in_(parent_ids),
                )
            ).all()
        }
        if parent_ids
        else {}
    )
    tombstones = tuple(
        db.scalars(
            select(NutritionSourceTombstone).where(
                NutritionSourceTombstone.user_id == user_id,
                NutritionSourceTombstone.provider_key == _PROVIDER,
                NutritionSourceTombstone.source_instance_id == source_instance_id,
            )
        ).all()
    )
    current_identity_ids = _current_links(db, user_id=user_id, event_ids=[event.id for event in events])
    return _ReadScope(
        events=events,
        sources=sources,
        fields={source_id: tuple(items) for source_id, items in field_map.items()},
        parent_fields=parent_fields,
        snapshots={snapshot.id: snapshot for snapshot in snapshots},
        tombstones=tombstones,
        current_event_identity_ids=current_identity_ids,
    )


def _revision_chain_is_valid(events: Sequence[NutritionConsumptionEvent], current: NutritionConsumptionEvent) -> bool:
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


def _current_events(scope: _ReadScope, *, user_id: UUID, source_instance_id: UUID, local_date: date) -> tuple[_CurrentEvent, ...]:
    grouped: dict[tuple[str, object], list[NutritionConsumptionEvent]] = defaultdict(list)
    current: list[_CurrentEvent] = []
    for event in scope.events:
        key: tuple[str, object] = (
            ("logical", event.logical_event_key)
            if event.logical_event_key is not None
            else ("id", event.id)
        )
        grouped[key].append(event)
    for key in sorted(grouped, key=lambda item: (item[0], str(item[1]))):
        candidates = grouped[key]
        highest_revision = max(event.revision for event in candidates)
        highest = tuple(event for event in candidates if event.revision == highest_revision)
        if any(
            _event_tombstoned(
                event,
                scope.sources.get(event.source_observation_id),
                scope.tombstones,
                scope.current_event_identity_ids,
            )
            for event in highest
        ):
            continue
        if len(highest) > 1:
            current.append(_CurrentEvent(highest, ResolutionState.DUPLICATE_CANDIDATE))
            continue
        event = highest[0]
        state = (
            ResolutionState.RESOLVED
            if _revision_chain_is_valid(candidates, event)
            else ResolutionState.UNRESOLVED
        )
        current.append(_CurrentEvent((event,), state))
    return tuple(current)


def _missing_contribution(
    evidence_id: UUID,
    metric_key: str,
    *,
    source_observation_id: UUID,
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
    canonical = canonical_unit(metric_key)
    presence = _enum(PresenceState, field.presence_state, PresenceState.UNKNOWN)
    resolution = _enum(ResolutionState, field.resolution_state, ResolutionState.UNRESOLVED)
    lineage = _enum(LineageState, field.lineage_state, LineageState.UNKNOWN)
    value = field.canonical_value
    reason: ReasonCode | None = None
    if value is None:
        presence = PresenceState.MISSING
    elif field.canonical_unit != canonical:
        value = None
        presence = PresenceState.MISSING
        resolution = ResolutionState.UNRESOLVED
        reason = ReasonCode.INVALID_UNIT
    elif (
        value == Decimal("0") and presence is not PresenceState.EXPLICIT_ZERO
    ) or (
        value != Decimal("0") and presence is not PresenceState.SUPPLIED
    ):
        value = None
        presence = PresenceState.MISSING
        resolution = ResolutionState.UNRESOLVED
        reason = ReasonCode.UNRESOLVED_PRODUCT
    return MetricContribution(
        evidence_id=field.id,
        source_observation_id=field.source_observation_id,
        metric_key=metric_key,
        value=value,
        unit=field.canonical_unit if value is not None else None,
        presence_state=presence,
        resolution_state=resolution,
        lineage_state=lineage,
        evidence_kind=EvidenceKind.FIELD_OBSERVATION,
        reason_code=reason,
    )


def _product_field_valid(
    field: NutritionFieldObservation,
    *,
    event: NutritionConsumptionEvent,
    snapshot: NutritionFoodSnapshot | None,
    parent_fields: Mapping[UUID, NutritionFieldObservation],
    sources: Mapping[UUID, NutritionSourceObservation],
    tombstones: Sequence[NutritionSourceTombstone],
    metric_key: str,
) -> bool:
    if snapshot is None or field.derived_from_field_observation_id is None:
        return False
    parent = parent_fields.get(field.derived_from_field_observation_id)
    if parent is None or parent.observation_role != ObservationRole.PROVIDER.value:
        return False
    if parent.metric_key != metric_key or parent.source_observation_id != snapshot.source_observation_id:
        return False
    snapshot_source = sources.get(snapshot.source_observation_id)
    if not _scope_source(
        snapshot_source,
        user_id=event.user_id,
        source_instance_id=event.source_instance_id,
        local_date=None,
    ):
        return False
    parent_source = sources.get(parent.source_observation_id)
    if not _scope_source(
        parent_source,
        user_id=event.user_id,
        source_instance_id=event.source_instance_id,
        local_date=None,
    ):
        return False
    return not _source_tombstoned(parent_source, tombstones)
def _event_contributions(
    scope: _ReadScope,
    current: Sequence[_CurrentEvent],
    metric_key: str,
    local_date: date,
) -> tuple[MetricContribution, ...]:
    contributions: list[MetricContribution] = []
    for item in current:
        if item.revision_state is not ResolutionState.RESOLVED:
            for event in item.candidates:
                contributions.append(
                    _missing_contribution(
                        event.id,
                        metric_key,
                        source_observation_id=event.source_observation_id,
                        resolution_state=item.revision_state,
                        reason_code=ReasonCode.UNRESOLVED_PRODUCT,
                    )
                )
            continue
        event = item.candidates[0]
        source = scope.sources.get(event.source_observation_id)
        if event.event_kind not in {
            ConsumptionEventKind.PRODUCT.value,
            ConsumptionEventKind.SIMPLE_PRODUCT.value,
        }:
            contributions.append(
                _missing_contribution(
                    event.id,
                    metric_key,
                    source_observation_id=event.source_observation_id,
                    reason_code=ReasonCode.UNRESOLVED_PRODUCT,
                )
            )
            continue
        expected_namespace = (
            "yazio.consumed_item"
            if event.event_kind == ConsumptionEventKind.PRODUCT.value
            else "yazio.simple_product"
        )
        if not _scope_source(
            source,
            user_id=event.user_id,
            source_instance_id=event.source_instance_id,
            local_date=local_date,
            namespace=expected_namespace,
        ):
            contributions.append(
                _missing_contribution(
                    event.id,
                    metric_key,
                    source_observation_id=event.source_observation_id,
                    reason_code=ReasonCode.UNRESOLVED_PRODUCT,
                )
            )
            continue
        role = (
            ObservationRole.DERIVED.value
            if event.event_kind == ConsumptionEventKind.PRODUCT.value
            else ObservationRole.PROVIDER.value
        )
        fields = tuple(field for field in scope.fields.get(event.source_observation_id, ()) if field.observation_role == role)
        if event.event_kind == ConsumptionEventKind.PRODUCT.value:
            snapshot = scope.snapshots.get(event.food_snapshot_id) if event.food_snapshot_id is not None else None
            fields = tuple(
                field
                for field in fields
                if _product_field_valid(
                    field,
                    event=event,
                    snapshot=snapshot,
                    parent_fields=scope.parent_fields,
                    sources=scope.sources,
                    tombstones=scope.tombstones,
                    metric_key=metric_key,
                )
            )
        if len(fields) == 0:
            contributions.append(
                _missing_contribution(
                    event.id,
                    metric_key,
                    source_observation_id=event.source_observation_id,
                    reason_code=ReasonCode.UNRESOLVED_PRODUCT,
                )
            )
        elif len(fields) > 1:
            units = {field.canonical_unit for field in fields}
            resolution = ResolutionState.CONFLICT if len(units) > 1 else ResolutionState.DUPLICATE_CANDIDATE
            reason = ReasonCode.METRIC_UNIT_MISMATCH if resolution is ResolutionState.CONFLICT else ReasonCode.SUMMARY_FALLBACK_DUPLICATE_CANDIDATE
            contributions.extend(
                _missing_contribution(
                    field.id,
                    metric_key,
                    source_observation_id=field.source_observation_id,
                    resolution_state=resolution,
                    reason_code=reason,
                    evidence_kind=EvidenceKind.FIELD_OBSERVATION,
                )
                for field in fields
            )
        else:
            contributions.append(_field_contribution(fields[0], metric_key))
    return tuple(contributions)


def _summary_groups(
    scope: _ReadScope,
    *,
    user_id: UUID,
    source_instance_id: UUID,
    local_date: date,
) -> tuple[tuple[NutritionSourceObservation, ...], ...]:
    observations = [
        source
        for source in scope.sources.values()
        if _scope_source(
            source,
            user_id=user_id,
            source_instance_id=source_instance_id,
            local_date=local_date,
            namespace=_SUMMARY_NAMESPACE,
        )
    ]
    grouped: dict[str, list[NutritionSourceObservation]] = defaultdict(list)
    for observation in observations:
        grouped[observation.source_record_id or str(observation.id)].append(observation)
    groups: list[tuple[NutritionSourceObservation, ...]] = []
    for key in sorted(grouped):
        items = grouped[key]
        highest = max(item.source_revision for item in items)
        current = tuple(item for item in items if item.source_revision == highest)
        if any(_source_tombstoned(item, scope.tombstones) for item in current):
            continue
        groups.append(current)
    return tuple(groups)
def _summary_candidate(
    scope: _ReadScope,
    observation: NutritionSourceObservation,
    metric_key: str,
    local_date: date,
) -> SummaryCandidate:
    fields = tuple(
        field
        for field in scope.fields.get(observation.id, ())
        if field.observation_role == ObservationRole.PROVIDER.value
    )
    if len(fields) != 1:
        resolution = ResolutionState.DUPLICATE_CANDIDATE if len(fields) > 1 else ResolutionState.UNRESOLVED
        if fields:
            missing_contributions = tuple(
                _missing_contribution(
                    field.id,
                    metric_key,
                    source_observation_id=field.source_observation_id,
                    resolution_state=resolution,
                    reason_code=ReasonCode.SUMMARY_UNUSABLE,
                    evidence_kind=EvidenceKind.FIELD_OBSERVATION,
                )
                for field in fields
            )
        else:
            missing_contributions = (
                _missing_contribution(
                    observation.id,
                    metric_key,
                    source_observation_id=observation.id,
                    resolution_state=resolution,
                    reason_code=ReasonCode.SUMMARY_UNUSABLE,
                    evidence_kind=EvidenceKind.SOURCE_OBSERVATION,
                ),
            )
        return SummaryCandidate(
            provider_key=_PROVIDER,
            user_id=observation.user_id,
            local_date=local_date,
            metric_key=metric_key,
            value=None,
            unit=None,
            selected_granularity=None,
            presence_state=PresenceState.MISSING,
            coverage_state=CoverageState.UNKNOWN,
            resolution_state=resolution,
            lineage_state=LineageState.UNKNOWN,
            source_lineage=missing_contributions,
            reason_code=ReasonCode.SUMMARY_ONLY,
        )
    contribution = _field_contribution(fields[0], metric_key)
    value = contribution.value
    coverage = _enum(CoverageState, observation.coverage_state, CoverageState.UNKNOWN)
    source_resolution = _enum(ResolutionState, observation.resolution_state, ResolutionState.UNRESOLVED)
    resolution = source_resolution if source_resolution is not ResolutionState.RESOLVED else contribution.resolution_state
    source_lineage = _enum(LineageState, observation.lineage_state, LineageState.UNKNOWN)
    lineage = (
        source_lineage
        if source_lineage is not LineageState.CONFIRMED
        else contribution.lineage_state
    )
    summary_contributions: list[MetricContribution] = [
        MetricContribution(
            evidence_id=contribution.evidence_id,
            source_observation_id=contribution.source_observation_id,
            metric_key=metric_key,
            value=value,
            unit=contribution.unit,
            presence_state=contribution.presence_state,
            resolution_state=resolution,
            lineage_state=lineage,
            evidence_kind=EvidenceKind.FIELD_OBSERVATION,
            reason_code=contribution.reason_code,
        )
    ]
    if value is not None and coverage is CoverageState.PARTIAL:
        summary_contributions.append(
            _missing_contribution(
                observation.id,
                metric_key,
                source_observation_id=observation.id,
                resolution_state=resolution,
                lineage_state=lineage,
                evidence_kind=EvidenceKind.SOURCE_OBSERVATION,
            )
        )
    return SummaryCandidate(
        provider_key=_PROVIDER,
        user_id=observation.user_id,
        local_date=local_date,
        metric_key=metric_key,
        value=value,
        unit=contribution.unit,
        selected_granularity=ProjectionGranularity.SUMMARY if value is not None else None,
        presence_state=contribution.presence_state,
        coverage_state=coverage,
        resolution_state=resolution,
        lineage_state=lineage,
        source_lineage=tuple(summary_contributions),
        reason_code=ReasonCode.SUMMARY_ONLY,
    )


def _unsupported_candidate(*, user_id: UUID, local_date: date, metric_key: str) -> ProviderCandidate:
    return ProviderCandidate(
        provider_key=_PROVIDER,
        user_id=user_id,
        local_date=local_date,
        metric_key=metric_key,
        value=None,
        unit=None,
        selected_granularity=None,
        presence_state=PresenceState.UNSUPPORTED,
        coverage_state=CoverageState.UNKNOWN,
        resolution_state=ResolutionState.UNRESOLVED,
        lineage_state=LineageState.UNKNOWN,
        source_lineage=(),
        reason_code=ReasonCode.UNSUPPORTED_METRIC,
    )


def resolve_yazio_metric(
    db: Session,
    *,
    user_id: UUID,
    source_instance_id: UUID,
    local_date: date,
    metric_key: str,
) -> ProviderCandidate:
    """Resolve one persisted YAZIO metric without mutating the session."""
    if metric_key not in CANONICAL_METRICS:
        return _unsupported_candidate(user_id=user_id, local_date=local_date, metric_key=metric_key)
    validate_source_instance(
        db,
        user_id=user_id,
        provider_key=_PROVIDER,
        source_instance_id=source_instance_id,
    )
    scope = _load_scope(
        db,
        user_id=user_id,
        source_instance_id=source_instance_id,
        local_date=local_date,
        metric_key=metric_key,
    )
    current = _current_events(
        scope,
        user_id=user_id,
        source_instance_id=source_instance_id,
        local_date=local_date,
    )
    event_candidate = build_event_candidate(
        provider_key=_PROVIDER,
        user_id=user_id,
        local_date=local_date,
        metric_key=metric_key,
        contributions=_event_contributions(scope, current, metric_key, local_date),
        event_set_known=True,
    )
    summary_groups = _summary_groups(
        scope,
        user_id=user_id,
        source_instance_id=source_instance_id,
        local_date=local_date,
    )
    summary_candidate: SummaryCandidate | None = None
    if len(summary_groups) == 1 and len(summary_groups[0]) == 1:
        summary_candidate = _summary_candidate(scope, summary_groups[0][0], metric_key, local_date)
    elif len(summary_groups) > 1 or (summary_groups and len(summary_groups[0]) > 1):
        observations = tuple(item for group in summary_groups for item in group)
        summary_candidate = SummaryCandidate(
            provider_key=_PROVIDER,
            user_id=user_id,
            local_date=local_date,
            metric_key=metric_key,
            value=None,
            unit=None,
            selected_granularity=None,
            presence_state=PresenceState.MISSING,
            coverage_state=CoverageState.UNKNOWN,
            resolution_state=ResolutionState.DUPLICATE_CANDIDATE,
            lineage_state=LineageState.UNKNOWN,
            source_lineage=tuple(
                _missing_contribution(
                    observation.id,
                    metric_key,
                    source_observation_id=observation.id,
                    resolution_state=ResolutionState.DUPLICATE_CANDIDATE,
                    reason_code=ReasonCode.SUMMARY_FALLBACK_DUPLICATE_CANDIDATE,
                    evidence_kind=EvidenceKind.SOURCE_OBSERVATION,
                )
                for observation in observations
            ),
            reason_code=ReasonCode.SUMMARY_ONLY,
        )
    return resolve_event_vs_summary(event_candidate, summary_candidate)


def resolve_yazio_day(
    db: Session,
    *,
    user_id: UUID,
    source_instance_id: UUID,
    local_date: date,
) -> Mapping[str, ProviderCandidate]:
    """Resolve all canonical B1 metrics for one YAZIO user/date scope."""
    return {
        metric_key: resolve_yazio_metric(
            db,
            user_id=user_id,
            source_instance_id=source_instance_id,
            local_date=local_date,
            metric_key=metric_key,
        )
        for metric_key in CANONICAL_METRICS
    }
