from __future__ import annotations

from typing import Final

WEIGHT_METRIC: Final = "weight_kg"
WEIGHT_UNIT: Final = "kg"
GOOGLE_HEALTH_WEIGHT_SOURCE_TYPE: Final = "google_health_weight_v4"
GOOGLE_HEALTH_WEIGHT_SOURCE_TYPE_GROUP: Final = (GOOGLE_HEALTH_WEIGHT_SOURCE_TYPE,)
WEIGHT_PROVIDER_SOURCE_TYPES: dict[str, str] = {
    "apple_health": "apple_health_xml",
    "health_auto_export": "health_auto_export_v2",
    "yazio": "yazio_export_v1",
}
WEIGHT_PROVIDER_SOURCE_TYPE_GROUPS: dict[str, tuple[str, ...]] = {
    "apple_health": ("apple_health_xml", "health_auto_export_v2"),
    "yazio": ("yazio_export_v1",),
}
WEIGHT_SOURCE_TYPES = frozenset(WEIGHT_PROVIDER_SOURCE_TYPES.values())


def weight_source_type(provider_key: str) -> str:
    return WEIGHT_PROVIDER_SOURCE_TYPES[provider_key]


__all__ = [
    "WEIGHT_METRIC",
    "WEIGHT_PROVIDER_SOURCE_TYPES",
    "WEIGHT_PROVIDER_SOURCE_TYPE_GROUPS",
    "WEIGHT_SOURCE_TYPES",
    "WEIGHT_UNIT",
    "weight_source_type",
]
