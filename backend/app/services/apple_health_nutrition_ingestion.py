"""Persistence boundary for Apple Health food correlations."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.importers.apple_xml import AppleFoodCorrelation, AppleFoodNutrient
from app.importers.common import METRIC_MAP, decimal_value, local_date_for, normalize_value
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
    NutritionProvenance,
)
from app.nutrition.repositories import (
    create_ingestion_run,
    get_or_create_consumption_event,
    get_or_create_external_identity,
    get_or_create_identity_link,
    get_or_create_source_observation,
)

_PROVIDER = "apple_health"
_CONNECTOR = "healthkit-export-v1"
_SOURCE_NAMESPACE = "apple_health.food_correlation"
_EXTERNAL_UUID_NAMESPACE = "apple_health.external_uuid"
_SOURCE_INSTANCE_NAMESPACE = UUID("6f5d56bf-258e-4ed5-8ecb-55d5b40c4f4e")
_MAX_METADATA_TEXT = 512
_MAX_PATH = 255
_SENSITIVE_TERMS = (
    "access_token",
    "api_key",
    "authorization",
    "credential",
    "password",
    "secret",
    "session",
    "token",
)


def apple_health_source_instance_id(user_id: UUID) -> UUID:
    return uuid5(_SOURCE_INSTANCE_NAMESPACE, str(user_id))


def _safe_text(value: str | None) -> str | None:
    if value is None or "\x00" in value or len(value.encode("utf-8")) > _MAX_METADATA_TEXT:
        return None
    if any(term in value.lower() for term in _SENSITIVE_TERMS):
        return None
    return value


def _safe_metadata(values: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in values.items():
        safe_value = _safe_text(value if isinstance(value, str) else None)
        if safe_value is not None:
            result[key] = safe_value
    return result


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


def _fingerprint(correlation: AppleFoodCorrelation) -> str:
    nutrients = sorted(
        correlation.nutrients,
        key=lambda item: (
            item.raw_type,
            item.raw_value or "",
            item.unit or "",
            item.start_at.isoformat() if item.start_at else "",
            item.end_at.isoformat() if item.end_at else "",
        ),
    )
    payload = {
        "source_name": correlation.source_name,
        "source_version": correlation.source_version,
        "device": correlation.device,
        "start_at": correlation.start_at,
        "end_at": correlation.end_at,
        "creation_at": correlation.creation_at,
        "metadata": correlation.metadata,
        "nutrients": [
            {
                "type": nutrient.raw_type,
                "value": nutrient.raw_value,
                "unit": nutrient.unit,
                "start_at": nutrient.start_at,
                "end_at": nutrient.end_at,
                "metadata": nutrient.metadata,
                "value_error": nutrient.value_error,
            }
            for nutrient in nutrients
        ],
    }
    encoded = json.dumps(_json_value(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _presence(value: Decimal | None) -> str:
    if value is None:
        return PresenceState.MISSING.value
    return PresenceState.EXPLICIT_ZERO.value if value == 0 else PresenceState.SUPPLIED.value


def _event_metadata(correlation: AppleFoodCorrelation, fingerprint: str) -> dict[str, str]:
    return _safe_metadata(
        {
            "source_name": correlation.source_name,
            "source_version": correlation.source_version,
            "device": correlation.device,
            "food_name": correlation.food_name,
            "apple_external_uuid": correlation.external_uuid,
            "creation_date": correlation.creation_at.isoformat()
            if correlation.creation_at is not None
            else None,
            "content_hash": fingerprint,
        }
    )


def _database_safe_value(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return decimal_value(value)
    except (TypeError, ValueError):
        return None


def _canonical_value(
    nutrient: AppleFoodNutrient,
    provider_value: Decimal | None,
) -> tuple[str | None, str | None, Decimal | None]:
    mapped = METRIC_MAP.get(nutrient.raw_type)
    if mapped is None or provider_value is None or nutrient.unit is None:
        return None, None, None
    metric_key, canonical_unit = mapped
    try:
        value = normalize_value(provider_value, nutrient.unit, canonical_unit)
        value = decimal_value(value)
    except (TypeError, ValueError):
        return None, None, None
    return metric_key, canonical_unit, value


def _field(
    db: Session,
    *,
    user_id: UUID,
    source_observation_id: UUID,
    provider_field_path: str,
    nutrient: AppleFoodNutrient,
    provider_raw_value_decimal: Decimal | None,
    metric_key: str | None,
    canonical_unit: str | None,
    canonical_value: Decimal | None,
    role: str,
    resolution_state: str,
    lineage_state: str,
    provider_metadata: dict[str, str],
) -> NutritionFieldObservation:
    existing = db.scalar(
        select(NutritionFieldObservation).where(
            NutritionFieldObservation.user_id == user_id,
            NutritionFieldObservation.source_observation_id == source_observation_id,
            NutritionFieldObservation.provider_field_path == provider_field_path,
            NutritionFieldObservation.observation_role == role,
        )
    )
    values = {
        "provider_raw_value_decimal": provider_raw_value_decimal,
        "provider_raw_value_text": nutrient.raw_value,
        "provider_raw_unit": nutrient.unit,
        "metric_key": metric_key,
        "canonical_value": canonical_value,
        "canonical_unit": canonical_unit,
        "presence_state": _presence(provider_raw_value_decimal),
        "coverage_state": CoverageState.PARTIAL.value,
        "resolution_state": resolution_state,
        "lineage_state": lineage_state,
        "provider_metadata": provider_metadata,
    }
    if existing is not None:
        for key, value in values.items():
            setattr(existing, key, value)
        db.flush()
        return existing
    field = NutritionFieldObservation(
        user_id=user_id,
        source_observation_id=source_observation_id,
        provider_field_path=provider_field_path,
        observation_role=role,
        derived_from_field_observation_id=None,
        **values,
    )
    db.add(field)
    db.flush()
    db.add(
        NutritionProvenance(
            user_id=user_id,
            source_observation_id=source_observation_id,
            field_observation_id=field.id,
            role=ObservationRole.PROVIDER.value,
            lineage_state=lineage_state,
            provider_metadata=provider_metadata,
        )
    )
    db.flush()
    return field


def create_apple_health_nutrition_run(db: Session, *, user_id: UUID) -> NutritionIngestionRun:
    return create_ingestion_run(
        db,
        user_id=user_id,
        provider_key=_PROVIDER,
        source_instance_id=apple_health_source_instance_id(user_id),
        connector_variant=_CONNECTOR,
        status="running",
        coverage_state=CoverageState.UNKNOWN.value,
        provider_metadata={"connector_variant": _CONNECTOR},
    )


def persist_apple_food_correlation(
    db: Session,
    *,
    user_id: UUID,
    run: NutritionIngestionRun,
    correlation: AppleFoodCorrelation,
    timezone: str,
) -> NutritionConsumptionEvent:
    fingerprint = _fingerprint(correlation)
    source_instance_id = apple_health_source_instance_id(user_id)
    external_uuid = (
        correlation.external_uuid
        if correlation.external_uuid
        and len(correlation.external_uuid.encode("utf-8")) <= 255
        else None
    )
    source_record_id = external_uuid
    local_date = (
        local_date_for(correlation.start_at, timezone)
        if correlation.start_at is not None
        else None
    )
    metadata = _event_metadata(correlation, fingerprint)
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
        local_date=local_date,
        provider_civil_datetime=None,
        provider_timezone=None,
        canonical_start_at=correlation.start_at,
        canonical_end_at=correlation.end_at,
        timezone_source="export_timestamp",
        time_confidence="exact" if correlation.start_at and correlation.end_at else "unknown",
        presence_state=(
            PresenceState.SUPPLIED.value if correlation.nutrients else PresenceState.MISSING.value
        ),
        coverage_state=CoverageState.PARTIAL.value,
        resolution_state=ResolutionState.RESOLVED.value,
        lineage_state=LineageState.CONFIRMED.value,
        payload_hash=fingerprint,
        provider_metadata=metadata,
    )

    sorted_nutrients = sorted(
        correlation.nutrients,
        key=lambda item: (
            item.raw_type,
            item.raw_value or "",
            item.unit or "",
            item.start_at.isoformat() if item.start_at else "",
            item.end_at.isoformat() if item.end_at else "",
        ),
    )
    seen_metrics: set[str] = set()
    canonical_count = 0
    for index, nutrient in enumerate(sorted_nutrients):
        provider_value = _database_safe_value(nutrient.value)
        metric_key, canonical_unit, canonical_value = _canonical_value(nutrient, provider_value)
        duplicate = metric_key is not None and metric_key in seen_metrics
        if duplicate:
            metric_key = None
            canonical_unit = None
            canonical_value = None
        if metric_key is not None:
            seen_metrics.add(metric_key)
            canonical_count += 1
        nutrient_metadata = _safe_metadata(
            {
                "source_name": nutrient.source_name,
                "source_version": nutrient.source_version,
                "apple_external_uuid": dict(nutrient.metadata).get("HKExternalUUID"),
                "food_name": dict(nutrient.metadata).get("HKFoodType"),
            }
        )
        raw_type = nutrient.raw_type[:80]
        path = f"nutrients[{index}].{raw_type}"[:_MAX_PATH]
        _field(
            db,
            user_id=user_id,
            source_observation_id=source.id,
            provider_field_path=path,
            nutrient=nutrient,
            provider_raw_value_decimal=provider_value,
            metric_key=metric_key,
            canonical_unit=canonical_unit,
            canonical_value=canonical_value,
            role=(
                ObservationRole.CANONICAL.value
                if metric_key is not None
                else ObservationRole.PROVIDER.value
            ),
            resolution_state=(
                ResolutionState.RESOLVED.value
                if metric_key is not None
                else ResolutionState.UNRESOLVED.value
            ),
            lineage_state=(
                LineageState.CONFIRMED.value
                if metric_key is not None
                else LineageState.UNKNOWN.value
            ),
            provider_metadata=nutrient_metadata,
        )

    identity_value = external_uuid or f"anonymous:{fingerprint}"
    identity = get_or_create_external_identity(
        db,
        user_id=user_id,
        provider_key=_PROVIDER,
        namespace=_EXTERNAL_UUID_NAMESPACE if external_uuid else _SOURCE_NAMESPACE,
        identity_value=identity_value,
        identity_kind="food_correlation",
        source_instance_id=source_instance_id,
        provider_metadata=metadata,
    )
    event = get_or_create_consumption_event(
        db,
        user_id=user_id,
        source_observation_id=source.id,
        provider_key=_PROVIDER,
        source_instance_id=source_instance_id,
        event_kind=ConsumptionEventKind.PRODUCT.value,
        logical_event_key=identity_value,
        provider_civil_datetime=None,
        provider_timezone=None,
        canonical_start_at=correlation.start_at,
        canonical_end_at=correlation.end_at,
        local_date=local_date,
        presence_state=(
            PresenceState.SUPPLIED.value if correlation.nutrients else PresenceState.MISSING.value
        ),
        coverage_state=CoverageState.PARTIAL.value,
        resolution_state=(
            ResolutionState.RESOLVED.value if canonical_count else ResolutionState.UNRESOLVED.value
        ),
        lineage_state=LineageState.CONFIRMED.value,
        content_hash=fingerprint,
        provider_metadata=metadata,
    )
    event_provenance = db.scalar(
        select(NutritionProvenance).where(
            NutritionProvenance.user_id == user_id,
            NutritionProvenance.source_observation_id == source.id,
            NutritionProvenance.consumption_event_id == event.id,
            NutritionProvenance.role == ObservationRole.PROVIDER.value,
        )
    )
    if event_provenance is None:
        db.add(
            NutritionProvenance(
                user_id=user_id,
                source_observation_id=source.id,
                consumption_event_id=event.id,
                role=ObservationRole.PROVIDER.value,
                lineage_state=LineageState.CONFIRMED.value,
                provider_metadata=metadata,
            )
        )
    get_or_create_identity_link(
        db,
        user_id=user_id,
        external_identity_id=identity.id,
        link_role="event_identity",
        consumption_event_id=event.id,
    )
    run.coverage_state = CoverageState.PARTIAL.value
    db.flush()
    return event


def finish_apple_health_nutrition_run(db: Session, run: NutritionIngestionRun) -> None:
    run.status = "completed"
    run.coverage_state = (
        CoverageState.PARTIAL.value
        if run.coverage_state != CoverageState.UNKNOWN.value
        else CoverageState.UNKNOWN.value
    )
    run.finished_at = datetime.now(UTC)
    db.flush()


def fail_apple_health_nutrition_run(db: Session, run_id: UUID) -> None:
    run = db.get(NutritionIngestionRun, run_id)
    if run is None:
        return
    run.status = "partial"
    run.coverage_state = CoverageState.PARTIAL.value
    run.finished_at = datetime.now(UTC)
    db.flush()
