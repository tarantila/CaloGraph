"""Persistence boundary for already-fetched YAZIO v22 food diaries."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any, cast
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
    ServingScope,
)
from app.nutrition.models import (
    NutritionFieldObservation,
    NutritionFoodSnapshot,
    NutritionIngestionRun,
    NutritionProvenance,
    NutritionServingObservation,
    NutritionSourceObservation,
)
from app.nutrition.repositories import (
    create_ingestion_run,
    get_or_create_consumption_event,
    get_or_create_external_identity,
    get_or_create_food_profile,
    get_or_create_food_snapshot,
    get_or_create_identity_link,
    get_or_create_source_observation,
)
from app.services.yazio_provider import (
    YazioConsumedProduct,
    YazioConsumedSimpleProduct,
    YazioDailyNutrientSummary,
    YazioFoodDiary,
    YazioNutrientValues,
    YazioProductProfile,
    YazioServing,
)

_PROVIDER = "yazio"
_CONNECTOR = "sdk-v22"
_MAX_METADATA_ITEMS = 32
_MAX_METADATA_KEY = 128
_MAX_METADATA_TEXT = 512
_SENSITIVE_TERMS = (
    "access_key",
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "auth",
    "bearer",
    "cookie",
    "credential",
    "header",
    "key",
    "password",
    "raw",
    "refresh",
    "response",
    "secret",
    "session",
    "token",
)
_NUTRIENTS = ("energy", "protein", "carb", "fat", "fiber", "sugar", "saturated_fat", "salt")
_METRIC_KEYS = {
    "energy": "dietary_energy_kcal",
    "protein": "protein_g",
    "carb": "carbohydrates_g",
    "fat": "fat_g",
    "fiber": "fiber_g",
    "sugar": "sugar_g",
    "saturated_fat": "saturated_fat_g",
}
_NUTRIENT_UNITS = {"energy": "kcal", "protein": "g", "carb": "g", "fat": "g", "fiber": "g", "sugar": "g", "saturated_fat": "g", "salt": "g"}
_UNITS = {**_NUTRIENT_UNITS, "energy_goal_kcal": "kcal"}

def _json_value(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {field.name: _json_value(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, Decimal):
        return {"decimal": str(value)}
    if isinstance(value, datetime):
        return {"datetime": value.isoformat()}
    if isinstance(value, date):
        return {"date": value.isoformat()}
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(_json_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()

def _product_event_link_role(consumed_item_id: str) -> str:
    return f"product_event:{hashlib.sha256(consumed_item_id.encode()).hexdigest()[:48]}"


def _safe_metadata(metadata: Mapping[str, Any] | None) -> dict[str, str | int | float | bool | None]:
    result: dict[str, str | int | float | bool | None] = {}
    for key, value in (metadata or {}).items():
        if not isinstance(key, str) or not key or len(key.encode()) > _MAX_METADATA_KEY:
            continue
        lowered = key.lower()
        if any(term in lowered for term in _SENSITIVE_TERMS):
            continue
        if isinstance(value, bool) or value is None or isinstance(value, int):
            result[key] = value
        elif isinstance(value, float):
            if value == value and abs(value) != float("inf"):
                result[key] = value
        elif isinstance(value, str) and len(value.encode()) <= _MAX_METADATA_TEXT:
            result[key] = value
        if len(result) >= _MAX_METADATA_ITEMS:
            break
    return result


def _civil(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=None) if value is not None else None


def _presence(value: Decimal | None) -> str:
    if value is None:
        return PresenceState.MISSING.value
    return PresenceState.EXPLICIT_ZERO.value if value == 0 else PresenceState.SUPPLIED.value


def _canonical_value(nutrient: str, value: Decimal | None, *, supported: bool = True) -> Decimal | None:
    return value if supported and (nutrient in _UNITS or nutrient in _METRIC_KEYS.values()) else None


def _metadata_with(metadata: Mapping[str, Any] | None, **values: Any) -> dict[str, Any]:
    merged = dict(metadata or {})
    merged.update(values)
    return _safe_metadata(merged)
def _nutrient_value(nutrients: YazioNutrientValues, key: str) -> Decimal | None:
    return cast(Decimal | None, getattr(nutrients, key))


def _metric_key(provider_key: str) -> str | None:
    return _METRIC_KEYS.get(provider_key)


def _nutrient_unit(provider_key: str) -> str | None:
    return _NUTRIENT_UNITS.get(provider_key)


def _provider_key(metric_key: str) -> str | None:
    return next((key for key, value in _METRIC_KEYS.items() if value == metric_key), None)

def _nutrient_data(nutrients: YazioNutrientValues) -> dict[str, Any]:
    return {
        **{key: _nutrient_value(nutrients, key) for key in _NUTRIENTS},
        "additional": dict(sorted(nutrients.additional.items())),
    }


def _serving_data(serving: YazioServing) -> dict[str, Any]:
    return {
        "label": serving.label,
        "amount": serving.amount,
        "unit": serving.unit,
        "metadata": _safe_metadata(serving.metadata),
    }


def _profile_data(profile: YazioProductProfile) -> dict[str, Any]:
    return {
        "product_id": profile.product_id,
        "name": profile.name,
        "producer": profile.producer,
        "category": profile.category,
        "base_unit": profile.base_unit,
        "nutrients": _nutrient_data(profile.nutrients),
        "servings": [_serving_data(item) for item in profile.servings],
        "eans": profile.eans,
        "language": profile.language,
        "countries": profile.countries,
        "updated_at": profile.updated_at,
        "flags": [profile.is_verified, profile.is_private, profile.is_deleted],
        "metadata": _safe_metadata(profile.metadata),
    }


def _product_data(item: YazioConsumedProduct, content_context: str | None = None) -> dict[str, Any]:
    return {
        "consumed_item_id": item.consumed_item_id,
        "product_id": item.product_id,
        "amount": item.amount,
        "provider_civil_datetime": item.provider_civil_datetime,
        "local_date": item.local_date,
        "daytime": item.daytime,
        "serving": item.serving,
        "serving_quantity": item.serving_quantity,
        "provider_timezone": item.provider_timezone,
        "metadata": _safe_metadata(item.metadata),
        "content_context": content_context,
    }


def _simple_data(item: YazioConsumedSimpleProduct) -> dict[str, Any]:
    return {
        "consumed_item_id": item.consumed_item_id,
        "amount": item.amount,
        "provider_civil_datetime": item.provider_civil_datetime,
        "local_date": item.local_date,
        "daytime": item.daytime,
        "nutrients": _nutrient_data(item.nutrients),
        "serving": item.serving,
        "serving_quantity": item.serving_quantity,
        "provider_timezone": item.provider_timezone,
        "name": item.name,
        "metadata": _safe_metadata(item.metadata),
    }

def _field(
    db: Session,
    *,
    user_id: UUID,
    source_observation_id: UUID,
    provider_field_path: str,
    value: Decimal | None,
    metric_key: str | None,
    canonical_supported: bool = True,
    canonical_unit: str | None,
    provider_raw_unit: str | None,
    role: str,
    derived_from_field_observation_id: UUID | None = None,
    raw_text: str | None = None,
    presence_state: str | None = None,
    coverage_state: str = CoverageState.COMPLETE.value,
    resolution_state: str = ResolutionState.RESOLVED.value,
    lineage_state: str = LineageState.CONFIRMED.value,
    provider_metadata: Mapping[str, Any] | None = None,
) -> NutritionFieldObservation:
    existing = db.scalar(
        select(NutritionFieldObservation).where(
            NutritionFieldObservation.user_id == user_id,
            NutritionFieldObservation.source_observation_id == source_observation_id,
            NutritionFieldObservation.provider_field_path == provider_field_path,
            NutritionFieldObservation.observation_role == role,
        )
    )
    if existing is not None:
        existing.provider_raw_value_decimal = value
        existing.provider_raw_value_text = raw_text
        existing.provider_raw_unit = provider_raw_unit
        existing.metric_key = metric_key
        existing.canonical_value = _canonical_value(metric_key or "", value, supported=canonical_supported)
        existing.canonical_unit = canonical_unit
        existing.derived_from_field_observation_id = derived_from_field_observation_id
        existing.presence_state = presence_state or _presence(value)
        existing.coverage_state = coverage_state
        existing.resolution_state = resolution_state
        existing.lineage_state = lineage_state
        existing.provider_metadata = _safe_metadata(provider_metadata)
        db.flush()
        return existing
    field = NutritionFieldObservation(
        user_id=user_id,
        source_observation_id=source_observation_id,
        provider_field_path=provider_field_path,
        provider_raw_value_decimal=value,
        provider_raw_value_text=raw_text,
        provider_raw_unit=provider_raw_unit,
        metric_key=metric_key,
        canonical_value=_canonical_value(metric_key or "", value, supported=canonical_supported),
        canonical_unit=canonical_unit,
        observation_role=role,
        derived_from_field_observation_id=derived_from_field_observation_id,
        presence_state=presence_state or _presence(value),
        coverage_state=coverage_state,
        resolution_state=resolution_state,
        lineage_state=lineage_state,
        provider_metadata=_safe_metadata(provider_metadata),
    )
    db.add(field)
    db.flush()
    _provenance(db, user_id=user_id, source_observation_id=source_observation_id, field_observation_id=field.id, lineage_state=lineage_state)
    return field
def _provenance(
    db: Session,
    *,
    user_id: UUID,
    source_observation_id: UUID,
    consumption_event_id: UUID | None = None,
    food_snapshot_id: UUID | None = None,
    serving_observation_id: UUID | None = None,
    field_observation_id: UUID | None = None,
    role: str = "provider",
    lineage_state: str = LineageState.CONFIRMED.value,
) -> None:
    targets = (consumption_event_id, food_snapshot_id, serving_observation_id, field_observation_id)
    if sum(target is not None for target in targets) != 1:
        raise ValueError("provenance must have exactly one target")
    target_columns = (
        NutritionProvenance.consumption_event_id,
        NutritionProvenance.food_snapshot_id,
        NutritionProvenance.serving_observation_id,
        NutritionProvenance.field_observation_id,
    )
    filters = [
        NutritionProvenance.user_id == user_id,
        NutritionProvenance.source_observation_id == source_observation_id,
        NutritionProvenance.role == role,
    ]
    filters.extend(column == target for column, target in zip(target_columns, targets, strict=True))
    if db.scalar(select(NutritionProvenance).where(*filters)) is not None:
        return
    db.add(
        NutritionProvenance(
            user_id=user_id,
            source_observation_id=source_observation_id,
            consumption_event_id=consumption_event_id,
            food_snapshot_id=food_snapshot_id,
            serving_observation_id=serving_observation_id,
            field_observation_id=field_observation_id,
            role=role,
            lineage_state=lineage_state,
        )
    )
    db.flush()


def _serving(
    db: Session,
    *,
    user_id: UUID,
    source_observation_id: UUID,
    scope: str,
    label: str | None,
    quantity: Decimal | None,
    amount: Decimal | None,
    unit: str | None,
    food_snapshot_id: UUID | None = None,
    consumption_event_id: UUID | None = None,
    profile_serving_id: UUID | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> NutritionServingObservation | None:
    if scope == ServingScope.PROFILE.value and (food_snapshot_id is None or amount is None or unit is None):
        return None
    if scope == ServingScope.EVENT.value and consumption_event_id is None:
        return None
    existing = db.scalar(
        select(NutritionServingObservation).where(
            NutritionServingObservation.user_id == user_id,
            NutritionServingObservation.serving_scope == scope,
            NutritionServingObservation.food_snapshot_id == food_snapshot_id,
            NutritionServingObservation.consumption_event_id == consumption_event_id,
            NutritionServingObservation.label == label,
            NutritionServingObservation.quantity == quantity,
            NutritionServingObservation.amount == amount,
            NutritionServingObservation.unit == unit,
        )
    )
    if existing is not None:
        return existing
    item = NutritionServingObservation(
        user_id=user_id,
        consumption_event_id=consumption_event_id,
        food_snapshot_id=food_snapshot_id,
        serving_scope=scope,
        label=label,
        quantity=quantity,
        amount=amount,
        unit=unit,
        profile_serving_id=profile_serving_id,
        profile_serving_scope=ServingScope.PROFILE.value if profile_serving_id else None,
        provider_field_path="serving",
        provider_metadata=_safe_metadata(metadata),
    )
    db.add(item)
    db.flush()
    _provenance(db, user_id=user_id, source_observation_id=source_observation_id, serving_observation_id=item.id)
    return item


def _profile_fields(
    db: Session,
    *,
    user_id: UUID,
    source_observation_id: UUID,
    profile: YazioProductProfile,
) -> None:
    for nutrient in _NUTRIENTS:
        value = _nutrient_value(profile.nutrients, nutrient)
        metric_key = _metric_key(nutrient)
        _field(
            db,
            user_id=user_id,
            source_observation_id=source_observation_id,
            provider_field_path=f"nutrients.{nutrient}",
            value=value,
            metric_key=metric_key,
            canonical_unit=_nutrient_unit(nutrient) if metric_key is not None else None,
            provider_raw_unit=_nutrient_unit(nutrient),
            role=ObservationRole.PROVIDER.value,
            presence_state=_presence(value),
            provider_metadata=profile.metadata,
        )
    for nutrient, value in profile.nutrients.additional.items():
        _field(
            db,
            user_id=user_id,
            source_observation_id=source_observation_id,
            provider_field_path=f"nutrients.{nutrient}",
            value=value,
            metric_key=None,
            canonical_unit=None,
            provider_raw_unit=None,
            role=ObservationRole.PROVIDER.value,
            provider_metadata=profile.metadata,
        )

def _profile_observation(
    db: Session,
    *,
    user_id: UUID,
    run_id: UUID,
    source_instance_id: UUID,
    profile: YazioProductProfile,
) -> NutritionFoodSnapshot:
    metadata = _safe_metadata(profile.metadata)
    fingerprint = _fingerprint({"product_id": profile.product_id, "profile": _profile_data(profile)})
    observation = get_or_create_source_observation(
        db,
        user_id=user_id,
        ingestion_run_id=run_id,
        provider_key=_PROVIDER,
        source_instance_id=source_instance_id,
        source_namespace="yazio.product",
        source_record_id=profile.product_id,
        source_revision=1,
        observation_fingerprint=fingerprint,
        observation_kind=ObservationKind.PRODUCT_PROFILE.value,
        local_date=None,
        provider_civil_datetime=_civil(profile.updated_at),
        provider_timezone=None,
        timezone_source="provider" if profile.updated_at else None,
        time_confidence="exact" if profile.updated_at else None,
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
        lineage_state=LineageState.CONFIRMED.value,
        payload_hash=fingerprint,
        provider_metadata=metadata,
    )
    identity = get_or_create_external_identity(
        db,
        user_id=user_id,
        provider_key=_PROVIDER,
        namespace="yazio.product",
        identity_value=profile.product_id,
        identity_kind="product",
        source_instance_id=source_instance_id,
        provider_metadata=metadata,
    )
    food_profile = get_or_create_food_profile(
        db,
        user_id=user_id,
        provider_key=_PROVIDER,
        source_instance_id=source_instance_id,
        external_identity_id=identity.id,
    )
    snapshot = get_or_create_food_snapshot(
        db,
        user_id=user_id,
        food_profile_id=food_profile.id,
        advance_current=True,
        order_by_provider_updated_at=True,
        source_observation_id=observation.id,
        content_hash=fingerprint,
        provider_revision=profile.updated_at.isoformat() if profile.updated_at else None,
        name=profile.name,
        producer=profile.producer,
        category=profile.category,
        base_unit=profile.base_unit,
        language=profile.language,
        is_verified=profile.is_verified,
        is_private=profile.is_private,
        is_deleted=profile.is_deleted,
        provider_updated_at=_civil(profile.updated_at),
        provider_metadata=metadata,
    )
    _provenance(db, user_id=user_id, source_observation_id=observation.id, food_snapshot_id=snapshot.id)
    get_or_create_identity_link(db, user_id=user_id, external_identity_id=identity.id, link_role="profile_identity", food_profile_id=food_profile.id)
    _profile_fields(db, user_id=user_id, source_observation_id=observation.id, profile=profile)
    profile_servings: list[NutritionServingObservation] = []
    for serving in profile.servings:
        item = _serving(
            db,
            user_id=user_id,
            source_observation_id=observation.id,
            scope=ServingScope.PROFILE.value,
            label=serving.label,
            quantity=None,
            amount=serving.amount,
            unit=serving.unit or profile.base_unit,
            food_snapshot_id=snapshot.id,
            metadata=serving.metadata,
        )
        if item is not None:
            profile_servings.append(item)
    for ean in profile.eans:
        ean_identity = get_or_create_external_identity(
            db,
            user_id=user_id,
            provider_key=_PROVIDER,
            namespace="yazio.ean",
            identity_value=ean,
            identity_kind="ean",
            source_instance_id=source_instance_id,
            provider_metadata=metadata,
        )
        get_or_create_identity_link(db, user_id=user_id, external_identity_id=ean_identity.id, link_role="profile_identity", food_profile_id=food_profile.id)
    return snapshot
def _event_observation(
    db: Session,
    *,
    user_id: UUID,
    run_id: UUID,
    source_instance_id: UUID,
    item: YazioConsumedProduct | YazioConsumedSimpleProduct,
    namespace: str,
    kind: str,
    content_context: str | None = None,
) -> NutritionSourceObservation:
    metadata = _safe_metadata(item.metadata)
    data = _product_data(item, content_context) if isinstance(item, YazioConsumedProduct) else _simple_data(item)
    fingerprint = _fingerprint(data)
    return get_or_create_source_observation(
        db,
        user_id=user_id,
        ingestion_run_id=run_id,
        provider_key=_PROVIDER,
        source_instance_id=source_instance_id,
        source_namespace=namespace,
        source_record_id=item.consumed_item_id,
        source_revision=1,
        observation_fingerprint=fingerprint,
        observation_kind=kind,
        local_date=item.local_date,
        provider_civil_datetime=_civil(item.provider_civil_datetime),
        provider_timezone=item.provider_timezone,
        timezone_source="provider" if item.provider_timezone else None,
        time_confidence="exact" if item.provider_civil_datetime else "date_only",
        presence_state=_presence(item.amount),
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
        lineage_state=LineageState.CONFIRMED.value,
        payload_hash=fingerprint,
        provider_metadata=metadata,
    )


def _product_event(
    db: Session,
    *,
    user_id: UUID,
    run_id: UUID,
    source_instance_id: UUID,
    item: YazioConsumedProduct,
    snapshots: dict[str, NutritionFoodSnapshot],
) -> None:
    snapshot = snapshots.get(item.product_id)
    resolved = snapshot is not None and snapshot.base_unit is not None
    observation = _event_observation(
        db,
        user_id=user_id,
        run_id=run_id,
        source_instance_id=source_instance_id,
        item=item,
        namespace="yazio.consumed_item",
        kind=ObservationKind.CONSUMPTION_EVENT.value,
        content_context=snapshot.content_hash if snapshot else None,
    )
    identity = get_or_create_external_identity(
        db,
        user_id=user_id,
        provider_key=_PROVIDER,
        namespace="yazio.consumed_item",
        identity_value=item.consumed_item_id,
        identity_kind="consumed_item",
        source_instance_id=source_instance_id,
        provider_metadata=_safe_metadata(item.metadata),
    )
    metadata = _metadata_with(item.metadata, product_id=item.product_id)
    product_identity = get_or_create_external_identity(
        db,
        user_id=user_id,
        provider_key=_PROVIDER,
        namespace="yazio.product",
        identity_value=item.product_id,
        identity_kind="product",
        source_instance_id=source_instance_id,
        provider_metadata={"product_id": item.product_id},
    )
    event = get_or_create_consumption_event(
        db,
        user_id=user_id,
        source_observation_id=observation.id,
        provider_key=_PROVIDER,
        source_instance_id=source_instance_id,
        event_kind=ConsumptionEventKind.PRODUCT.value,
        logical_event_key=item.consumed_item_id,
        food_snapshot_id=snapshot.id if snapshot else None,
        provider_civil_datetime=_civil(item.provider_civil_datetime),
        provider_timezone=item.provider_timezone,
        local_date=item.local_date,
        daytime=item.daytime,
        amount=item.amount,
        amount_unit=snapshot.base_unit if snapshot else None,
        presence_state=_presence(item.amount),
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value if resolved else ResolutionState.UNRESOLVED.value,
        lineage_state=LineageState.CONFIRMED.value,
        content_hash=_fingerprint({"item": _product_data(item), "snapshot": snapshot.content_hash if snapshot else None}),
        provider_metadata=metadata,
    )
    _provenance(db, user_id=user_id, source_observation_id=observation.id, consumption_event_id=event.id)
    get_or_create_identity_link(
        db,
        user_id=user_id,
        external_identity_id=identity.id,
        link_role="event_identity",
        consumption_event_id=event.id,
    )
    get_or_create_identity_link(
        db,
        user_id=user_id,
        external_identity_id=product_identity.id,
        link_role=_product_event_link_role(item.consumed_item_id),
        consumption_event_id=event.id,
    )
    _field(
        db,
        user_id=user_id,
        source_observation_id=observation.id,
        provider_field_path="amount",
        value=item.amount,
        metric_key=None,
        canonical_unit=snapshot.base_unit if snapshot else None,
        provider_raw_unit=snapshot.base_unit if snapshot else None,
        role=ObservationRole.PROVIDER.value,
        provider_metadata=item.metadata,
    )
    if resolved:
        assert snapshot is not None
        profile_fields = db.scalars(
            select(NutritionFieldObservation).where(
                NutritionFieldObservation.source_observation_id == snapshot.source_observation_id,
                NutritionFieldObservation.observation_role == ObservationRole.PROVIDER.value,
            )
        ).all()
        for source_field in profile_fields:
            provider_nutrient = _provider_key(source_field.metric_key or "")
            if provider_nutrient is None:
                continue
            profile_value = source_field.canonical_value
            derived = None if profile_value is None or event.amount is None else profile_value * event.amount
            _field(
                db,
                user_id=user_id,
                source_observation_id=observation.id,
                provider_field_path=f"nutrients.{provider_nutrient}",
                value=derived,
                metric_key=source_field.metric_key,
                canonical_unit=source_field.canonical_unit,
                provider_raw_unit=source_field.canonical_unit,
                role=ObservationRole.DERIVED.value,
                derived_from_field_observation_id=source_field.id,
                presence_state=_presence(derived),
                provider_metadata={"source": "profile"},
            )
    _serving(
        db,
        user_id=user_id,
        source_observation_id=observation.id,
        scope=ServingScope.EVENT.value,
        label=item.serving,
        quantity=item.serving_quantity,
        amount=None,
        unit=None,
        consumption_event_id=event.id,
        metadata=item.metadata,
    )


def _simple_event(
    db: Session,
    *,
    user_id: UUID,
    run_id: UUID,
    source_instance_id: UUID,
    item: YazioConsumedSimpleProduct,
) -> None:
    observation = _event_observation(db, user_id=user_id, run_id=run_id, source_instance_id=source_instance_id, item=item, namespace="yazio.simple_product", kind=ObservationKind.SIMPLE_PRODUCT.value)
    identity = get_or_create_external_identity(db, user_id=user_id, provider_key=_PROVIDER, namespace="yazio.simple_product", identity_value=item.consumed_item_id, identity_kind="simple_product", source_instance_id=source_instance_id, provider_metadata=_safe_metadata(item.metadata))
    metadata = _metadata_with(item.metadata, name=item.name, is_ai_generated=item.metadata.get("is_ai_generated"))
    event = get_or_create_consumption_event(db, user_id=user_id, source_observation_id=observation.id, provider_key=_PROVIDER, source_instance_id=source_instance_id, event_kind=ConsumptionEventKind.SIMPLE_PRODUCT.value, logical_event_key=item.consumed_item_id, provider_civil_datetime=_civil(item.provider_civil_datetime), provider_timezone=item.provider_timezone, local_date=item.local_date, daytime=item.daytime, amount=item.amount, amount_unit=None, presence_state=_presence(item.amount), coverage_state=CoverageState.COMPLETE.value, resolution_state=ResolutionState.RESOLVED.value, lineage_state=LineageState.CONFIRMED.value, content_hash=_fingerprint(_simple_data(item)), provider_metadata=metadata)
    _provenance(db, user_id=user_id, source_observation_id=observation.id, consumption_event_id=event.id)
    get_or_create_identity_link(db, user_id=user_id, external_identity_id=identity.id, link_role="event_identity", consumption_event_id=event.id)
    _field(db, user_id=user_id, source_observation_id=observation.id, provider_field_path="amount", value=item.amount, metric_key=None, canonical_unit=None, provider_raw_unit=None, role=ObservationRole.PROVIDER.value, provider_metadata=item.metadata)
    for nutrient in _NUTRIENTS:
        value = _nutrient_value(item.nutrients, nutrient)
        metric_key = _metric_key(nutrient)
        _field(db, user_id=user_id, source_observation_id=observation.id, provider_field_path=f"nutrients.{nutrient}", value=value, metric_key=metric_key, canonical_unit=_nutrient_unit(nutrient) if metric_key is not None else None, provider_raw_unit=_nutrient_unit(nutrient), role=ObservationRole.PROVIDER.value, presence_state=_presence(value), provider_metadata=item.metadata)
    for nutrient, value in item.nutrients.additional.items():
        _field(db, user_id=user_id, source_observation_id=observation.id, provider_field_path=f"nutrients.{nutrient}", value=value, metric_key=None, canonical_unit=None, provider_raw_unit=None, role=ObservationRole.PROVIDER.value, provider_metadata=item.metadata)
    _serving(db, user_id=user_id, source_observation_id=observation.id, scope=ServingScope.EVENT.value, label=item.serving, quantity=item.serving_quantity, amount=None, unit=None, consumption_event_id=event.id, metadata=item.metadata)


def _summary(
    db: Session,
    *,
    user_id: UUID,
    run_id: UUID,
    source_instance_id: UUID,
    item: YazioDailyNutrientSummary,
) -> None:
    metadata = _safe_metadata(item.metadata)
    summary_missing = metadata.get("provider_summary_missing") is True
    fingerprint = _fingerprint(
        {
            "local_date": item.local_date,
            "nutrients": _nutrient_data(item.nutrients),
            "energy_goal": item.energy_goal,
            "metadata": metadata,
        }
    )
    observation = get_or_create_source_observation(
        db,
        user_id=user_id,
        ingestion_run_id=run_id,
        provider_key=_PROVIDER,
        source_instance_id=source_instance_id,
        source_namespace="yazio.daily_summary",
        source_record_id=item.local_date.isoformat(),
        source_revision=1,
        observation_fingerprint=fingerprint,
        observation_kind=ObservationKind.DAILY_SUMMARY.value,
        local_date=item.local_date,
        presence_state=(
            PresenceState.MISSING.value if summary_missing else PresenceState.SUPPLIED.value
        ),
        coverage_state=(
            CoverageState.PARTIAL.value if summary_missing else CoverageState.COMPLETE.value
        ),
        resolution_state=(
            ResolutionState.UNRESOLVED.value if summary_missing else ResolutionState.RESOLVED.value
        ),
        lineage_state=LineageState.CONFIRMED.value,
        payload_hash=fingerprint,
        provider_metadata=metadata,
    )
    identity = get_or_create_external_identity(
        db,
        user_id=user_id,
        provider_key=_PROVIDER,
        namespace="yazio.daily_summary",
        identity_value=item.local_date.isoformat(),
        identity_kind="daily_summary",
        source_instance_id=source_instance_id,
        provider_metadata=metadata,
    )
    get_or_create_identity_link(
        db,
        user_id=user_id,
        external_identity_id=identity.id,
        link_role="summary_identity",
        source_observation_id=observation.id,
    )
    for nutrient in _NUTRIENTS:
        value = _nutrient_value(item.nutrients, nutrient)
        metric_key = _metric_key(nutrient)
        _field(
            db,
            user_id=user_id,
            source_observation_id=observation.id,
            provider_field_path=f"nutrients.{nutrient}",
            value=value,
            metric_key=metric_key,
            canonical_unit=_nutrient_unit(nutrient) if metric_key is not None else None,
            provider_raw_unit=_nutrient_unit(nutrient),
            role=ObservationRole.PROVIDER.value,
            presence_state=_presence(value),
            provider_metadata=item.metadata,
        )
    _field(
        db,
        user_id=user_id,
        source_observation_id=observation.id,
        provider_field_path="energy_goal",
        value=item.energy_goal,
        metric_key=None,
        canonical_unit=None,
        provider_raw_unit="kcal" if item.energy_goal is not None else None,
        role=ObservationRole.PROVIDER.value,
        presence_state=_presence(item.energy_goal),
        provider_metadata=item.metadata,
    )
    for nutrient, value in item.nutrients.additional.items():
        _field(
            db,
            user_id=user_id,
            source_observation_id=observation.id,
            provider_field_path=f"nutrients.{nutrient}",
            value=value,
            metric_key=None,
            canonical_unit=None,
            provider_raw_unit=None,
            role=ObservationRole.PROVIDER.value,
            provider_metadata=item.metadata,
        )


def ingest_yazio_food_diary(
    db: Session,
    *,
    user_id: UUID,
    source_instance_id: UUID,
    requested_start: date,
    requested_end: date,
    diary: YazioFoodDiary,
) -> NutritionIngestionRun:
    if requested_start > requested_end:
        raise ValueError("requested_start must not be after requested_end")
    if diary.requested_start_day != requested_start or diary.requested_end_day != requested_end:
        raise ValueError("diary range must match requested range")
    coverage = CoverageState.COMPLETE.value
    run = create_ingestion_run(
        db,
        user_id=user_id,
        provider_key=_PROVIDER,
        source_instance_id=source_instance_id,
        connector_variant=_CONNECTOR,
        requested_start_date=requested_start,
        requested_end_date=requested_end,
        covered_start_date=requested_start,
        covered_end_date=requested_end,
        status="completed",
        coverage_state=coverage,
        provider_metadata={
            "connector_variant": _CONNECTOR,
            "covered_item_count": len(diary.consumed_products) + len(diary.consumed_simple_products),
        },
    )
    snapshots = {profile.product_id: _profile_observation(db, user_id=user_id, run_id=run.id, source_instance_id=source_instance_id, profile=profile) for profile in diary.product_profiles}
    for consumed_product in diary.consumed_products:
        _product_event(
            db,
            user_id=user_id,
            run_id=run.id,
            source_instance_id=source_instance_id,
            item=consumed_product,
            snapshots=snapshots,
        )
    for simple_product in diary.consumed_simple_products:
        _simple_event(
            db,
            user_id=user_id,
            run_id=run.id,
            source_instance_id=source_instance_id,
            item=simple_product,
        )
    for summary in diary.daily_summaries:
        _summary(
            db,
            user_id=user_id,
            run_id=run.id,
            source_instance_id=source_instance_id,
            item=summary,
        )
    db.flush()
    return run
