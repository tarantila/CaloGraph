from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from app.activity import ACTIVITY_PROVIDER_SOURCE_TYPES
from app.weight import WEIGHT_PROVIDER_SOURCE_TYPES

NUTRITION_DATA_AREA: Final = "nutrition"
WEIGHT_DATA_AREA: Final = "weight"
ACTIVITY_ENERGY_DATA_AREA: Final = "activity_energy"
SUPPORTED_PROVIDER_KEYS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        NUTRITION_DATA_AREA: ("google_health", "yazio", "apple_health"),
        WEIGHT_DATA_AREA: ("yazio", "apple_health", "health_auto_export"),
        ACTIVITY_ENERGY_DATA_AREA: ("yazio", "apple_health", "health_auto_export"),
    }
)


def effective_provider_order(data_area: str, provider_keys: Sequence[str]) -> tuple[str, ...]:
    """Return saved providers followed by missing providers in registry order."""
    normalized_area = normalize_data_area(data_area)
    supported = SUPPORTED_PROVIDER_KEYS.get(normalized_area)
    if supported is None:
        raise ValueError("unknown data area")
    effective: list[str] = []
    seen: set[str] = set()
    for provider_key in provider_keys:
        normalized_provider = normalize_provider_key(provider_key)
        if normalized_provider not in supported:
            raise ValueError("provider is not supported for data area")
        if normalized_provider in seen:
            raise ValueError("provider list must not contain duplicates")
        seen.add(normalized_provider)
        effective.append(normalized_provider)
    effective.extend(provider_key for provider_key in supported if provider_key not in seen)
    return tuple(effective)


@dataclass(frozen=True, slots=True)
class ProviderAvailability:
    provider_key: str
    available: bool
    status: str


def normalize_data_area(data_area: str) -> str:
    if not isinstance(data_area, str):
        raise ValueError("data area must be a string")
    normalized = data_area.strip().lower()
    if not normalized or any(character.isspace() for character in normalized):
        raise ValueError("data area must be a non-empty token")
    return normalized


def normalize_provider_key(provider_key: str) -> str:
    if not isinstance(provider_key, str):
        raise ValueError("provider must be a string")
    normalized = provider_key.strip().lower()
    if not normalized or any(character.isspace() for character in normalized):
        raise ValueError("provider must be a non-empty token")
    return normalized


def validate_provider_preference(data_area: str, provider_key: str) -> tuple[str, str]:
    normalized_area = normalize_data_area(data_area)
    normalized_provider = normalize_provider_key(provider_key)
    supported = SUPPORTED_PROVIDER_KEYS.get(normalized_area)
    if supported is None:
        raise ValueError("unknown data area")
    if normalized_provider not in supported:
        raise ValueError("provider is not supported for data area")
    return normalized_area, normalized_provider


__all__ = [
    "ACTIVITY_ENERGY_DATA_AREA",
    "NUTRITION_DATA_AREA",
    "SUPPORTED_PROVIDER_KEYS",
    "WEIGHT_DATA_AREA",
    "ProviderAvailability",
    "effective_provider_order",
    "normalize_data_area",
    "normalize_provider_key",
    "validate_provider_preference",
]
