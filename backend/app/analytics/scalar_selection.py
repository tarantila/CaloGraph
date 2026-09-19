from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.provider_preferences import effective_provider_order, validate_provider_preference
from app.services.provider_preferences import (
    provider_is_available,
    resolve_provider_source_type,
)
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
    source_types: Mapping[str, str | Sequence[str]],
) -> ScalarProviderSelection | None:
    saved_preferences = [
        preference
        for preference in list_provider_preferences(db, user_id)
        if preference.data_area == data_area
    ]
    try:
        provider_keys = effective_provider_order(
            data_area,
            tuple(preference.provider_key for preference in saved_preferences),
        )
    except ValueError as exc:
        raise ScalarProviderNotReady("configured provider is invalid") from exc

    provider_sources: list[tuple[str, str]] = []
    for provider_key in provider_keys:
        try:
            _, provider_key = validate_provider_preference(data_area, provider_key)
            configured_source_types = source_types[provider_key]
        except (KeyError, ValueError) as exc:
            raise ScalarProviderNotReady("configured provider is invalid") from exc
        if not provider_is_available(
            db,
            user_id=user_id,
            data_area=data_area,
            provider_key=provider_key,
        ):
            continue
        try:
            source_type = resolve_provider_source_type(
                db,
                user_id=user_id,
                data_area=data_area,
                provider_key=provider_key,
                configured=configured_source_types,
            )
        except ValueError as exc:
            raise ScalarProviderNotReady("provider transports are ambiguous") from exc
        provider_sources.append((provider_key, source_type))

    if not provider_sources:
        if not saved_preferences:
            return None
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
