from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import GoogleHealthConnection, YazioConnection
from app.nutrition.models import NutritionSourceObservation
from app.provider_preferences import NUTRITION_DATA_AREA, validate_provider_preference
from app.services.apple_health_nutrition_ingestion import apple_health_source_instance_id
from app.source_priority.compatibility import list_provider_preferences


class NutritionProviderSelectionError(RuntimeError):
    """A persisted nutrition provider cannot be read safely."""


class NutritionProviderUnavailable(NutritionProviderSelectionError):
    pass


class NutritionProviderNotReady(NutritionProviderSelectionError):
    pass


@dataclass(frozen=True, slots=True)
class NutritionProviderSelection:
    provider_key: str
    source_instance_id: UUID
    provider_sources: tuple[tuple[str, UUID], ...] = ()

def _available_owned_source_instance_ids(
    db: Session,
    *,
    user_id: UUID,
    provider_key: str,
) -> tuple[UUID, ...]:
    if provider_key == "yazio":
        if not settings.yazio_enabled:
            return ()
        return tuple(
            db.scalars(select(YazioConnection.id).where(YazioConnection.user_id == user_id)).all()
        )
    if provider_key == "google_health":
        if (
            not settings.google_health_enabled
            or not settings.google_health_client_id
            or not settings.google_health_client_secret
        ):
            return ()
        return tuple(
            db.scalars(
                select(GoogleHealthConnection.id).where(
                    GoogleHealthConnection.user_id == user_id,
                    GoogleHealthConnection.state == "active",
                )
            ).all()
        )
    if provider_key == "apple_health":
        source_instance_id = apple_health_source_instance_id(user_id)
        has_observation = db.scalar(
            select(NutritionSourceObservation.id).where(
                NutritionSourceObservation.user_id == user_id,
                NutritionSourceObservation.provider_key == provider_key,
                NutritionSourceObservation.source_instance_id == source_instance_id,
            )
        )
        return (source_instance_id,) if has_observation is not None else ()
    return ()


def resolve_nutrition_provider(
    db: Session,
    *,
    user_id: UUID,
) -> NutritionProviderSelection | None:
    preferences = [
        item
        for item in list_provider_preferences(db, user_id)
        if item.data_area == NUTRITION_DATA_AREA
    ]
    if not preferences:
        return None

    provider_sources: list[tuple[str, UUID]] = []
    seen_provider_keys: set[str] = set()
    for preference in preferences:
        try:
            _, provider_key = validate_provider_preference(
                NUTRITION_DATA_AREA, preference.provider_key
            )
        except ValueError as exc:
            raise NutritionProviderNotReady("configured provider is invalid") from exc
        if provider_key in seen_provider_keys:
            raise NutritionProviderNotReady("configured provider is duplicated")
        seen_provider_keys.add(provider_key)
        source_instance_ids = _available_owned_source_instance_ids(
            db,
            user_id=user_id,
            provider_key=provider_key,
        )
        if not source_instance_ids:
            continue
        if len(source_instance_ids) != 1:
            raise NutritionProviderNotReady("provider source instance is not unambiguous")
        provider_sources.append((provider_key, source_instance_ids[0]))

    if not provider_sources:
        raise NutritionProviderUnavailable("configured providers are unavailable")
    provider_key, source_instance_id = provider_sources[0]
    return NutritionProviderSelection(provider_key, source_instance_id, tuple(provider_sources))


__all__ = [
    "NutritionProviderNotReady",
    "NutritionProviderSelection",
    "NutritionProviderSelectionError",
    "NutritionProviderUnavailable",
    "resolve_nutrition_provider",
]
