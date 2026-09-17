"""Database-only Google Health nutrition resolution for B1/B6."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
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
    NutritionFieldObservation,
    NutritionIngestionRun,
    NutritionSourceObservation,
)
from app.nutrition.repositories import validate_source_instance
from app.nutrition.resolution.contracts import (
    MetricContribution,
    ProviderCandidate,
    build_event_candidate,
)
from app.nutrition.resolution.metrics import CANONICAL_NUTRITION_METRICS, canonical_unit
from app.nutrition.resolution.reasons import EvidenceKind, ReasonCode
from app.nutrition.resolution.resolver import resolve_event_vs_summary

_PROVIDER = "google_health"
_EVENT_NAMESPACE = "google_health.nutrition_log"


def _enum(enum_type: type[Any], value: str | None, fallback: Any) -> Any:
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        return fallback


def _lineage_state(*values: str | None) -> LineageState:
    states = {_enum(LineageState, value, LineageState.UNKNOWN) for value in values}
    if LineageState.UNKNOWN in states:
        return LineageState.UNKNOWN
    if LineageState.UNCERTAIN in states:
        return LineageState.UNCERTAIN
    return LineageState.CONFIRMED


def _missing_contribution(
    *,
    evidence_id: UUID,
    source_observation_id: UUID,
    metric_key: str,
    resolution_state: ResolutionState = ResolutionState.UNRESOLVED,
    lineage_state: LineageState = LineageState.UNKNOWN,
    reason_code: ReasonCode = ReasonCode.UNRESOLVED_PRODUCT,
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
        evidence_kind=EvidenceKind.CONSUMPTION_EVENT,
        reason_code=reason_code,
    )


def _field_contribution(field: NutritionFieldObservation, metric_key: str, lineage: LineageState) -> MetricContribution:
    expected_unit = canonical_unit(metric_key)
    value = field.canonical_value
    presence = _enum(PresenceState, field.presence_state, PresenceState.UNKNOWN)
    resolution = _enum(ResolutionState, field.resolution_state, ResolutionState.UNRESOLVED)
    reason: ReasonCode | None = None
    if value is None or field.canonical_unit != expected_unit:
        value = None
        presence = PresenceState.MISSING
        resolution = ResolutionState.UNRESOLVED
        reason = ReasonCode.INVALID_UNIT if field.canonical_unit != expected_unit else ReasonCode.UNRESOLVED_PRODUCT
    elif (value == Decimal("0") and presence is not PresenceState.EXPLICIT_ZERO) or (
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


def _current_events(events: Sequence[NutritionConsumptionEvent]) -> tuple[tuple[NutritionConsumptionEvent, ...], ...]:
    grouped: dict[tuple[str, str], list[NutritionConsumptionEvent]] = defaultdict(list)
    for event in events:
        key = ("logical", event.logical_event_key) if event.logical_event_key else ("id", str(event.id))
        grouped[key].append(event)
    current: list[tuple[NutritionConsumptionEvent, ...]] = []
    for key in sorted(grouped):
        candidates = grouped[key]
        highest_revision = max(event.revision for event in candidates)
        current.append(tuple(event for event in candidates if event.revision == highest_revision))
    return tuple(current)


def _run_is_complete(run: NutritionIngestionRun | None) -> bool:
    return bool(
        run is not None
        and run.status == "completed"
        and run.coverage_state == CoverageState.COMPLETE.value
    )


@dataclass(frozen=True, slots=True)
class _GoogleReadScope:
    events: tuple[NutritionConsumptionEvent, ...]
    sources: Mapping[UUID, NutritionSourceObservation]
    fields: Mapping[tuple[UUID, str], tuple[NutritionFieldObservation, ...]]
    runs: Mapping[UUID, NutritionIngestionRun]


def _load_google_scope(
    db: Session,
    *,
    user_id: UUID,
    source_instance_id: UUID,
    events: tuple[NutritionConsumptionEvent, ...],
    metric_keys: Sequence[str],
) -> _GoogleReadScope:
    source_ids = {event.source_observation_id for event in events}
    source_filters = [
        NutritionSourceObservation.user_id == user_id,
        NutritionSourceObservation.provider_key == _PROVIDER,
        NutritionSourceObservation.source_instance_id == source_instance_id,
        NutritionSourceObservation.id.in_(source_ids),
    ]
    source_rows = (
        db.scalars(select(NutritionSourceObservation).where(*source_filters)).all()
        if source_ids
        else ()
    )
    sources = {source.id: source for source in source_rows}

    supported_metric_keys = tuple(
        metric_key for metric_key in metric_keys if metric_key in CANONICAL_NUTRITION_METRICS
    )
    fields_by_scope: dict[tuple[UUID, str], list[NutritionFieldObservation]] = defaultdict(list)
    if source_ids and supported_metric_keys:
        field_rows = db.scalars(
            select(NutritionFieldObservation)
            .where(
                NutritionFieldObservation.user_id == user_id,
                NutritionFieldObservation.source_observation_id.in_(source_ids),
                NutritionFieldObservation.metric_key.in_(supported_metric_keys),
                NutritionFieldObservation.observation_role == ObservationRole.CANONICAL.value,
            )
            .order_by(
                NutritionFieldObservation.source_observation_id,
                NutritionFieldObservation.metric_key,
                NutritionFieldObservation.provider_field_path,
                NutritionFieldObservation.id,
            )
        ).all()
        for field in field_rows:
            if field.metric_key is not None:
                fields_by_scope[(field.source_observation_id, field.metric_key)].append(field)

    run_ids = {source.ingestion_run_id for source in sources.values()}
    run_rows = (
        db.scalars(
            select(NutritionIngestionRun).where(
                NutritionIngestionRun.user_id == user_id,
                NutritionIngestionRun.provider_key == _PROVIDER,
                NutritionIngestionRun.source_instance_id == source_instance_id,
                NutritionIngestionRun.id.in_(run_ids),
            )
        ).all()
        if run_ids
        else ()
    )
    return _GoogleReadScope(
        events=events,
        sources=sources,
        fields={scope: tuple(items) for scope, items in fields_by_scope.items()},
        runs={run.id: run for run in run_rows},
    )


def _load_google_metric_scope(
    db: Session,
    *,
    user_id: UUID,
    source_instance_id: UUID,
    metric_key: str,
) -> _GoogleReadScope:
    events = tuple(
        db.scalars(
            select(NutritionConsumptionEvent)
            .where(
                NutritionConsumptionEvent.user_id == user_id,
                NutritionConsumptionEvent.provider_key == _PROVIDER,
                NutritionConsumptionEvent.source_instance_id == source_instance_id,
            )
            .order_by(
                NutritionConsumptionEvent.logical_event_key,
                NutritionConsumptionEvent.revision,
                NutritionConsumptionEvent.id,
            )
        ).all()
    )
    return _load_google_scope(
        db,
        user_id=user_id,
        source_instance_id=source_instance_id,
        events=events,
        metric_keys=(metric_key,),
    )


def _load_google_period_scope(
    db: Session,
    *,
    user_id: UUID,
    source_instance_id: UUID,
    start: date,
    end: date,
    metric_keys: Sequence[str],
) -> _GoogleReadScope:
    events = tuple(
        db.scalars(
            select(NutritionConsumptionEvent)
            .where(
                NutritionConsumptionEvent.user_id == user_id,
                NutritionConsumptionEvent.provider_key == _PROVIDER,
                NutritionConsumptionEvent.source_instance_id == source_instance_id,
                NutritionConsumptionEvent.local_date >= start,
                NutritionConsumptionEvent.local_date <= end,
            )
            .order_by(
                NutritionConsumptionEvent.logical_event_key,
                NutritionConsumptionEvent.revision,
                NutritionConsumptionEvent.id,
            )
        ).all()
    )
    logical_keys = tuple(
        sorted(
            {
                event.logical_event_key
                for event in events
                if event.logical_event_key is not None
            }
        )
    )
    if logical_keys:
        events = tuple(
            db.scalars(
                select(NutritionConsumptionEvent)
                .where(
                    NutritionConsumptionEvent.user_id == user_id,
                    NutritionConsumptionEvent.provider_key == _PROVIDER,
                    NutritionConsumptionEvent.source_instance_id == source_instance_id,
                    NutritionConsumptionEvent.logical_event_key.in_(logical_keys),
                )
                .order_by(
                    NutritionConsumptionEvent.logical_event_key,
                    NutritionConsumptionEvent.revision,
                    NutritionConsumptionEvent.id,
                )
            ).all()
        )
    return _load_google_scope(
        db,
        user_id=user_id,
        source_instance_id=source_instance_id,
        events=events,
        metric_keys=metric_keys,
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


def _resolve_google_health_metric_from_scope(
    scope: _GoogleReadScope,
    *,
    user_id: UUID,
    source_instance_id: UUID,
    local_date: date,
    metric_key: str,
    current_events: tuple[tuple[NutritionConsumptionEvent, ...], ...] | None = None,
) -> ProviderCandidate:
    contributions: list[MetricContribution] = []
    for revision_group in (
        _current_events(scope.events) if current_events is None else current_events
    ):
        if len(revision_group) != 1:
            contributions.extend(
                _missing_contribution(
                    evidence_id=event.id,
                    source_observation_id=event.source_observation_id,
                    metric_key=metric_key,
                    resolution_state=ResolutionState.DUPLICATE_CANDIDATE,
                    reason_code=ReasonCode.UNRESOLVED_PRODUCT,
                )
                for event in revision_group
            )
            continue

        event = revision_group[0]
        if event.local_date != local_date:
            continue
        source = scope.sources.get(event.source_observation_id)
        source_valid = bool(
            source is not None
            and source.provider_key == _PROVIDER
            and source.source_instance_id == source_instance_id
            and source.observation_kind == ObservationKind.CONSUMPTION_EVENT.value
            and source.source_namespace == _EVENT_NAMESPACE
            and source.local_date == local_date
        )
        event_state = _enum(ResolutionState, event.resolution_state, ResolutionState.UNRESOLVED)
        event_lineage = _lineage_state(event.lineage_state, source.lineage_state if source else None)
        if (
            not source_valid
            or event.event_kind
            not in {
                ConsumptionEventKind.PRODUCT.value,
                ConsumptionEventKind.SIMPLE_PRODUCT.value,
            }
            or (
                event_state is not ResolutionState.RESOLVED
                and event.event_kind != ConsumptionEventKind.PRODUCT.value
            )
            or not _revision_chain_is_valid(scope.events, event)
        ):
            contributions.append(
                _missing_contribution(
                    evidence_id=event.id,
                    source_observation_id=event.source_observation_id,
                    metric_key=metric_key,
                    resolution_state=event_state
                    if event_state is not ResolutionState.RESOLVED
                    else ResolutionState.UNRESOLVED,
                    lineage_state=event_lineage,
                )
            )
            continue
        assert source is not None

        fields = scope.fields.get((event.source_observation_id, metric_key), ())
        if len(fields) != 1:
            contributions.append(
                _missing_contribution(
                    evidence_id=event.id,
                    source_observation_id=event.source_observation_id,
                    metric_key=metric_key,
                    resolution_state=(
                        ResolutionState.DUPLICATE_CANDIDATE
                        if len(fields) > 1
                        else ResolutionState.UNRESOLVED
                    ),
                    lineage_state=event_lineage,
                )
            )
            continue

        field = fields[0]
        contribution = _field_contribution(
            field,
            metric_key,
            _lineage_state(event.lineage_state, source.lineage_state, field.lineage_state),
        )
        contributions.append(contribution)
        source_run = scope.runs.get(source.ingestion_run_id)
        if (
            contribution.value is None
            or field.coverage_state != CoverageState.COMPLETE.value
            or event.coverage_state != CoverageState.COMPLETE.value
            or source.coverage_state != CoverageState.COMPLETE.value
            or not _run_is_complete(source_run)
        ):
            contributions.append(
                _missing_contribution(
                    evidence_id=event.id,
                    source_observation_id=event.source_observation_id,
                    metric_key=metric_key,
                    resolution_state=ResolutionState.UNRESOLVED,
                    lineage_state=contribution.lineage_state,
                )
            )

    event_candidate = build_event_candidate(
        provider_key=_PROVIDER,
        user_id=user_id,
        local_date=local_date,
        metric_key=metric_key,
        contributions=contributions,
        event_set_known=True,
    )
    return resolve_event_vs_summary(event_candidate, None)


def resolve_google_health_metric(
    db: Session,
    *,
    user_id: UUID,
    source_instance_id: UUID,
    local_date: date,
    metric_key: str,
) -> ProviderCandidate:
    """Resolve one Google Health metric from persisted current event evidence."""
    if metric_key not in CANONICAL_NUTRITION_METRICS:
        return _unsupported_candidate(
            user_id=user_id,
            local_date=local_date,
            metric_key=metric_key,
        )

    validate_source_instance(
        db,
        user_id=user_id,
        provider_key=_PROVIDER,
        source_instance_id=source_instance_id,
    )
    scope = _load_google_metric_scope(
        db,
        user_id=user_id,
        source_instance_id=source_instance_id,
        metric_key=metric_key,
    )
    return _resolve_google_health_metric_from_scope(
        scope,
        user_id=user_id,
        source_instance_id=source_instance_id,
        local_date=local_date,
        metric_key=metric_key,
    )


def resolve_google_health_period(
    db: Session,
    *,
    user_id: UUID,
    source_instance_id: UUID,
    start: date,
    end: date,
    metric_keys: Sequence[str],
    skip_source_instance_validation: bool = False,
) -> Mapping[date, Mapping[str, ProviderCandidate]]:
    """Resolve a complete Google Health metric matrix from one bounded read."""
    if type(start) is not date or type(end) is not date or start > end:
        raise ValueError("period range must contain dates in ascending order")

    requested_metrics = tuple(metric_keys)
    if len(requested_metrics) != len(set(requested_metrics)):
        raise ValueError("duplicate metric key")
    supported_metrics = tuple(
        metric_key
        for metric_key in requested_metrics
        if metric_key in CANONICAL_NUTRITION_METRICS
    )
    if supported_metrics:
        if not skip_source_instance_validation:
            validate_source_instance(
                db,
                user_id=user_id,
                provider_key=_PROVIDER,
                source_instance_id=source_instance_id,
            )
        scope = _load_google_period_scope(
            db,
            user_id=user_id,
            source_instance_id=source_instance_id,
            start=start,
            end=end,
            metric_keys=supported_metrics,
        )
        current_events = _current_events(scope.events)
    else:
        scope = None
        current_events = ()

    resolved: dict[date, Mapping[str, ProviderCandidate]] = {}
    for offset in range((end - start).days + 1):
        current_date = start + timedelta(days=offset)
        day_result: dict[str, ProviderCandidate] = {}
        for metric_key in requested_metrics:
            if metric_key not in CANONICAL_NUTRITION_METRICS:
                day_result[metric_key] = _unsupported_candidate(
                    user_id=user_id,
                    local_date=current_date,
                    metric_key=metric_key,
                )
            else:
                assert scope is not None
                day_result[metric_key] = _resolve_google_health_metric_from_scope(
                    scope,
                    user_id=user_id,
                    source_instance_id=source_instance_id,
                    local_date=current_date,
                    metric_key=metric_key,
                    current_events=current_events,
                )
        resolved[current_date] = day_result
    return resolved

__all__ = ["resolve_google_health_metric", "resolve_google_health_period"]
