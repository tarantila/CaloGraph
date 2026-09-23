from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.activity import (
    ACTIVE_ENERGY_METRIC,
    ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS,
)
from app.config import settings
from app.google_health.constants import GOOGLE_HEALTH_REQUIRED_SCOPES
from app.models import (
    GoogleHealthConnection,
    HealthSample,
    NutritionTarget,
    NutritionTargetActivitySource,
    User,
    YazioConnection,
)
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
from app.weight import WEIGHT_METRIC, WEIGHT_PROVIDER_SOURCE_TYPE_GROUPS


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
        for provider_key in SUPPORTED_PROVIDER_KEYS[data_area]
    )


def _yazio_status(db: Session, user_id: UUID) -> str:
    connection = db.scalar(select(YazioConnection).where(YazioConnection.user_id == user_id))
    if not settings.yazio_enabled:
        return "disabled"
    return "available" if connection is not None else "not_configured"


def _normalize_source_types(configured: str | Sequence[str]) -> tuple[str, ...]:
    return (configured,) if isinstance(configured, str) else tuple(configured)


def _source_types(
    data_area: str,
    provider_key: str,
    configured: str | Sequence[str] | None = None,
) -> tuple[str, ...]:
    if configured is not None:
        return _normalize_source_types(configured)
    groups = {
        WEIGHT_DATA_AREA: WEIGHT_PROVIDER_SOURCE_TYPE_GROUPS,
        ACTIVITY_ENERGY_DATA_AREA: ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS,
    }.get(data_area, {})
    source_types = groups.get(provider_key)
    if source_types is None:
        raise ValueError("provider source types are not configured")
    return tuple(source_types)


def _transport_evidence(
    db: Session,
    *,
    user_id: UUID,
    data_area: str,
    provider_key: str,
    configured: str | Sequence[str] | None = None,
) -> tuple[str, ...]:
    metric_type = {
        WEIGHT_DATA_AREA: WEIGHT_METRIC,
        ACTIVITY_ENERGY_DATA_AREA: ACTIVE_ENERGY_METRIC,
    }.get(data_area)
    if metric_type is None:
        return ()
    source_types = _source_types(data_area, provider_key, configured)
    evidenced = set(
        db.scalars(
            select(HealthSample.source_type)
            .where(
                HealthSample.user_id == user_id,
                HealthSample.metric_type == metric_type,
                HealthSample.source_type.in_(source_types),
            )
            .distinct()
        )
    )
    return tuple(source_type for source_type in source_types if source_type in evidenced)


def resolve_provider_source_type(
    db: Session,
    *,
    user_id: UUID,
    data_area: str,
    provider_key: str,
    configured: str | Sequence[str] | None = None,
    fallback: str | None = None,
) -> str:
    source_types = _source_types(data_area, provider_key, configured)
    evidence = _transport_evidence(
        db,
        user_id=user_id,
        data_area=data_area,
        provider_key=provider_key,
        configured=source_types,
    )
    if len(evidence) > 1:
        raise ValueError("provider transports are ambiguous")
    return evidence[0] if evidence else (fallback or source_types[0])


def _nutrition_availability(db: Session, user_id: UUID) -> tuple[ProviderAvailability, ...]:
    google = db.scalar(select(GoogleHealthConnection).where(GoogleHealthConnection.user_id == user_id))
    evidenced_providers = {
        provider.provider_key
        for provider in discover_nutrition_providers(db, user_id=user_id).providers
    }
    if not settings.google_health_enabled or not settings.google_health_client_id or not settings.google_health_client_secret:
        google_status = "disabled"
    elif google is None:
        google_status = "not_configured"
    elif google.state != "active":
        google_status = "reauth_required"
    else:
        google_status = "available" if "google_health" in evidenced_providers else "no_data"
    if not settings.yazio_enabled:
        yazio_status = "disabled"
    else:
        yazio_status = "available" if "yazio" in evidenced_providers else "no_data"
    statuses = {
        "apple_health": "available" if "apple_health" in evidenced_providers else "no_data",
        "google_health": google_status,
        "yazio": yazio_status,
    }
    return _availability(NUTRITION_DATA_AREA, statuses)


def _sample_provider_statuses(
    db: Session,
    *,
    user_id: UUID,
    metric_type: str,
    source_types: Mapping[str, str | Sequence[str]],
    include_zero: bool = False,
) -> dict[str, str]:
    value_filter = HealthSample.value >= 0 if include_zero else HealthSample.value > 0
    raw_source_types = tuple(
        dict.fromkeys(
            raw_type
            for configured in source_types.values()
            for raw_type in _normalize_source_types(configured)
        )
    )
    evidenced = set(
        db.scalars(
            select(HealthSample.source_type)
            .where(
                HealthSample.user_id == user_id,
                HealthSample.metric_type == metric_type,
                HealthSample.source_type.in_(raw_source_types),
                value_filter,
            )
            .distinct()
        )
    )
    return {
        provider_key: (
            "available"
            if any(
                raw_type in evidenced
                for raw_type in _normalize_source_types(configured)
            )
            else "no_data"
        )
        for provider_key, configured in source_types.items()
    }


def _google_scalar_status(
    db: Session,
    *,
    user_id: UUID,
    metric_type: str,
    source_type: str,
    include_zero: bool,
) -> str:
    if (
        not settings.google_health_enabled
        or not settings.google_health_client_id
        or not settings.google_health_client_secret
    ):
        return "disabled"
    connection = db.scalar(
        select(GoogleHealthConnection).where(GoogleHealthConnection.user_id == user_id)
    )
    if connection is None:
        return "not_configured"
    if (
        connection.state != "active"
        or not GOOGLE_HEALTH_REQUIRED_SCOPES.issubset(set(connection.granted_scopes or ()))
    ):
        return "reauth_required"
    value_filter = HealthSample.value >= 0 if include_zero else HealthSample.value > 0
    evidence = db.scalar(
        select(HealthSample.id)
        .where(
            HealthSample.user_id == user_id,
            HealthSample.source_type == source_type,
            HealthSample.source_identifier == str(connection.id),
            HealthSample.metric_type == metric_type,
            value_filter,
        )
        .limit(1)
    )
    return "available" if evidence is not None else "no_data"


def _weight_availability(db: Session, user_id: UUID) -> tuple[ProviderAvailability, ...]:
    statuses = _sample_provider_statuses(
        db,
        user_id=user_id,
        metric_type=WEIGHT_METRIC,
        source_types=WEIGHT_PROVIDER_SOURCE_TYPE_GROUPS,
    )
    statuses["google_health"] = _google_scalar_status(
        db,
        user_id=user_id,
        metric_type=WEIGHT_METRIC,
        source_type=WEIGHT_PROVIDER_SOURCE_TYPE_GROUPS["google_health"][0],
        include_zero=False,
    )
    statuses["yazio"] = _yazio_status(db, user_id)
    return _availability(WEIGHT_DATA_AREA, statuses)


def _activity_availability(db: Session, user_id: UUID) -> tuple[ProviderAvailability, ...]:
    statuses = _sample_provider_statuses(
        db,
        user_id=user_id,
        metric_type=ACTIVE_ENERGY_METRIC,
        source_types=ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS,
        include_zero=True,
    )
    statuses["google_health"] = _google_scalar_status(
        db,
        user_id=user_id,
        metric_type=ACTIVE_ENERGY_METRIC,
        source_type=ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS["google_health"][0],
        include_zero=True,
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


def replace_activity_target_sources(
    db: Session,
    target: NutritionTarget,
    source_types: Sequence[str],
) -> None:
    provider_by_source_type = {
        raw_source_type: provider_key
        for provider_key, raw_source_types in ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS.items()
        for raw_source_type in raw_source_types
    }
    unique_source_types = tuple(dict.fromkeys(source_types))
    for snapshot in tuple(target.activity_sources):
        db.delete(snapshot)
    db.flush()
    target.activity_sources = [
        NutritionTargetActivitySource(
            target_id=target.id,
            user_id=target.user_id,
            priority=priority,
            provider_key=provider_by_source_type.get(source_type),
            source_type=source_type,
        )
        for priority, source_type in enumerate(unique_source_types, start=1)
    ]


def _activity_target_sources(target: NutritionTarget) -> tuple[str, ...]:
    snapshots = tuple(
        snapshot.source_type
        for snapshot in target.activity_sources
        if snapshot.source_type
    )
    if snapshots:
        return snapshots
    return (target.activity_source_type,) if target.activity_source_type else ()


def apply_activity_provider_to_current_target(
    db: Session,
    *,
    user: User,
    source_type: str | None = None,
    source_types: Sequence[str] | None = None,
) -> None:
    user_id = user.id
    today = datetime.now(ZoneInfo(user.timezone)).date()
    desired_sources = tuple(source_types or ((source_type,) if source_type else ()))
    if not desired_sources:
        return
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
    normalized_sources = tuple(dict.fromkeys(desired_sources))
    if (
        current.activity_source_type == normalized_sources[0]
        and _activity_target_sources(current) == normalized_sources
    ):
        return
    if current.valid_from == today:
        current.activity_source_type = normalized_sources[0]
        replace_activity_target_sources(db, current, normalized_sources)
        return
    successor = next((item for item in targets if item.valid_from > today), None)
    current.valid_to = today
    version = NutritionTarget(
        user_id=user_id,
        valid_from=today,
        valid_to=successor.valid_from if successor else None,
        calories_kcal=current.calories_kcal,
        maintenance_kcal=current.maintenance_kcal,
        target_weight_min_kg=current.target_weight_min_kg,
        target_weight_max_kg=current.target_weight_max_kg,
        activity_mode=current.activity_mode,
        activity_source_type=normalized_sources[0],
        protein_g=current.protein_g,
        carbs_g=current.carbs_g,
        fat_g=current.fat_g,
        fiber_g=current.fiber_g,
        water_ml=current.water_ml,
    )
    db.add(version)
    db.flush()
    replace_activity_target_sources(db, version, normalized_sources)



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
    "replace_activity_target_sources",
    "resolve_provider_source_type",
]
