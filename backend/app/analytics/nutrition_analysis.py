"""Source-coherent canonical Analysis nutrition days and their named meal evidence."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, exists, not_, or_, select, true
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql.elements import ColumnElement

from app.analytics.provider_selection import NutritionProviderSelection
from app.analytics.service import PRIMARY_NUTRITION_METRICS
from app.analytics.verification import (
    _metadata_text,
    _normalized_meal_type,
    _nutrition_local_time,
    _safe_nutrition_display_name,
)
from app.nutrition.enums import (
    CoverageState,
    LineageState,
    ObservationRole,
    PresenceState,
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
from app.nutrition.resolution import (
    DAILY_PROJECTION_METRICS,
    MAX_PROVIDER_PERIOD_DAYS,
    ProviderCandidate,
    iter_provider_period_chunks,
)
from app.nutrition.resolution.metrics import canonical_unit
from app.nutrition.resolution.providers import ProviderNotAvailableError
from app.schemas import (
    AnalysisNutritionDayResponse,
    AnalysisNutritionMealResponse,
    AnalysisNutritionResponse,
    AnalysisSelectedProviderResponse,
    ReaderPeriodCapabilities,
)

MAX_ANALYSIS_NUTRITION_DAYS = 365
_PRESENT = (PresenceState.SUPPLIED.value, PresenceState.EXPLICIT_ZERO.value)
_SAFE_LINEAGE = (LineageState.CONFIRMED.value, LineageState.UNCERTAIN.value)


class AnalysisNutritionReadError(RuntimeError):
    """A selected source cannot safely provide the canonical Analysis contract."""


def _value(candidate: ProviderCandidate) -> Decimal | None:
    if candidate.resolution_state in {ResolutionState.CONFLICT, ResolutionState.DUPLICATE_CANDIDATE}:
        raise AnalysisNutritionReadError("unsafe canonical nutrition candidate")
    if (
        candidate.coverage_state is CoverageState.COMPLETE
        and candidate.resolution_state is ResolutionState.RESOLVED
        and candidate.presence_state in {PresenceState.SUPPLIED, PresenceState.EXPLICIT_ZERO}
    ):
        return candidate.value
    return None


def _has_eligible_primary_evidence(candidates: Mapping[str, ProviderCandidate]) -> bool:
    # Evaluate every metric first so secondary conflicts and duplicate candidates still fail closed.
    values = {key: _value(candidates[key]) for key in DAILY_PROJECTION_METRICS}
    return all(
        candidates[key].coverage_state is CoverageState.COMPLETE
        and candidates[key].resolution_state is ResolutionState.RESOLVED
        for key in PRIMARY_NUTRITION_METRICS
    ) and any(values[key] is not None for key in PRIMARY_NUTRITION_METRICS)


def _current_event_condition(event: type[NutritionConsumptionEvent]) -> ColumnElement[bool]:
    newer = aliased(NutritionConsumptionEvent)
    same_logical_event = and_(
        event.logical_event_key.is_not(None),
        newer.user_id == event.user_id,
        newer.provider_key == event.provider_key,
        newer.source_instance_id == event.source_instance_id,
        newer.logical_event_key == event.logical_event_key,
    )
    return or_(
        event.logical_event_key.is_(None),
        and_(
            not_(exists(select(1).where(same_logical_event, newer.revision > event.revision))),
            not_(exists(select(1).where(
                same_logical_event, newer.revision == event.revision, newer.id != event.id,
            ))),
        ),
    )


def _current_source_condition(source: type[NutritionSourceObservation]) -> ColumnElement[bool]:
    newer = aliased(NutritionSourceObservation)
    return or_(
        source.source_record_id.is_(None),
        not_(exists(select(1).where(
            newer.user_id == source.user_id,
            newer.provider_key == source.provider_key,
            newer.source_instance_id == source.source_instance_id,
            newer.source_namespace == source.source_namespace,
            newer.source_record_id == source.source_record_id,
            newer.source_revision > source.source_revision,
        ))),
    )


def _not_tombstoned(
    event: type[NutritionConsumptionEvent], source: type[NutritionSourceObservation],
) -> ColumnElement[bool]:
    tombstone = NutritionSourceTombstone
    link = aliased(NutritionExternalIdentityLink)
    newer_link = aliased(NutritionExternalIdentityLink)
    return and_(
        not_(exists(select(1).where(
            tombstone.user_id == source.user_id,
            tombstone.provider_key == source.provider_key,
            tombstone.source_instance_id == source.source_instance_id,
            or_(
                tombstone.source_observation_id == source.id,
                and_(
                    tombstone.source_observation_id.is_(None),
                    tombstone.source_namespace == source.source_namespace,
                    tombstone.source_record_id.is_not(None),
                    tombstone.source_record_id == source.source_record_id,
                ),
            ),
        ))),
        not_(exists(select(1).select_from(link).join(
            tombstone,
            and_(
                tombstone.user_id == link.user_id,
                tombstone.provider_key == event.provider_key,
                tombstone.source_instance_id == event.source_instance_id,
                tombstone.external_identity_id == link.external_identity_id,
                tombstone.source_namespace == source.source_namespace,
            ),
        ).where(
            link.user_id == event.user_id,
            link.consumption_event_id == event.id,
            link.link_role == "event_identity",
            not_(exists(select(1).where(
                newer_link.user_id == link.user_id,
                newer_link.external_identity_id == link.external_identity_id,
                newer_link.link_role == link.link_role,
                newer_link.link_revision > link.link_revision,
            ))),
        ))),
    )


def _safe_field(
    field: NutritionFieldObservation,
    source: NutritionSourceObservation,
    event: NutritionConsumptionEvent,
    snapshot: NutritionFoodSnapshot | None,
    parent_fields: Mapping[UUID, NutritionFieldObservation],
) -> bool:
    key = field.metric_key
    if (
        key not in DAILY_PROJECTION_METRICS
        or field.canonical_value is None
        or field.canonical_unit != canonical_unit(key)
        or field.presence_state not in _PRESENT
        or field.resolution_state != ResolutionState.RESOLVED.value
        or field.lineage_state not in _SAFE_LINEAGE
        or (field.coverage_state != CoverageState.COMPLETE.value and not (
            event.provider_key == "apple_health"
            and field.coverage_state == CoverageState.PARTIAL.value
        ))
    ):
        return False
    if event.provider_key != "yazio":
        return field.observation_role == ObservationRole.CANONICAL.value
    if event.event_kind == "simple_product":
        return source.source_namespace == "yazio.simple_product" and field.observation_role == ObservationRole.PROVIDER.value
    if (
        event.event_kind != "product"
        or source.source_namespace != "yazio.consumed_item"
        or field.observation_role != ObservationRole.DERIVED.value
        or snapshot is None
        or field.derived_from_field_observation_id is None
    ):
        return False
    parent = parent_fields.get(field.derived_from_field_observation_id)
    return bool(
        parent is not None
        and parent.observation_role == ObservationRole.PROVIDER.value
        and parent.metric_key == key
        and parent.source_observation_id == snapshot.source_observation_id
        and parent.user_id == event.user_id
    )


def _read_safe_meals(
    db: Session,
    *,
    user_id: UUID,
    days_by_source: Mapping[tuple[str, UUID], list[date]],
    event_ids: set[UUID] | None = None,
) -> list[AnalysisNutritionMealResponse]:
    """Read named event evidence in one batch, using the Analysis safety contract."""
    if not days_by_source or event_ids == set():
        return []
    event = NutritionConsumptionEvent
    source = NutritionSourceObservation
    snapshot_source = aliased(NutritionSourceObservation)
    scopes = or_(*(
        and_(event.provider_key == provider_key, event.source_instance_id == source_id,
             event.local_date.in_(days))
        for (provider_key, source_id), days in days_by_source.items()
    ))
    event_rows = db.execute(
        select(event, source, NutritionFoodSnapshot, snapshot_source)
        .join(source, and_(
            event.source_observation_id == source.id,
            event.user_id == source.user_id,
            event.provider_key == source.provider_key,
            event.source_instance_id == source.source_instance_id,
        ))
        .outerjoin(NutritionFoodSnapshot, and_(
            event.food_snapshot_id == NutritionFoodSnapshot.id,
            event.user_id == NutritionFoodSnapshot.user_id,
        ))
        .outerjoin(snapshot_source, and_(
            NutritionFoodSnapshot.source_observation_id == snapshot_source.id,
            NutritionFoodSnapshot.user_id == snapshot_source.user_id,
            event.provider_key == snapshot_source.provider_key,
            event.source_instance_id == snapshot_source.source_instance_id,
            not_(exists(select(1).where(
                NutritionSourceTombstone.user_id == snapshot_source.user_id,
                NutritionSourceTombstone.provider_key == snapshot_source.provider_key,
                NutritionSourceTombstone.source_instance_id == snapshot_source.source_instance_id,
                or_(
                    NutritionSourceTombstone.source_observation_id == snapshot_source.id,
                    and_(
                        NutritionSourceTombstone.source_observation_id.is_(None),
                        NutritionSourceTombstone.source_namespace == snapshot_source.source_namespace,
                        NutritionSourceTombstone.source_record_id.is_not(None),
                        NutritionSourceTombstone.source_record_id == snapshot_source.source_record_id,
                    ),
                ),
            ))),
        ))
        .where(
            event.user_id == user_id,
            source.user_id == user_id,
            scopes,
            event.id.in_(event_ids) if event_ids is not None else true(),
            event.presence_state.in_(_PRESENT),
            event.resolution_state == ResolutionState.RESOLVED.value,
            event.lineage_state.in_(_SAFE_LINEAGE),
            event.coverage_state.in_((CoverageState.COMPLETE.value, CoverageState.PARTIAL.value)),
            source.presence_state.in_(_PRESENT),
            source.resolution_state == ResolutionState.RESOLVED.value,
            source.lineage_state.in_(_SAFE_LINEAGE),
            source.coverage_state.in_((CoverageState.COMPLETE.value, CoverageState.PARTIAL.value)),
            _current_event_condition(event),
            _current_source_condition(source),
            _not_tombstoned(event, source),
        )
        .order_by(event.local_date, event.canonical_start_at, event.id)
    ).all()
    if not event_rows:
        return []
    source_ids = {source_row.id for _, source_row, _, _ in event_rows}
    fields = db.execute(
        select(NutritionFieldObservation, source)
        .join(source, and_(
            NutritionFieldObservation.source_observation_id == source.id,
            NutritionFieldObservation.user_id == source.user_id,
        ))
        .where(
            NutritionFieldObservation.user_id == user_id,
            source.user_id == user_id,
            source.id.in_(source_ids),
            or_(*(
                and_(source.provider_key == provider_key,
                     source.source_instance_id == source_id)
                for provider_key, source_id in days_by_source
            )),
            NutritionFieldObservation.metric_key.in_(DAILY_PROJECTION_METRICS),
        )
        .order_by(NutritionFieldObservation.created_at, NutritionFieldObservation.id)
    ).all()
    parent_ids = {
        field.derived_from_field_observation_id
        for field, _ in fields if field.derived_from_field_observation_id is not None
    }
    parents: dict[UUID, NutritionFieldObservation] = {}
    if parent_ids:
        parent_source = aliased(NutritionSourceObservation)
        parents = {
            field.id: field
            for field in db.scalars(
                select(NutritionFieldObservation)
                .join(parent_source, and_(
                    NutritionFieldObservation.source_observation_id == parent_source.id,
                    NutritionFieldObservation.user_id == parent_source.user_id,
                ))
                .where(
                    NutritionFieldObservation.user_id == user_id,
                    parent_source.user_id == user_id,
                    NutritionFieldObservation.id.in_(parent_ids),
                    or_(*(
                        and_(parent_source.provider_key == provider_key,
                             parent_source.source_instance_id == source_id)
                        for provider_key, source_id in days_by_source
                    )),
                )
            )
        }
    fields_by_source: dict[UUID, list[NutritionFieldObservation]] = defaultdict(list)
    for field, _ in fields:
        fields_by_source[field.source_observation_id].append(field)
    meals: list[AnalysisNutritionMealResponse] = []
    for item, observation, snapshot, snapshot_observation in event_rows:
        # A foreign-source or deleted snapshot cannot supply a meal name.
        snapshot_name = (
            snapshot.name
            if snapshot is not None and snapshot_observation is not None
            and isinstance(snapshot.name, str) and snapshot.name.strip()
            and snapshot.is_deleted is not True
            else None
        )
        metadata_key = (
            "food_display_name" if item.provider_key == "google_health"
            else "food_name" if item.provider_key == "apple_health"
            else None
        )
        name = _safe_nutrition_display_name(
            item,
            snapshot_name=snapshot_name,
            source_metadata=observation.provider_metadata or {},
            source_metadata_key=metadata_key,
        )
        if name is None:
            continue
        values: dict[str, Decimal] = {}
        for field in fields_by_source[item.source_observation_id]:
            if (
                _safe_field(field, observation, item, snapshot, parents)
                and field.metric_key is not None
                and field.canonical_value is not None
            ):
                values.setdefault(field.metric_key, field.canonical_value)
        meal_type = _normalized_meal_type(item.provider_key, item.daytime)
        if meal_type is None and item.daytime is None:
            meal_type = _normalized_meal_type(
                item.provider_key, _metadata_text(observation.provider_metadata or {}, "meal_type"),
            )
        serving_is_safe = (
            item.amount is not None and item.amount >= 0
            and item.amount_unit is not None and bool(item.amount_unit.strip())
        )
        meals.append(AnalysisNutritionMealResponse(
            event_id=item.id,
            local_date=item.local_date,
            serving_amount=item.amount if serving_is_safe else None,
            serving_unit=item.amount_unit if serving_is_safe else None,
            provider_key=item.provider_key,
            local_time=_nutrition_local_time(item),
            meal_type=meal_type,
            display_name=name,
            **{key: values.get(key) for key in DAILY_PROJECTION_METRICS},
        ))
    return meals


def _named_meals(
    db: Session,
    *,
    user_id: UUID,
    winners: Mapping[date, tuple[str, UUID, Mapping[str, ProviderCandidate]]],
    start: date,
    end: date,
) -> dict[date, list[AnalysisNutritionMealResponse]]:
    """Read only winning event scopes, in bounded windows and without N+1 queries."""
    meals: dict[date, list[AnalysisNutritionMealResponse]] = defaultdict(list)
    cursor = start
    while cursor <= end:
        chunk_end = (
            end if (end - cursor).days < MAX_PROVIDER_PERIOD_DAYS
            else cursor + timedelta(days=MAX_PROVIDER_PERIOD_DAYS - 1)
        )
        days_by_source: dict[tuple[str, UUID], list[date]] = defaultdict(list)
        for offset in range((chunk_end - cursor).days + 1):
            day = cursor + timedelta(days=offset)
            if day in winners:
                provider_key, source_id, _ = winners[day]
                days_by_source[provider_key, source_id].append(day)
        for meal in _read_safe_meals(db, user_id=user_id, days_by_source=days_by_source):
            meals[meal.local_date].append(meal)
        if chunk_end == end:
            break
        cursor = chunk_end + timedelta(days=1)
    return meals


def read_nutrition_meal_detail(
    db: Session,
    *,
    user_id: UUID,
    event_id: UUID,
    allowed_sources: tuple[tuple[str, UUID], ...],
) -> AnalysisNutritionMealResponse | None:
    """Resolve exactly one owned, currently permitted event; never infer identity."""
    item = db.scalar(select(NutritionConsumptionEvent).where(
        NutritionConsumptionEvent.id == event_id,
        NutritionConsumptionEvent.user_id == user_id,
    ))
    if item is None:
        return None
    pair = (item.provider_key, item.source_instance_id)
    if pair not in allowed_sources:
        return None
    if item.local_date is None:
        return None
    meals = _read_safe_meals(
        db,
        user_id=user_id,
        days_by_source={pair: [item.local_date]},
        event_ids={event_id},
    )
    return meals[0] if meals else None


def nutrition_detail_available_ids(
    db: Session,
    *,
    user_id: UUID,
    events: list[NutritionConsumptionEvent],
) -> set[UUID]:
    """Apply the exact detail eligibility to all daily diagnostic rows at once."""
    days_by_source: dict[tuple[str, UUID], list[date]] = defaultdict(list)
    for item in events:
        if item.local_date is None:
            continue
        pair = (item.provider_key, item.source_instance_id)
        if item.local_date not in days_by_source[pair]:
            days_by_source[pair].append(item.local_date)
    return {
        meal.event_id
        for meal in _read_safe_meals(
            db,
            user_id=user_id,
            days_by_source=days_by_source,
            event_ids={item.id for item in events},
        )
    }


def read_analysis_nutrition(
    db: Session,
    *,
    user_id: UUID,
    selection: NutritionProviderSelection,
    start: date,
    end: date,
) -> AnalysisNutritionResponse:
    if (
        type(start) is not date or type(end) is not date
        or start > end or (end - start).days >= MAX_ANALYSIS_NUTRITION_DAYS
    ):
        raise AnalysisNutritionReadError("canonical nutrition range is invalid")
    sources = selection.provider_sources or ((selection.provider_key, selection.source_instance_id),)
    if not sources or len({key for key, _ in sources}) != len(sources):
        raise AnalysisNutritionReadError("invalid canonical provider source chain")
    dates = tuple(start + timedelta(days=offset) for offset in range((end - start).days + 1))
    winners: dict[date, tuple[str, UUID, Mapping[str, ProviderCandidate]]] = {}
    try:
        for provider_key, source_id in sources:
            if len(winners) == len(dates):
                break
            for _, _, matrix in iter_provider_period_chunks(
                db, provider_key=provider_key, user_id=user_id,
                source_instance_id=source_id, start=start, end=end,
                metric_keys=DAILY_PROJECTION_METRICS,
            ):
                for day, candidates in matrix.items():
                    if day not in winners and _has_eligible_primary_evidence(candidates):
                        winners[day] = (provider_key, source_id, candidates)
    except (ProviderNotAvailableError, ValueError, KeyError, TypeError) as exc:
        raise AnalysisNutritionReadError("canonical nutrition provider period is unreadable") from exc
    meals = _named_meals(db, user_id=user_id, winners=winners, start=start, end=end)
    return AnalysisNutritionResponse(
        start_date=start,
        end_date=end,
        selected_provider=AnalysisSelectedProviderResponse(provider_key=selection.provider_key),
        period_days=len(dates),
        days=[
            AnalysisNutritionDayResponse(
                date=day,
                provider_key=winner[0] if winner is not None else None,
                meals=meals.get(day, []),
                **{
                    key: _value(winner[2][key]) if winner is not None else None
                    for key in DAILY_PROJECTION_METRICS
                },
            )
            for day in dates
            for winner in (winners.get(day),)
        ],
        # The requested window does not establish a complete reader start boundary.
        period_capabilities=ReaderPeriodCapabilities(
            supports_all_data=False, all_data_start_date=None,
        ),
    )
