"""Persistence boundary for already-fetched Google Health Nutrition Log DTOs."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.google_health.client import (
    NutritionDataSource,
    NutritionLogDataPoint,
    NutritionQuantity,
    NutritionServing,
)
from app.models import GoogleHealthConnection
from app.nutrition.enums import (
    ConsumptionEventKind,
    CoverageState,
    LineageState,
    ObservationKind,
    ObservationRole,
    PresenceState,
    ResolutionState,
    ServingScope,
)
from app.nutrition.models import (
    NutritionConsumptionEvent,
    NutritionFieldObservation,
    NutritionIngestionRun,
    NutritionProvenance,
    NutritionServingObservation,
)
from app.nutrition.repositories import (
    create_ingestion_run,
    get_current_consumption_event,
    get_or_create_consumption_event,
    get_or_create_external_identity,
    get_or_create_identity_link,
    get_or_create_source_observation,
)

_PROVIDER = "google_health"
_DEFAULT_CONNECTOR = "google-health-api-v4"
_SOURCE_NAMESPACE = "google_health.nutrition_log"
_FOOD_NAMESPACE = "google_health.food"
_MAX_METADATA_ITEMS = 32
_MAX_METADATA_TEXT = 512
_MAX_PATH = 255
_SENSITIVE_TERMS = (
    "raw",
    "payload",
    "token",
    "secret",
    "credential",
    "auth",
    "password",
    "refresh",
    "response",
    "key",
)

# These are the canonical fields approved for this adapter.  Nutrient names
# not in this table remain provider evidence and deliberately do not become
# canonical domain metrics.
_NUTRIENT_METRICS = {
    "PROTEIN": ("protein_g", "g"),
    "DIETARY_FIBER": ("fiber_g", "g"),
    "SUGAR": ("sugar_g", "g"),
    "SATURATED_FAT": ("saturated_fat_g", "g"),
}

# The DTO normally carries user-provided units, while tests and callers may
# construct it directly with short semantic units.  Resource names are never
# accepted here as a serving unit.
_SEMANTIC_UNITS = {
    "g",
    "gram",
    "grams",
    "kg",
    "kilogram",
    "kilograms",
    "mg",
    "milligram",
    "milligrams",
    "mcg",
    "ug",
    "oz",
    "ounce",
    "ounces",
    "lb",
    "pound",
    "pounds",
    "ml",
    "milliliter",
    "milliliters",
    "l",
    "liter",
    "liters",
    "kcal",
    "cal",
    "calorie",
    "calories",
}


def _json_value(value: Any) -> Any:
    """Return a stable, JSON-safe representation without DTO repr leakage."""

    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _json_value(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return {"__decimal__": format(value, "f")}
    if isinstance(value, float):
        return {"__decimal__": format(Decimal(str(value)), "f")}
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(nested) for key, nested in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(
        _json_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_metadata(metadata: Mapping[str, Any] | None) -> dict[str, str | int | float | bool | None]:
    """Keep only bounded scalar metadata and reject sensitive names/values."""

    if not metadata:
        return {}
    result: dict[str, str | int | float | bool | None] = {}
    for key, value in metadata.items():
        if not isinstance(key, str) or not key or len(key) > 128:
            continue
        key_lower = key.lower()
        if any(term in key_lower for term in _SENSITIVE_TERMS):
            continue
        if value is not None and not isinstance(value, (str, int, float, bool)):
            continue
        if isinstance(value, str):
            if len(value) > _MAX_METADATA_TEXT:
                continue
            if any(term in value.lower() for term in _SENSITIVE_TERMS):
                continue
        result[key] = value
        if len(result) >= _MAX_METADATA_ITEMS:
            break
    return {key: result[key] for key in sorted(result)}


def _data_source_metadata(source: NutritionDataSource | None) -> dict[str, Any]:
    if source is None:
        return {}
    metadata: dict[str, Any] = {
        "recording_method": source.recording_method,
        "platform": source.platform,
    }
    if source.device is not None:
        metadata.update(
            {
                "device_form_factor": source.device.form_factor,
                "device_manufacturer": source.device.manufacturer,
                "device_display_name": source.device.display_name,
            }
        )
    if source.application is not None:
        metadata.update(
            {
                "application_package_name": source.application.package_name,
                "application_web_client_id": source.application.web_client_id,
                "application_google_web_client_id": source.application.google_web_client_id,
            }
        )
    return _safe_metadata(metadata)


def _decimal(quantity: NutritionQuantity | None) -> Decimal | None:
    if quantity is None:
        return None
    return Decimal(str(quantity.value))


def _presence(value: Decimal | None) -> str:
    if value is None:
        return PresenceState.MISSING.value
    return PresenceState.EXPLICIT_ZERO.value if value == 0 else PresenceState.SUPPLIED.value


def _civil(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=None) if value is not None else None


def _safe_path(path: str) -> str:
    if len(path) <= _MAX_PATH:
        return path
    digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]
    return f"{path[: _MAX_PATH - 17]}-{digest}"


def _serving_unit(value: str | None) -> str | None:
    if value is None:
        return None
    lowered = value.strip().lower()
    if lowered in _SEMANTIC_UNITS and len(value) <= 32:
        return value
    return None


def _provenance(
    db: Session,
    *,
    user_id: UUID,
    source_observation_id: UUID,
    consumption_event_id: UUID | None = None,
    serving_observation_id: UUID | None = None,
    field_observation_id: UUID | None = None,
) -> None:
    targets = (consumption_event_id, serving_observation_id, field_observation_id)
    if sum(target is not None for target in targets) != 1:
        raise ValueError("provenance must have exactly one target")
    filters = [
        NutritionProvenance.user_id == user_id,
        NutritionProvenance.source_observation_id == source_observation_id,
        NutritionProvenance.role == ObservationRole.PROVIDER.value,
    ]
    columns = (
        NutritionProvenance.consumption_event_id,
        NutritionProvenance.serving_observation_id,
        NutritionProvenance.field_observation_id,
    )
    filters.extend(column == target for column, target in zip(columns, targets, strict=True))
    if db.scalar(select(NutritionProvenance).where(*filters)) is not None:
        return
    db.add(
        NutritionProvenance(
            user_id=user_id,
            source_observation_id=source_observation_id,
            consumption_event_id=consumption_event_id,
            serving_observation_id=serving_observation_id,
            field_observation_id=field_observation_id,
            role=ObservationRole.PROVIDER.value,
            lineage_state=LineageState.CONFIRMED.value,
        )
    )
    db.flush()


def _field(
    db: Session,
    *,
    user_id: UUID,
    source_observation_id: UUID,
    provider_field_path: str,
    value: Decimal | None,
    raw_text: str | None = None,
    raw_unit: str | None = None,
    metric_key: str | None = None,
    canonical_value: Decimal | None = None,
    canonical_unit: str | None = None,
    role: str = ObservationRole.PROVIDER.value,
    coverage_state: str = CoverageState.COMPLETE.value,
    metadata: Mapping[str, Any] | None = None,
) -> NutritionFieldObservation:
    provider_field_path = _safe_path(provider_field_path)
    existing = db.scalar(
        select(NutritionFieldObservation).where(
            NutritionFieldObservation.user_id == user_id,
            NutritionFieldObservation.source_observation_id == source_observation_id,
            NutritionFieldObservation.provider_field_path == provider_field_path,
            NutritionFieldObservation.observation_role == role,
        )
    )
    if existing is None:
        existing = NutritionFieldObservation(
            user_id=user_id,
            source_observation_id=source_observation_id,
            provider_field_path=provider_field_path,
            provider_raw_value_decimal=value,
            provider_raw_value_text=raw_text,
            provider_raw_unit=raw_unit,
            metric_key=metric_key,
            canonical_value=canonical_value,
            canonical_unit=canonical_unit,
            observation_role=role,
            presence_state=_presence(value),
            coverage_state=coverage_state,
            resolution_state=ResolutionState.RESOLVED.value,
            lineage_state=LineageState.CONFIRMED.value,
            provider_metadata=_safe_metadata(metadata),
        )
        db.add(existing)
    else:
        existing.provider_raw_value_decimal = value
        existing.provider_raw_value_text = raw_text
        existing.provider_raw_unit = raw_unit
        existing.metric_key = metric_key
        existing.canonical_value = canonical_value
        existing.canonical_unit = canonical_unit
        existing.presence_state = _presence(value)
        existing.coverage_state = coverage_state
        existing.provider_metadata = _safe_metadata(metadata)
    db.flush()
    _provenance(db, user_id=user_id, source_observation_id=source_observation_id, field_observation_id=existing.id)
    return existing


def _serving(
    db: Session,
    *,
    user_id: UUID,
    source_observation_id: UUID,
    event_id: UUID,
    serving: NutritionServing,
    metadata: Mapping[str, Any],
) -> NutritionServingObservation:
    quantity = _decimal(NutritionQuantity(serving.amount, None)) if serving.amount is not None else None
    unit = _serving_unit(serving.food_measurement_unit)
    existing = db.scalar(
        select(NutritionServingObservation).where(
            NutritionServingObservation.user_id == user_id,
            NutritionServingObservation.consumption_event_id == event_id,
            NutritionServingObservation.serving_scope == ServingScope.EVENT.value,
        )
    )
    if existing is None:
        existing = NutritionServingObservation(
            user_id=user_id,
            consumption_event_id=event_id,
            serving_scope=ServingScope.EVENT.value,
            label=serving.food_measurement_unit_display_name,
            quantity=quantity,
            amount=quantity,
            unit=unit,
            provider_field_path="nutritionLog.serving",
            provider_metadata=_safe_metadata(metadata),
        )
        db.add(existing)
    db.flush()
    _provenance(db, user_id=user_id, source_observation_id=source_observation_id, serving_observation_id=existing.id)
    return existing


def _field_observations(
    db: Session,
    *,
    user_id: UUID,
    source_observation_id: UUID,
    point: NutritionLogDataPoint,
    coverage_state: str,
    metadata: Mapping[str, Any],
) -> None:
    log = point.nutrition_log
    # Provider evidence is retained for every supplied direct fact.  Canonical
    # observations are emitted once, and top-level totals take precedence over
    # duplicate nutrient-array values.
    energy = _decimal(log.energy)
    _field(
        db,
        user_id=user_id,
        source_observation_id=source_observation_id,
        provider_field_path="nutritionLog.energy",
        value=energy,
        raw_unit=log.energy.unit if log.energy else None,
        metric_key="dietary_energy_kcal" if energy is not None else None,
        canonical_value=energy,
        canonical_unit="kcal" if energy is not None else None,
        role=ObservationRole.CANONICAL.value if energy is not None else ObservationRole.PROVIDER.value,
        metadata=metadata,
        coverage_state=coverage_state,
    )
    energy_from_fat = _decimal(log.energy_from_fat)
    if log.energy_from_fat is not None:
        _field(
            db,
            user_id=user_id,
            source_observation_id=source_observation_id,
            provider_field_path="nutritionLog.energyFromFat",
            value=energy_from_fat,
            raw_unit=log.energy_from_fat.unit,
            metadata=metadata,
            coverage_state=coverage_state,
        )

    direct_totals = (
        ("totalCarbohydrate", log.total_carbohydrate, "carbohydrates_g"),
        ("totalFat", log.total_fat, "fat_g"),
    )
    direct_metric_names = {name for name, quantity, _ in direct_totals if quantity is not None}
    for name, quantity, metric in direct_totals:
        value = _decimal(quantity)
        if quantity is None:
            continue
        _field(
            db,
            user_id=user_id,
            source_observation_id=source_observation_id,
            provider_field_path=f"nutritionLog.{name}",
            value=value,
            raw_unit=quantity.unit,
            metric_key=metric,
            canonical_value=value,
            canonical_unit="g",
            role=ObservationRole.CANONICAL.value,
            metadata=metadata,
            coverage_state=coverage_state,
        )

    for index, nutrient in enumerate(log.nutrients):
        value = _decimal(nutrient.quantity)
        provider_path = f"nutritionLog.nutrients[{index}].{nutrient.nutrient}"
        canonical = _NUTRIENT_METRICS.get(nutrient.nutrient)
        if nutrient.nutrient == "CARBOHYDRATES":
            canonical = ("carbohydrates_g", "g")
        elif nutrient.nutrient == "FAT":
            canonical = ("fat_g", "g")
        overridden = nutrient.nutrient in {"CARBOHYDRATES", "FAT"} and (
            (nutrient.nutrient == "CARBOHYDRATES" and "totalCarbohydrate" in direct_metric_names)
            or (nutrient.nutrient == "FAT" and "totalFat" in direct_metric_names)
        )
        _field(
            db,
            user_id=user_id,
            source_observation_id=source_observation_id,
            provider_field_path=provider_path,
            value=value,
            raw_unit=nutrient.quantity.unit,
            metric_key=None if overridden or canonical is None else canonical[0],
            canonical_value=None if overridden or canonical is None else value,
            canonical_unit=None if overridden or canonical is None else canonical[1],
            role=ObservationRole.PROVIDER.value if overridden or canonical is None else ObservationRole.CANONICAL.value,
            metadata=metadata,
            coverage_state=coverage_state,
        )

    if log.serving is not None:
        serving = log.serving
        if serving.food_measurement_unit is not None:
            _field(
                db,
                user_id=user_id,
                source_observation_id=source_observation_id,
                provider_field_path="nutritionLog.serving.foodMeasurementUnit",
                value=None,
                raw_text=serving.food_measurement_unit,
                raw_unit=None,
                metadata=metadata,
                coverage_state=coverage_state,
            )
        if serving.amount is not None:
            amount = Decimal(str(serving.amount))
            _field(
                db,
                user_id=user_id,
                source_observation_id=source_observation_id,
                provider_field_path="nutritionLog.serving.amount",
                value=amount,
                raw_unit=_serving_unit(serving.food_measurement_unit),
                metadata=metadata,
                coverage_state=coverage_state,
            )


def _event_kind(point: NutritionLogDataPoint) -> str:
    log = point.nutrition_log
    if log.food is not None:
        return ConsumptionEventKind.PRODUCT.value
    if (
        log.energy is not None
        or log.energy_from_fat is not None
        or log.total_carbohydrate is not None
        or log.total_fat is not None
        or bool(log.nutrients)
        or log.serving is not None
    ):
        return ConsumptionEventKind.SIMPLE_PRODUCT.value
    return ConsumptionEventKind.UNKNOWN.value


def _logical_key(point: NutritionLogDataPoint, fingerprint: str) -> str:
    return point.name if point.name else f"anonymous:{fingerprint}"


def _persist_point(
    db: Session,
    *,
    user_id: UUID,
    source_instance_id: UUID,
    run: NutritionIngestionRun,
    point: NutritionLogDataPoint,
    fingerprint: str,
    coverage_state: str,
) -> None:
    log = point.nutrition_log
    interval = log.interval
    civil_start = _civil(interval.civil_start_time)
    local_date = civil_start.date() if civil_start is not None else None
    point_coverage = CoverageState.PARTIAL.value if civil_start is None else coverage_state
    metadata = _safe_metadata(
        {
            **_data_source_metadata(point.data_source),
            "external_name": point.name,
            "food": log.food,
            "food_display_name": log.food_display_name,
            "meal_type": log.meal_type,
            "content_hash": fingerprint,
        }
    )
    source_record_id = point.name or None
    source = get_or_create_source_observation(
        db,
        user_id=user_id,
        ingestion_run_id=run.id,
        provider_key=_PROVIDER,
        source_instance_id=source_instance_id,
        source_namespace=_SOURCE_NAMESPACE,
        source_record_id=source_record_id,
        source_revision=1,
        observation_fingerprint=fingerprint,
        observation_kind=ObservationKind.CONSUMPTION_EVENT.value,
        provider_civil_datetime=civil_start,
        provider_timezone=interval.start_utc_offset,
        canonical_start_at=interval.start_time,
        canonical_end_at=interval.end_time,
        local_date=local_date,
        timezone_source="provider",
        time_confidence="exact" if civil_start is not None else "physical_only",
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=point_coverage,
        resolution_state=ResolutionState.RESOLVED.value,
        lineage_state=LineageState.CONFIRMED.value,
        payload_hash=fingerprint,
        provider_metadata=metadata,
    )
    _field_observations(
        db,
        user_id=user_id,
        source_observation_id=source.id,
        point=point,
        coverage_state=point_coverage,
        metadata=metadata,
    )

    food_identity = None
    if log.food is not None:
        food_identity = get_or_create_external_identity(
            db,
            user_id=user_id,
            provider_key=_PROVIDER,
            namespace=_FOOD_NAMESPACE,
            identity_value=log.food,
            identity_kind="product",
            source_instance_id=source_instance_id,
            provider_metadata=_safe_metadata({"display_name": log.food_display_name}),
        )

    logical_event_key = _logical_key(point, fingerprint)
    latest_event = get_current_consumption_event(
        db,
        user_id=user_id,
        provider_key=_PROVIDER,
        source_instance_id=source_instance_id,
        logical_event_key=logical_event_key,
    )
    if latest_event is not None and (latest_event.provider_metadata or {}).get("content_hash") == fingerprint:
        event = latest_event
    else:
        event = get_or_create_consumption_event(
            db,
            user_id=user_id,
            source_observation_id=source.id,
            provider_key=_PROVIDER,
            source_instance_id=source_instance_id,
            event_kind=_event_kind(point),
            logical_event_key=logical_event_key,
            food_snapshot_id=None,
            provider_civil_datetime=civil_start,
            provider_timezone=interval.start_utc_offset,
            canonical_start_at=interval.start_time,
            canonical_end_at=interval.end_time,
            local_date=local_date,
            amount=Decimal(str(log.serving.amount)) if log.serving and log.serving.amount is not None else None,
            amount_unit=_serving_unit(log.serving.food_measurement_unit) if log.serving else None,
            presence_state=PresenceState.SUPPLIED.value,
            coverage_state=point_coverage,
            resolution_state=ResolutionState.UNRESOLVED.value if log.food else ResolutionState.RESOLVED.value,
            lineage_state=LineageState.CONFIRMED.value,
            content_hash=fingerprint,
            provider_metadata=metadata,
        )
    _provenance(db, user_id=user_id, source_observation_id=source.id, consumption_event_id=event.id)

    event_identity = get_or_create_external_identity(
        db,
        user_id=user_id,
        provider_key=_PROVIDER,
        namespace=_SOURCE_NAMESPACE,
        identity_value=point.name or f"anonymous:{fingerprint}",
        identity_kind="nutrition_log",
        source_instance_id=source_instance_id,
    )
    get_or_create_identity_link(
        db,
        user_id=user_id,
        external_identity_id=event_identity.id,
        link_role="event_identity",
        consumption_event_id=event.id,
    )

    if food_identity is not None:
        get_or_create_identity_link(
            db,
            user_id=user_id,
            external_identity_id=food_identity.id,
            link_role="product_event",
            consumption_event_id=event.id,
        )
    if log.serving is not None:
        _serving(
            db,
            user_id=user_id,
            source_observation_id=source.id,
            event_id=event.id,
            serving=log.serving,
            metadata=metadata,
        )


def _validate_point_names(data_points: tuple[NutritionLogDataPoint, ...] | list[NutritionLogDataPoint]) -> None:
    for point in data_points:
        if point.name and len(point.name.encode("utf-8")) > _MAX_PATH:
            raise ValueError("point.name exceeds the 255-byte UTF-8 limit")


def _validate_source_owner(db: Session, *, user_id: UUID, source_instance_id: UUID) -> None:
    owned = db.scalar(
        select(GoogleHealthConnection.id).where(
            GoogleHealthConnection.id == source_instance_id,
            GoogleHealthConnection.user_id == user_id,
        )
    )
    if owned is None:
        raise ValueError("source_instance_id must belong to the same user")


def ingest_google_health_nutrition_logs(
    db: Session,
    *,
    user_id: UUID,
    source_instance_id: UUID,
    requested_start: date,
    requested_end: date,
    data_points: tuple[NutritionLogDataPoint, ...],
    connector_variant: str = _DEFAULT_CONNECTOR,
    coverage_state: str = CoverageState.COMPLETE.value,
) -> NutritionIngestionRun:
    """Persist fetched Nutrition Log DTOs without networking or committing."""

    if requested_start > requested_end:
        raise ValueError("requested_start must not be after requested_end")
    if coverage_state not in {state.value for state in CoverageState}:
        raise ValueError("coverage_state is invalid")
    if not isinstance(data_points, (tuple, list)) or any(
        not isinstance(point, NutritionLogDataPoint) for point in data_points
    ):
        raise ValueError("data_points must contain NutritionLogDataPoint DTOs")
    _validate_point_names(data_points)

    # This direct query is intentionally before run creation and every other
    # write.  Generic provider validation does not know Google connection rows.
    _validate_source_owner(db, user_id=user_id, source_instance_id=source_instance_id)
    fingerprints = tuple(_fingerprint(point) for point in data_points)
    has_missing_civil_date = any(point.nutrition_log.interval.civil_start_time is None for point in data_points)
    effective_coverage = CoverageState.PARTIAL.value if has_missing_civil_date else coverage_state
    run = create_ingestion_run(
        db,
        user_id=user_id,
        provider_key=_PROVIDER,
        source_instance_id=source_instance_id,
        connector_variant=connector_variant,
        requested_start_date=requested_start,
        requested_end_date=requested_end,
        covered_start_date=requested_start,
        covered_end_date=requested_end,
        status="partial" if effective_coverage == CoverageState.PARTIAL.value else "completed",
        coverage_state=effective_coverage,
        provider_metadata=_safe_metadata(
            {
                "connector_variant": connector_variant,
                "covered_item_count": len(data_points),
            }
        ),
    )
    for point, fingerprint in zip(data_points, fingerprints, strict=True):
        _persist_point(
            db,
            user_id=user_id,
            source_instance_id=source_instance_id,
            run=run,
            point=point,
            fingerprint=fingerprint,
            coverage_state=effective_coverage,
        )
    db.flush()
    return run


__all__ = ["ingest_google_health_nutrition_logs"]
