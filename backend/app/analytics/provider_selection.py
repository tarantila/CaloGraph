from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import GoogleHealthConnection, UserProviderPreference, YazioConnection
from app.provider_preferences import NUTRITION_DATA_AREA, validate_provider_preference
from app.services.apple_health_nutrition_ingestion import apple_health_source_instance_id
from app.services.provider_preferences import provider_is_available


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


def _owned_source_instance_ids(db: Session, *, user_id: UUID, provider_key: str) -> tuple[UUID, ...]:
    if provider_key == "yazio":
        return tuple(
            db.scalars(select(YazioConnection.id).where(YazioConnection.user_id == user_id)).all()
        )
    if provider_key == "google_health":
        return tuple(
            db.scalars(
                select(GoogleHealthConnection.id).where(GoogleHealthConnection.user_id == user_id)
            ).all()
        )
    if provider_key == "apple_health":
        return (apple_health_source_instance_id(user_id),)
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
    if not provider_is_available(
        db,
        user_id=user_id,
        data_area=NUTRITION_DATA_AREA,
        provider_key=provider_key,
    ):
        raise NutritionProviderUnavailable("configured provider is unavailable")
    source_instance_ids = _owned_source_instance_ids(
        db,
        user_id=user_id,
        provider_key=provider_key,
    )
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
