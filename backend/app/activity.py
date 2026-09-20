from typing import Final

ACTIVE_ENERGY_METRIC: Final = "active_energy_kcal"
ACTIVITY_MODES: Final = frozenset({"off", "full"})
ACTIVITY_PROVIDER_SOURCE_TYPES: Final = {
    "apple_health": "apple_health_xml",
    "health_auto_export": "health_auto_export_v2",
    "yazio": "yazio_export_v1",
}
ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS: Final = {
    "apple_health": ("apple_health_xml", "health_auto_export_v2"),
    "yazio": ("yazio_export_v1",),
}
ACTIVITY_SOURCE_TYPES: Final = frozenset(ACTIVITY_PROVIDER_SOURCE_TYPES.values())
