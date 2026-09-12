"""Database-only Google Health nutrition resolution for B1/B6."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import date
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
from app.nutrition.resolution.metrics import CANONICAL_METRICS, canonical_unit
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


def resolve_google_health_metric(
    db: Session,
    *,
    user_id: UUID,
    source_instance_id: UUID,
    local_date: date,
    metric_key: str,
) -> ProviderCandidate:
    """Resolve one Google Health metric from persisted current event evidence."""
    if metric_key not in CANONICAL_METRICS:
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

    validate_source_instance(
        db,
        user_id=user_id,
        provider_key=_PROVIDER,
        source_instance_id=source_instance_id,
    )

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
    run_ids = {source.ingestion_run_id for source in sources.values()}
    runs = (
        {
            run.id: run
            for run in db.scalars(
                select(NutritionIngestionRun).where(
                    NutritionIngestionRun.user_id == user_id,
                    NutritionIngestionRun.id.in_(run_ids),
                )
            ).all()
        }
        if run_ids
        else {}
    )

    contributions: list[MetricContribution] = []
    for revision_group in _current_events(events):
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
        source = sources.get(event.source_observation_id)
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
            or not _revision_chain_is_valid(events, event)
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

        fields = tuple(
            db.scalars(
                select(NutritionFieldObservation).where(
                    NutritionFieldObservation.user_id == user_id,
                    NutritionFieldObservation.source_observation_id == event.source_observation_id,
                    NutritionFieldObservation.metric_key == metric_key,
                    NutritionFieldObservation.observation_role == ObservationRole.CANONICAL.value,
                )
                .order_by(NutritionFieldObservation.provider_field_path, NutritionFieldObservation.id)
            ).all()
        )
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
        source_run = runs.get(source.ingestion_run_id)
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



__all__ = ["resolve_google_health_metric"]
