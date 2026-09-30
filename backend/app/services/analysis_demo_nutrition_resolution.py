"""Read only persisted Analysis demo event evidence; never contact providers."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.nutrition.enums import CoverageState, LineageState, PresenceState, ResolutionState
from app.nutrition.models import NutritionConsumptionEvent, NutritionFieldObservation
from app.nutrition.resolution.contracts import (
    MetricContribution,
    ProviderCandidate,
    build_event_candidate,
)
from app.nutrition.resolution.metrics import CANONICAL_NUTRITION_METRICS, canonical_unit
from app.nutrition.resolution.reasons import EvidenceKind, ReasonCode

_PROVIDER = "analysis_demo"


def require_analysis_demo_source(db: Session, *, user_id: UUID, source_instance_id: UUID) -> None:
    from app.services.analysis_demo import (
        _require_disposable_environment,
        demo_source_instance_id,
        demo_user_id,
        is_owned_analysis_demo_account,
    )

    _require_disposable_environment()
    if user_id != demo_user_id() or source_instance_id != demo_source_instance_id(user_id):
        raise ValueError("analysis demo source belongs only to the dedicated account")
    if not is_owned_analysis_demo_account(db, user_id):
        raise ValueError("analysis demo account is unavailable")


def _empty(user_id: UUID, day: date, metric: str) -> ProviderCandidate:
    return ProviderCandidate(
        provider_key=_PROVIDER, user_id=user_id, local_date=day, metric_key=metric,
        value=None, unit=None, selected_granularity=None,
        presence_state=PresenceState.MISSING, coverage_state=CoverageState.UNKNOWN,
        resolution_state=ResolutionState.UNRESOLVED, lineage_state=LineageState.UNKNOWN,
        source_lineage=(), reason_code=ReasonCode.ALL_SOURCES_MISSING,
    )


def resolve_analysis_demo_period(
    db: Session, *, user_id: UUID, source_instance_id: UUID,
    start: date, end: date, metric_keys: Sequence[str],
    skip_source_instance_validation: bool = False,
) -> Mapping[date, Mapping[str, ProviderCandidate]]:
    """Produce every date/metric, distinguishing persisted zero from no event."""
    # Chunking may skip repeated lookup but must not skip identity or environment.
    require_analysis_demo_source(db, user_id=user_id, source_instance_id=source_instance_id)
    if type(start) is not date or type(end) is not date or start > end or (end - start).days >= 31:
        raise ValueError("analysis demo period must contain at most 31 ascending dates")
    requested = tuple(metric_keys)
    if len(requested) != len(set(requested)) or any(key not in CANONICAL_NUTRITION_METRICS for key in requested):
        raise ValueError("analysis demo metric must be canonical and unique")
    events = tuple(db.scalars(select(NutritionConsumptionEvent).where(
        NutritionConsumptionEvent.user_id == user_id,
        NutritionConsumptionEvent.provider_key == _PROVIDER,
        NutritionConsumptionEvent.source_instance_id == source_instance_id,
        NutritionConsumptionEvent.local_date >= start,
        NutritionConsumptionEvent.local_date <= end,
    )))
    event_by_source = {event.source_observation_id: event for event in events}
    fields = tuple(db.scalars(select(NutritionFieldObservation).where(
        NutritionFieldObservation.user_id == user_id,
        NutritionFieldObservation.source_observation_id.in_(event_by_source),
        NutritionFieldObservation.metric_key.in_(requested),
    ))) if event_by_source and requested else ()
    grouped: dict[tuple[date, str], list[MetricContribution]] = defaultdict(list)
    for field in fields:
        event = event_by_source[field.source_observation_id]
        metric_key = field.metric_key
        local_date = event.local_date
        if metric_key is None or local_date is None:
            continue
        unit = canonical_unit(metric_key)
        value = field.canonical_value
        if value is None or field.canonical_unit != unit:
            continue
        if field.presence_state != (PresenceState.EXPLICIT_ZERO.value if value == 0 else PresenceState.SUPPLIED.value):
            continue
        grouped[local_date, metric_key].append(MetricContribution(
            evidence_id=field.id, source_observation_id=field.source_observation_id,
            metric_key=metric_key, value=value, unit=unit,
            presence_state=PresenceState(field.presence_state),
            resolution_state=ResolutionState.RESOLVED,
            lineage_state=LineageState.CONFIRMED,
            evidence_kind=EvidenceKind.FIELD_OBSERVATION,
        ))
    result: dict[date, dict[str, ProviderCandidate]] = {}
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        day_result: dict[str, ProviderCandidate] = {}
        for metric in requested:
            contributions = grouped.get((day, metric), ())
            if not contributions:
                day_result[metric] = _empty(user_id, day, metric)
                continue
            candidate = build_event_candidate(
                provider_key=_PROVIDER, user_id=user_id, local_date=day,
                metric_key=metric, contributions=tuple(contributions),
            )
            day_result[metric] = ProviderCandidate(
                provider_key=_PROVIDER, user_id=user_id, local_date=day,
                metric_key=metric, value=candidate.value, unit=candidate.unit,
                selected_granularity=candidate.selected_granularity,
                presence_state=candidate.presence_state, coverage_state=candidate.coverage_state,
                resolution_state=candidate.resolution_state,
                lineage_state=candidate.lineage_state,
                source_lineage=candidate.source_lineage, reason_code=candidate.reason_code,
            )
        result[day] = day_result
    return result


class AnalysisDemoMetricResolver:
    provider_key = _PROVIDER

    def owns_source_instance_id(
        self, db: Session, *, user_id: UUID, source_instance_id: UUID,
    ) -> bool:
        try:
            require_analysis_demo_source(db, user_id=user_id, source_instance_id=source_instance_id)
        except ValueError:
            return False
        return True

    def resolve_metric(
        self, db: Session, *, user_id: UUID, source_instance_id: UUID,
        local_date: date, metric_key: str,
    ) -> ProviderCandidate:
        return resolve_analysis_demo_period(
            db, user_id=user_id, source_instance_id=source_instance_id,
            start=local_date, end=local_date, metric_keys=(metric_key,),
        )[local_date][metric_key]
