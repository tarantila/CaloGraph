from typing import Final

ACTIVE_ENERGY_METRIC: Final = "active_energy_kcal"
ACTIVITY_MODES: Final = frozenset({"off", "full"})
ACTIVITY_SOURCE_TYPES: Final = frozenset(
    {
        "yazio_export_v1",
        "apple_health_xml",
        "health_auto_export_v2",
    }
)
