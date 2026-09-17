from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import GoogleHealthConnection, YazioConnection
from app.nutrition.resolution.discovery import discover_nutrition_providers
from app.provider_preferences import (
    NUTRITION_DATA_AREA,
    SUPPORTED_PROVIDER_KEYS,
    ProviderAvailability,
    normalize_data_area,
    normalize_provider_key,
)


def _nutrition_availability(db: Session, user_id: UUID) -> tuple[ProviderAvailability, ...]:
    yazio = db.scalar(select(YazioConnection).where(YazioConnection.user_id == user_id))
    if not settings.yazio_enabled:
        yazio_status = "disabled"
    elif yazio is None:
        yazio_status = "not_configured"
    else:
        yazio_status = "available"

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
    apple_status = "available" if "apple_health" in evidenced_providers else "no_data"

    statuses = {
        "apple_health": apple_status,
        "google_health": google_status,
        "yazio": yazio_status,
    }
    return tuple(
        ProviderAvailability(
            provider_key=provider_key,
            available=statuses[provider_key] == "available",
            status=statuses[provider_key],
        )
        for provider_key in sorted(SUPPORTED_PROVIDER_KEYS[NUTRITION_DATA_AREA])
    )


def provider_availability(
    db: Session,
    *,
    user_id: UUID,
    data_area: str,
) -> tuple[ProviderAvailability, ...]:
    normalized_area = normalize_data_area(data_area)
    if normalized_area != NUTRITION_DATA_AREA:
        raise ValueError("unknown data area")
    return _nutrition_availability(db, user_id)


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


__all__ = ["provider_availability", "provider_is_available"]
