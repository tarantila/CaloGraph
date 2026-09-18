from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.activity import ACTIVE_ENERGY_METRIC, ACTIVITY_PROVIDER_SOURCE_TYPES
from app.config import settings
from app.models import GoogleHealthConnection, HealthSample, User, YazioConnection
from app.nutrition.resolution.discovery import discover_nutrition_providers
from app.provider_preferences import (
    ACTIVITY_ENERGY_DATA_AREA,
    NUTRITION_DATA_AREA,
    SUPPORTED_PROVIDER_KEYS,
    WEIGHT_DATA_AREA,
    ProviderAvailability,
    normalize_data_area,
    normalize_provider_key,
)
from app.weight import WEIGHT_METRIC, WEIGHT_PROVIDER_SOURCE_TYPES


def _availability(
    data_area: str,
    statuses: dict[str, str],
) -> tuple[ProviderAvailability, ...]:
    return tuple(
        ProviderAvailability(
            provider_key=provider_key,
            available=statuses[provider_key] == "available",
            status=statuses[provider_key],
        )
        for provider_key in sorted(SUPPORTED_PROVIDER_KEYS[data_area])
    )


def _yazio_status(db: Session, user_id: UUID) -> str:
    connection = db.scalar(select(YazioConnection).where(YazioConnection.user_id == user_id))
    if not settings.yazio_enabled:
        return "disabled"
    return "available" if connection is not None else "not_configured"


def _nutrition_availability(db: Session, user_id: UUID) -> tuple[ProviderAvailability, ...]:
    google = db.scalar(select(GoogleHealthConnection).where(GoogleHealthConnection.user_id == user_id))
    if not settings.google_health_enabled or not settings.google_health_client_id or not settings.google_health_client_secret:
        google_status = "disabled"
    elif google is None:
        google_status = "not_configured"
    elif google.state != "active":
        google_status = "reauth_required"
    else:
        google_status = "available"

    evidenced_providers = {
        provider.provider_key
        for provider in discover_nutrition_providers(db, user_id=user_id).providers
    }
    statuses = {
        "apple_health": "available" if "apple_health" in evidenced_providers else "no_data",
        "google_health": google_status,
        "yazio": _yazio_status(db, user_id),
    }
    return _availability(NUTRITION_DATA_AREA, statuses)


def _sample_provider_statuses(
    db: Session,
    *,
    user_id: UUID,
    metric_type: str,
    source_types: dict[str, str],
) -> dict[str, str]:
    evidenced = set(
        db.scalars(
            select(HealthSample.source_type)
            .where(
                HealthSample.user_id == user_id,
                HealthSample.metric_type == metric_type,
                HealthSample.source_type.in_(source_types.values()),
                HealthSample.value > 0,
            )
            .distinct()
        )
    )
    return {
        provider_key: ("available" if source_type in evidenced else "no_data")
        for provider_key, source_type in source_types.items()
    }


def _weight_availability(db: Session, user_id: UUID) -> tuple[ProviderAvailability, ...]:
    statuses = _sample_provider_statuses(
        db,
        user_id=user_id,
        metric_type=WEIGHT_METRIC,
        source_types=WEIGHT_PROVIDER_SOURCE_TYPES,
    )
    statuses["yazio"] = _yazio_status(db, user_id)
    return _availability(WEIGHT_DATA_AREA, statuses)


def _activity_availability(db: Session, user_id: UUID) -> tuple[ProviderAvailability, ...]:
    statuses = _sample_provider_statuses(
        db,
        user_id=user_id,
        metric_type=ACTIVE_ENERGY_METRIC,
        source_types=ACTIVITY_PROVIDER_SOURCE_TYPES,
    )
    return _availability(ACTIVITY_ENERGY_DATA_AREA, statuses)


def provider_availability(
    db: Session,
    *,
    user_id: UUID,
    data_area: str,
) -> tuple[ProviderAvailability, ...]:
    normalized_area = normalize_data_area(data_area)
    if normalized_area == NUTRITION_DATA_AREA:
        return _nutrition_availability(db, user_id)
    if normalized_area == WEIGHT_DATA_AREA:
        return _weight_availability(db, user_id)
    if normalized_area == ACTIVITY_ENERGY_DATA_AREA:
        return _activity_availability(db, user_id)
    raise ValueError("unknown data area")

def apply_activity_provider_to_current_target(
    db: Session,
    *,
    user: User,
    source_type: str,
) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.models import NutritionTarget

    user_id = user.id
    timezone = user.timezone
    today = datetime.now(ZoneInfo(timezone)).date()
    targets = list(
        db.scalars(
            select(NutritionTarget)
            .where(NutritionTarget.user_id == user_id)
            .order_by(NutritionTarget.valid_from)
            .with_for_update()
        )
    )
    current = next(
        (
            item
            for item in targets
            if item.valid_from <= today
            and (item.valid_to is None or today < item.valid_to)
        ),
        None,
    )
    if current is None or current.activity_mode != "full":
        return
    if current.activity_source_type == source_type:
        return
    if current.valid_from == today:
        current.activity_source_type = source_type
        return
    successor = next((item for item in targets if item.valid_from > today), None)
    current.valid_to = today
    db.add(
        NutritionTarget(
            user_id=user_id,
            valid_from=today,
            valid_to=successor.valid_from if successor else None,
            calories_kcal=current.calories_kcal,
            maintenance_kcal=current.maintenance_kcal,
            target_weight_min_kg=current.target_weight_min_kg,
            target_weight_max_kg=current.target_weight_max_kg,
            activity_mode=current.activity_mode,
            activity_source_type=source_type,
            protein_g=current.protein_g,
            carbs_g=current.carbs_g,
            fat_g=current.fat_g,
            fiber_g=current.fiber_g,
            water_ml=current.water_ml,
        )
    )



def provider_is_available(
    db: Session,
    *,
    user_id: UUID,
    data_area: str,
    provider_key: str,
) -> bool:
    normalized_provider = normalize_provider_key(provider_key)
    return any(
        item.provider_key == normalized_provider and item.available
        for item in provider_availability(db, user_id=user_id, data_area=data_area)
    )

__all__ = [
    "apply_activity_provider_to_current_target",
    "provider_availability",
    "provider_is_available",
]
