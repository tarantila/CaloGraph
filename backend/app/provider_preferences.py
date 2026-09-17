from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

NUTRITION_DATA_AREA: Final = "nutrition"
SUPPORTED_PROVIDER_KEYS: Mapping[str, frozenset[str]] = MappingProxyType(
    {NUTRITION_DATA_AREA: frozenset({"apple_health", "google_health", "yazio"})}
)


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
    "NUTRITION_DATA_AREA",
    "SUPPORTED_PROVIDER_KEYS",
    "ProviderAvailability",
    "normalize_data_area",
    "normalize_provider_key",
    "validate_provider_preference",
]
