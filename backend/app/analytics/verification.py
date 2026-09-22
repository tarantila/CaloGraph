from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, exists, not_, or_, select
from sqlalchemy.orm import Session, aliased

from app.activity import ACTIVE_ENERGY_METRIC, ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS
from app.analytics.provider_daily import ProviderDailyReadError, read_provider_daily_points
from app.analytics.provider_selection import NutritionProviderSelection, resolve_nutrition_provider
from app.analytics.scalar_selection import (
    ScalarProviderNotReady,
    ScalarProviderSelection,
    resolve_scalar_provider,
)
from app.models import HealthSample
from app.nutrition.enums import (
    ConsumptionEventKind,
    CoverageState,
    LineageState,
    ObservationRole,
    PresenceState,
    ResolutionState,
)
from app.nutrition.models import (
    NutritionConsumptionEvent,
    NutritionFieldObservation,
    NutritionFoodSnapshot,
    NutritionSourceObservation,
)
from app.nutrition.resolution.metrics import canonical_unit
from app.nutrition.resolution.sources import resolve_default_provider_sources
from app.provider_preferences import ACTIVITY_ENERGY_DATA_AREA
from app.schemas import (
    VerificationActivityDayResponse,
    VerificationActivityProviderRecord,
    VerificationActivityResponse,
    VerificationNutritionEvent,
    VerificationNutritionProviderGroup,
    VerificationNutritionResponse,
    VerificationNutritionSummary,
    VerificationView,
)
from app.services.apple_health_nutrition_ingestion import apple_health_source_instance_id
from app.services.provider_preferences import resolve_provider_source_type

_PROVIDER_KEYS = tuple(ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS)
_KNOWN_SOURCE_TYPES = tuple(
    source_type
    for source_types in ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS.values()
    for source_type in source_types
)


def _source_types_for_samples(
    provider_key: str, samples: list[HealthSample]
) -> list[str]:
    observed = {sample.source_type for sample in samples}
    return [
        source_type
        for source_type in ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS[provider_key]
        if source_type in observed
    ]


def _resolve_apple_transport(db: Session, user_id: UUID) -> str | None:
    try:
        return resolve_provider_source_type(
            db,
            user_id=user_id,
            data_area=ACTIVITY_ENERGY_DATA_AREA,
            provider_key="apple_health",
            configured=ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS["apple_health"],
        )
    except ValueError:
        return None


def _canonical_selection(
    db: Session, user_id: UUID
) -> tuple[ScalarProviderSelection | None, str | None]:
    try:
        selection = resolve_scalar_provider(
            db,
            user_id=user_id,
            data_area=ACTIVITY_ENERGY_DATA_AREA,
            source_types=ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS,
        )
    except ScalarProviderNotReady:
        if _resolve_apple_transport(db, user_id) is not None:
            raise
        source_types = dict(ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS)
        source_types["apple_health"] = (ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS["apple_health"][0],)
        return (
            resolve_scalar_provider(
                db,
                user_id=user_id,
                data_area=ACTIVITY_ENERGY_DATA_AREA,
                source_types=source_types,
            ),
            None,
        )
    return (
        selection,
        (
            selection.source_type
            if selection is not None and selection.provider_key == "apple_health"
            else None
        ),
    )


def _provider_record(
    *,
    provider_key: str,
    samples: list[HealthSample],
    apple_source_type: str | None = None,
) -> VerificationActivityProviderRecord:
    if not samples:
        return VerificationActivityProviderRecord(
            provider_key=provider_key,
            status="no_data",
            active_energy_kcal=None,
            record_count=0,
            source_types=[],
        )

    if provider_key == "apple_health":
        if apple_source_type is None:
            return VerificationActivityProviderRecord(
                provider_key=provider_key,
                status="unavailable",
                active_energy_kcal=None,
                record_count=len(samples),
                source_types=_source_types_for_samples(provider_key, samples),
            )
        samples = [sample for sample in samples if sample.source_type == apple_source_type]
        if not samples:
            return VerificationActivityProviderRecord(
                provider_key=provider_key,
                status="no_data",
                active_energy_kcal=None,
                record_count=0,
                source_types=[],
            )

    if provider_key == "google_health":
        active_energy_kcal = samples[0].value
    else:
        active_energy_kcal = sum((sample.value for sample in samples), Decimal())
    return VerificationActivityProviderRecord(
        provider_key=provider_key,
        status="available",
        active_energy_kcal=float(active_energy_kcal),
        record_count=len(samples),
        source_types=_source_types_for_samples(provider_key, samples),
    )


def read_activity_verification(
    db: Session,
    *,
    user_id: UUID,
    start: date,
    end: date,
    view: VerificationView,
) -> VerificationActivityResponse:
    """Read user-scoped activity evidence without blending provider totals."""
    selection: ScalarProviderSelection | None = None
    apple_source_type: str | None = None
    if view == "canonical":
        selection, apple_source_type = _canonical_selection(db, user_id)
    else:
        apple_source_type = _resolve_apple_transport(db, user_id)

    samples_by_provider_day: defaultdict[tuple[str, date], list[HealthSample]] = defaultdict(list)
    samples = db.scalars(
        select(HealthSample)
        .where(
            HealthSample.user_id == user_id,
            HealthSample.metric_type == ACTIVE_ENERGY_METRIC,
            HealthSample.local_date >= start,
            HealthSample.local_date <= end,
            HealthSample.source_type.in_(_KNOWN_SOURCE_TYPES),
        )
        .order_by(HealthSample.local_date, HealthSample.start_at, HealthSample.id)
    )
    source_to_provider = {
        source_type: provider_key
        for provider_key, source_types in ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS.items()
        for source_type in source_types
    }
    for sample in samples:
        samples_by_provider_day[(source_to_provider[sample.source_type], sample.local_date)].append(sample)

    days: list[VerificationActivityDayResponse] = []
    for offset in range((end - start).days + 1):
        local_date = start + timedelta(days=offset)
        if view == "canonical":
            canonical = (
                None
                if selection is None
                else _provider_record(
                    provider_key=selection.provider_key,
                    samples=samples_by_provider_day[(selection.provider_key, local_date)],
                    apple_source_type=apple_source_type,
                )
            )
            days.append(
                VerificationActivityDayResponse(
                    date=local_date,
                    canonical=canonical,
                    providers=[],
                )
            )
            continue
        days.append(
            VerificationActivityDayResponse(
                date=local_date,
                canonical=None,
                providers=[
                    _provider_record(
                        provider_key=provider_key,
                        samples=samples_by_provider_day[(provider_key, local_date)],
                        apple_source_type=apple_source_type,
                    )
                    for provider_key in _PROVIDER_KEYS
                ],
            )
        )

    return VerificationActivityResponse(
        start_date=start,
        end_date=end,
        view=view,
        days=days,
    )


_NUTRITION_PROVIDER_KEYS = ("google_health", "yazio", "apple_health")
_NUTRITION_METRIC_KEYS = (
    "dietary_energy_kcal",
    "protein_g",
    "carbohydrates_g",
    "fat_g",
)
_UNNAMED_FOOD = "Unbenannter Eintrag"


def _empty_nutrition_summary() -> VerificationNutritionSummary:
    return VerificationNutritionSummary(
        calories_kcal=None,
        protein_g=None,
        carbohydrates_g=None,
        fat_g=None,
    )


def _read_nutrition_summary(
    db: Session,
    *,
    user_id: UUID,
    provider_key: str,
    source_instance_id: UUID,
    local_date: date,
) -> VerificationNutritionSummary:
    try:
        points = read_provider_daily_points(
            db,
            user_id=user_id,
            provider_key=provider_key,
            source_instance_id=source_instance_id,
            start=local_date,
            end=local_date,
        )
    except ProviderDailyReadError:
        return _empty_nutrition_summary()
    if not points:
        return _empty_nutrition_summary()
    point = points[0]
    return VerificationNutritionSummary(
        calories_kcal=point.calories_kcal,
        protein_g=point.protein_g,
        carbohydrates_g=point.carbs_g,
        fat_g=point.fat_g,
    )


def _all_nutrition_sources(db: Session, user_id: UUID) -> dict[str, UUID | None]:
    bindings = resolve_default_provider_sources(
        db,
        user_id=user_id,
        provider_keys=("google_health", "yazio"),
    )
    return {
        "google_health": (
            binding.source_instance_id
            if (binding := bindings.for_provider("google_health")) is not None
            else None
        ),
        "yazio": (
            binding.source_instance_id
            if (binding := bindings.for_provider("yazio")) is not None
            else None
        ),
        "apple_health": apple_health_source_instance_id(user_id),
    }


def _source_pair_predicates(
    source: type[NutritionSourceObservation],
    source_instances: dict[str, UUID | None],
) -> list[object]:
    return [
        and_(
            source.provider_key == provider_key,
            source.source_instance_id == source_instance_id,
        )
        for provider_key, source_instance_id in source_instances.items()
        if source_instance_id is not None
    ]


def _read_current_nutrition_events(
    db: Session,
    *,
    user_id: UUID,
    local_date: date,
    source_instances: dict[str, UUID | None],
) -> list[NutritionConsumptionEvent]:
    source = NutritionSourceObservation
    pairs = _source_pair_predicates(source, source_instances)
    if not pairs:
        return []
    event = NutritionConsumptionEvent
    higher_revision = aliased(NutritionConsumptionEvent)
    has_higher_revision = exists(
        select(1).where(
            higher_revision.user_id == user_id,
            higher_revision.provider_key == event.provider_key,
            higher_revision.source_instance_id == event.source_instance_id,
            higher_revision.logical_event_key == event.logical_event_key,
            higher_revision.revision > event.revision,
        )
    )
    rows = list(
        db.scalars(
            select(event)
            .join(
                source,
                (event.source_observation_id == source.id)
                & (event.user_id == source.user_id)
                & (event.provider_key == source.provider_key)
                & (event.source_instance_id == source.source_instance_id),
            )
            .where(
                event.user_id == user_id,
                source.user_id == user_id,
                event.local_date == local_date,
                or_(event.logical_event_key.is_(None), not_(has_higher_revision)),
                or_(*pairs),
            )
            .order_by(event.canonical_start_at, event.id)
        )
    )
    return sorted(
        rows,
        key=lambda item: (
            item.canonical_start_at is not None,
            (
                (
                    item.canonical_start_at.replace(tzinfo=UTC)
                    if item.canonical_start_at.tzinfo is None
                    else item.canonical_start_at.astimezone(UTC)
                ).isoformat()
                if item.canonical_start_at is not None
                else ""
            ),
            str(item.id),
        ),
    )


def _nutrition_field_is_safe(
    field: NutritionFieldObservation,
    *,
    source: NutritionSourceObservation,
    event: NutritionConsumptionEvent,
    snapshots: dict[UUID, NutritionFoodSnapshot],
    parent_fields: dict[UUID, NutritionFieldObservation],
) -> bool:
    if (
        field.metric_key not in _NUTRITION_METRIC_KEYS
        or field.canonical_value is None
        or field.canonical_unit != canonical_unit(field.metric_key)
        or field.presence_state
        not in (PresenceState.SUPPLIED.value, PresenceState.EXPLICIT_ZERO.value)
        or field.resolution_state != ResolutionState.RESOLVED.value
        or field.lineage_state
        not in (LineageState.CONFIRMED.value, LineageState.UNCERTAIN.value)
    ):
        return False
    if (
        field.coverage_state != CoverageState.COMPLETE.value
        and not (
            event.provider_key == "apple_health"
            and field.coverage_state == CoverageState.PARTIAL.value
        )
    ):
        return False
    if event.provider_key != "yazio":
        return field.observation_role == ObservationRole.CANONICAL.value
    if event.event_kind == ConsumptionEventKind.SIMPLE_PRODUCT.value:
        return (
            source.source_namespace == "yazio.simple_product"
            and field.observation_role == ObservationRole.PROVIDER.value
        )
    if (
        event.event_kind != ConsumptionEventKind.PRODUCT.value
        or source.source_namespace != "yazio.consumed_item"
        or field.observation_role != ObservationRole.DERIVED.value
        or field.derived_from_field_observation_id is None
    ):
        return False
    snapshot = snapshots.get(event.food_snapshot_id)
    parent = parent_fields.get(field.derived_from_field_observation_id)
    return bool(
        snapshot is not None
        and parent is not None
        and parent.observation_role == ObservationRole.PROVIDER.value
        and parent.metric_key == field.metric_key
        and parent.source_observation_id == snapshot.source_observation_id
    )


def _read_nutrition_event_metrics(
    db: Session,
    *,
    user_id: UUID,
    events: list[NutritionConsumptionEvent],
    source_instances: dict[str, UUID | None],
) -> dict[UUID, dict[str, Decimal]]:
    source_ids = {item.source_observation_id for item in events}
    if not source_ids:
        return {}
    source = NutritionSourceObservation
    pairs = _source_pair_predicates(source, source_instances)
    if not pairs:
        return {}
    field_rows = db.execute(
        select(NutritionFieldObservation, source)
        .join(
            source,
            (NutritionFieldObservation.source_observation_id == source.id)
            & (NutritionFieldObservation.user_id == source.user_id),
        )
        .where(
            NutritionFieldObservation.user_id == user_id,
            source.user_id == user_id,
            NutritionFieldObservation.source_observation_id.in_(source_ids),
            NutritionFieldObservation.metric_key.in_(_NUTRITION_METRIC_KEYS),
            NutritionFieldObservation.observation_role.in_(
                (
                    ObservationRole.CANONICAL.value,
                    ObservationRole.PROVIDER.value,
                    ObservationRole.DERIVED.value,
                )
            ),
            or_(*pairs),
        )
        .order_by(
            NutritionFieldObservation.source_observation_id,
            NutritionFieldObservation.metric_key,
            NutritionFieldObservation.created_at,
            NutritionFieldObservation.id,
        )
    ).all()
    snapshot_ids = {
        item.food_snapshot_id for item in events if item.food_snapshot_id is not None
    }
    snapshots = {
        item.id: item
        for item in (
            db.scalars(
                select(NutritionFoodSnapshot)
                .join(
                    source,
                    (NutritionFoodSnapshot.source_observation_id == source.id)
                    & (NutritionFoodSnapshot.user_id == source.user_id),
                )
                .where(
                    NutritionFoodSnapshot.user_id == user_id,
                    source.user_id == user_id,
                    NutritionFoodSnapshot.id.in_(snapshot_ids),
                    or_(*pairs),
                )
            )
            if snapshot_ids
            else ()
        )
    }
    parent_ids = {
        field.derived_from_field_observation_id
        for field, _source in field_rows
        if field.derived_from_field_observation_id is not None
    }
    parent_fields: dict[UUID, NutritionFieldObservation] = {}
    if parent_ids:
        parent_source = aliased(NutritionSourceObservation)
        parent_pairs = _source_pair_predicates(parent_source, source_instances)
        parent_fields = {
            field.id: field
            for field in db.scalars(
                select(NutritionFieldObservation)
                .join(
                    parent_source,
                    (NutritionFieldObservation.source_observation_id == parent_source.id)
                    & (NutritionFieldObservation.user_id == parent_source.user_id),
                )
                .where(
                    NutritionFieldObservation.user_id == user_id,
                    parent_source.user_id == user_id,
                    NutritionFieldObservation.id.in_(parent_ids),
                    or_(*parent_pairs),
                )
            )
        }
    events_by_source = {item.source_observation_id: item for item in events}
    metrics: dict[UUID, dict[str, Decimal]] = defaultdict(dict)
    for field, field_source in field_rows:
        event = events_by_source.get(field.source_observation_id)
        if event is None or not _nutrition_field_is_safe(
            field,
            source=field_source,
            event=event,
            snapshots=snapshots,
            parent_fields=parent_fields,
        ):
            continue
        if field.metric_key is not None and field.canonical_value is not None:
            metrics[field.source_observation_id].setdefault(
                field.metric_key, field.canonical_value
            )
    return metrics


def _read_nutrition_food_names(
    db: Session,
    *,
    user_id: UUID,
    events: list[NutritionConsumptionEvent],
    source_instances: dict[str, UUID | None],
) -> dict[UUID, str | None]:
    snapshot_ids = {item.food_snapshot_id for item in events if item.food_snapshot_id is not None}
    if not snapshot_ids:
        return {}
    source = NutritionSourceObservation
    pairs = _source_pair_predicates(source, source_instances)
    if not pairs:
        return {}
    snapshots = db.scalars(
        select(NutritionFoodSnapshot)
        .join(
            source,
            (NutritionFoodSnapshot.source_observation_id == source.id)
            & (NutritionFoodSnapshot.user_id == source.user_id),
        )
        .where(
            NutritionFoodSnapshot.user_id == user_id,
            source.user_id == user_id,
            NutritionFoodSnapshot.id.in_(snapshot_ids),
            or_(*pairs),
        )
    )
    return {snapshot.id: snapshot.name for snapshot in snapshots}


def _nutrition_event_response(
    event: NutritionConsumptionEvent,
    *,
    metrics: dict[str, Decimal],
    food_names: dict[UUID, str | None],
) -> VerificationNutritionEvent:
    serving_is_safe = (
        event.amount is not None
        and event.amount >= 0
        and event.amount_unit is not None
        and bool(event.amount_unit.strip())
    )
    return VerificationNutritionEvent(
        provider_key=event.provider_key,
        occurred_at=event.canonical_start_at,
        meal_type=event.daytime,
        food_name=food_names.get(event.food_snapshot_id) or _UNNAMED_FOOD,
        calories_kcal=metrics.get("dietary_energy_kcal"),
        protein_g=metrics.get("protein_g"),
        carbohydrates_g=metrics.get("carbohydrates_g"),
        fat_g=metrics.get("fat_g"),
        serving_amount=event.amount if serving_is_safe else None,
        serving_unit=event.amount_unit if serving_is_safe else None,
    )


def _nutrition_provider_group(
    db: Session,
    *,
    user_id: UUID,
    provider_key: str,
    source_instance_id: UUID | None,
    local_date: date,
    events: list[NutritionConsumptionEvent],
    metrics_by_source: dict[UUID, dict[str, Decimal]],
    food_names: dict[UUID, str | None],
) -> VerificationNutritionProviderGroup:
    summary = (
        _empty_nutrition_summary()
        if source_instance_id is None
        else _read_nutrition_summary(
            db,
            user_id=user_id,
            provider_key=provider_key,
            source_instance_id=source_instance_id,
            local_date=local_date,
        )
    )
    response_events = [
        _nutrition_event_response(
            event,
            metrics=metrics_by_source.get(event.source_observation_id, {}),
            food_names=food_names,
        )
        for event in events
    ]
    has_summary = any(
        value is not None
        for value in (
            summary.calories_kcal,
            summary.protein_g,
            summary.carbohydrates_g,
            summary.fat_g,
        )
    )
    return VerificationNutritionProviderGroup(
        provider_key=provider_key,
        status="available" if response_events or has_summary else "no_data",
        record_count=len(response_events),
        summary=summary,
        events=response_events,
    )


def read_nutrition_verification(
    db: Session,
    *,
    user_id: UUID,
    local_date: date,
    view: VerificationView,
) -> VerificationNutritionResponse:
    """Read user-scoped nutrition evidence without blending provider totals."""
    if view == "canonical":
        selection: NutritionProviderSelection | None = resolve_nutrition_provider(
            db, user_id=user_id
        )
        if selection is None:
            return VerificationNutritionResponse(
                date=local_date,
                view=view,
                canonical=None,
                providers=[],
            )
        source_instances = {selection.provider_key: selection.source_instance_id}
        events = _read_current_nutrition_events(
            db,
            user_id=user_id,
            local_date=local_date,
            source_instances=source_instances,
        )
        metrics_by_source = _read_nutrition_event_metrics(
            db,
            user_id=user_id,
            events=events,
            source_instances=source_instances,
        )
        food_names = _read_nutrition_food_names(
            db,
            user_id=user_id,
            events=events,
            source_instances=source_instances,
        )
        canonical_events = [
            item for item in events if item.provider_key == selection.provider_key
        ]
        return VerificationNutritionResponse(
            date=local_date,
            view=view,
            canonical=_nutrition_provider_group(
                db,
                user_id=user_id,
                provider_key=selection.provider_key,
                source_instance_id=selection.source_instance_id,
                local_date=local_date,
                events=canonical_events,
                metrics_by_source=metrics_by_source,
                food_names=food_names,
            ),
            providers=[],
        )

    source_instances = _all_nutrition_sources(db, user_id)
    events = _read_current_nutrition_events(
        db,
        user_id=user_id,
        local_date=local_date,
        source_instances=source_instances,
    )
    metrics_by_source = _read_nutrition_event_metrics(
        db,
        user_id=user_id,
        events=events,
        source_instances=source_instances,
    )
    food_names = _read_nutrition_food_names(
        db,
        user_id=user_id,
        events=events,
        source_instances=source_instances,
    )
    return VerificationNutritionResponse(
        date=local_date,
        view=view,
        canonical=None,
        providers=[
            _nutrition_provider_group(
                db,
                user_id=user_id,
                provider_key=provider_key,
                source_instance_id=source_instances[provider_key],
                local_date=local_date,
                events=[item for item in events if item.provider_key == provider_key],
                metrics_by_source=metrics_by_source,
                food_names=food_names,
            )
            for provider_key in _NUTRITION_PROVIDER_KEYS
        ],
    )
