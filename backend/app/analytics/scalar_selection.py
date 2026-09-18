from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.provider_preferences import validate_provider_preference
from app.services.provider_preferences import provider_is_available
from app.source_priority.compatibility import list_provider_preferences


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
    provider_sources: tuple[tuple[str, str], ...] = ()


def resolve_scalar_provider(
    db: Session,
    *,
    user_id: UUID,
    data_area: str,
    source_types: dict[str, str],
) -> ScalarProviderSelection | None:
    preferences = [
        preference
        for preference in list_provider_preferences(db, user_id)
        if preference.data_area == data_area
    ]
    if not preferences:
        return None

    provider_sources: list[tuple[str, str]] = []
    seen_provider_keys: set[str] = set()
    for preference in preferences:
        try:
            _, provider_key = validate_provider_preference(data_area, preference.provider_key)
            source_type = source_types[provider_key]
        except (KeyError, ValueError) as exc:
            raise ScalarProviderNotReady("configured provider is invalid") from exc
        if provider_key in seen_provider_keys:
            raise ScalarProviderNotReady("configured provider is duplicated")
        seen_provider_keys.add(provider_key)
        if provider_is_available(
            db,
            user_id=user_id,
            data_area=data_area,
            provider_key=provider_key,
        ):
            provider_sources.append((provider_key, source_type))

    if not provider_sources:
        raise ScalarProviderUnavailable("configured provider is unavailable")
    provider_key, source_type = provider_sources[0]
    return ScalarProviderSelection(
        data_area,
        provider_key,
        source_type,
        tuple(provider_sources),
    )


__all__ = [
    "ScalarProviderNotReady",
    "ScalarProviderSelection",
    "ScalarProviderSelectionError",
    "ScalarProviderUnavailable",
    "resolve_scalar_provider",
]
