from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

NUTRITION_DATA_AREA: Final = "nutrition"
WEIGHT_DATA_AREA: Final = "weight"
ACTIVITY_ENERGY_DATA_AREA: Final = "activity_energy"
SUPPORTED_PROVIDER_KEYS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        NUTRITION_DATA_AREA: ("google_health", "yazio", "apple_health"),
        WEIGHT_DATA_AREA: ("google_health", "yazio", "apple_health", "withings"),
        ACTIVITY_ENERGY_DATA_AREA: ("google_health", "yazio", "apple_health", "withings"),
    }
)
LEGACY_PROVIDER_KEY_ALIASES: Mapping[str, Mapping[str, str]] = MappingProxyType(
    {
        WEIGHT_DATA_AREA: MappingProxyType({"health_auto_export": "apple_health"}),
        ACTIVITY_ENERGY_DATA_AREA: MappingProxyType({"health_auto_export": "apple_health"}),
    }
)

def effective_provider_order(
    data_area: str,
    provider_keys: Sequence[str],
    *,
    include_missing: bool = True,
) -> tuple[str, ...]:
    """Return normalized saved providers, optionally followed by registry defaults."""
    normalized_area = normalize_data_area(data_area)
    supported = SUPPORTED_PROVIDER_KEYS.get(normalized_area)
    if supported is None:
        raise ValueError("unknown data area")
    effective: list[str] = []
    seen: set[str] = set()
    for provider_key in provider_keys:
        normalized_provider = canonicalize_provider_key(normalized_area, provider_key)
        if normalized_provider not in supported:
            raise ValueError("provider is not supported for data area")
        if normalized_provider in seen:
            raise ValueError("provider list must not contain duplicates")
        seen.add(normalized_provider)
        effective.append(normalized_provider)
    if include_missing:
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

def canonicalize_provider_key(data_area: str, provider_key: str) -> str:
    normalized_area = normalize_data_area(data_area)
    normalized_provider = normalize_provider_key(provider_key)
    return LEGACY_PROVIDER_KEY_ALIASES.get(normalized_area, {}).get(
        normalized_provider, normalized_provider
    )


def validate_provider_preference(data_area: str, provider_key: str) -> tuple[str, str]:
    normalized_area = normalize_data_area(data_area)
    normalized_provider = canonicalize_provider_key(normalized_area, provider_key)
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
    "canonicalize_provider_key",
    "effective_provider_order",
    "normalize_data_area",
    "normalize_provider_key",
    "validate_provider_preference",
]
