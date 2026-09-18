from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import GoogleHealthConnection, UserProviderPreference, YazioConnection
from app.nutrition.models import NutritionSourceObservation
from app.provider_preferences import NUTRITION_DATA_AREA, validate_provider_preference
from app.services.apple_health_nutrition_ingestion import apple_health_source_instance_id


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
    preference = db.get(UserProviderPreference, (user_id, NUTRITION_DATA_AREA))
    if preference is None:
        return None
    try:
        _, provider_key = validate_provider_preference(
            NUTRITION_DATA_AREA, preference.provider_key
        )
    except ValueError as exc:
        raise NutritionProviderNotReady("configured provider is invalid") from exc
    source_instance_ids = _available_owned_source_instance_ids(
        db,
        user_id=user_id,
        provider_key=provider_key,
    )
    if not source_instance_ids:
        raise NutritionProviderUnavailable("configured provider is unavailable")
    if len(source_instance_ids) != 1:
        raise NutritionProviderNotReady("provider source instance is not unambiguous")
    return NutritionProviderSelection(provider_key, source_instance_ids[0])


__all__ = [
    "NutritionProviderNotReady",
    "NutritionProviderSelection",
    "NutritionProviderSelectionError",
    "NutritionProviderUnavailable",
    "resolve_nutrition_provider",
]
