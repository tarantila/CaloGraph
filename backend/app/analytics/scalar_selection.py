from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import UserProviderPreference
from app.provider_preferences import validate_provider_preference
from app.services.provider_preferences import provider_is_available


class ScalarProviderSelectionError(RuntimeError):
    """A persisted scalar provider cannot be read safely."""


class ScalarProviderUnavailable(ScalarProviderSelectionError):
    pass


class ScalarProviderNotReady(ScalarProviderSelectionError):
    pass


@dataclass(frozen=True, slots=True)
class ScalarProviderSelection:
    data_area: str
    provider_key: str
    source_type: str


def resolve_scalar_provider(
    db: Session,
    *,
    user_id: UUID,
    data_area: str,
    source_types: dict[str, str],
) -> ScalarProviderSelection | None:
    preference = db.get(UserProviderPreference, (user_id, data_area))
    if preference is None:
        return None
    try:
        _, provider_key = validate_provider_preference(data_area, preference.provider_key)
        source_type = source_types[provider_key]
    except (KeyError, ValueError) as exc:
        raise ScalarProviderNotReady("configured provider is invalid") from exc
    if not provider_is_available(
        db,
        user_id=user_id,
        data_area=data_area,
        provider_key=provider_key,
    ):
        raise ScalarProviderUnavailable("configured provider is unavailable")
    return ScalarProviderSelection(data_area, provider_key, source_type)


__all__ = [
    "ScalarProviderNotReady",
    "ScalarProviderSelection",
    "ScalarProviderSelectionError",
    "ScalarProviderUnavailable",
    "resolve_scalar_provider",
]
