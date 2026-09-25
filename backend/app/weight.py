from __future__ import annotations

from typing import Final

from app.withings.constants import WITHINGS_WEIGHT_SOURCE_TYPE

WEIGHT_METRIC: Final = "weight_kg"
WEIGHT_UNIT: Final = "kg"
GOOGLE_HEALTH_WEIGHT_SOURCE_TYPE: Final = "google_health_weight_v4"
GOOGLE_HEALTH_WEIGHT_SOURCE_TYPE_GROUP: Final = (GOOGLE_HEALTH_WEIGHT_SOURCE_TYPE,)
WEIGHT_PROVIDER_SOURCE_TYPES: dict[str, str] = {
    "google_health": GOOGLE_HEALTH_WEIGHT_SOURCE_TYPE,
    "apple_health": "apple_health_xml",
    "health_auto_export": "health_auto_export_v2",
    "yazio": "yazio_export_v1",
    "withings": WITHINGS_WEIGHT_SOURCE_TYPE,
}
WEIGHT_PROVIDER_SOURCE_TYPE_GROUPS: dict[str, tuple[str, ...]] = {
    "google_health": GOOGLE_HEALTH_WEIGHT_SOURCE_TYPE_GROUP,
    "apple_health": ("apple_health_xml", "health_auto_export_v2"),
    "yazio": ("yazio_export_v1",),
    "withings": (WITHINGS_WEIGHT_SOURCE_TYPE,),
}
WEIGHT_SOURCE_TYPES = frozenset(WEIGHT_PROVIDER_SOURCE_TYPES.values()) | frozenset(
    source_type
    for source_types in WEIGHT_PROVIDER_SOURCE_TYPE_GROUPS.values()
    for source_type in source_types
)


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
