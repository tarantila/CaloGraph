from typing import Final

from app.withings.constants import WITHINGS_ACTIVITY_SOURCE_TYPE

ACTIVE_ENERGY_METRIC: Final = "active_energy_kcal"
GOOGLE_HEALTH_ACTIVITY_SOURCE_TYPE: Final = "google_health_activity_v4"
GOOGLE_HEALTH_ACTIVITY_SOURCE_TYPE_GROUP: Final = (GOOGLE_HEALTH_ACTIVITY_SOURCE_TYPE,)
ACTIVITY_MODES: Final = frozenset({"off", "full"})
ACTIVITY_PROVIDER_SOURCE_TYPES: Final = {
    "google_health": GOOGLE_HEALTH_ACTIVITY_SOURCE_TYPE,
    "apple_health": "apple_health_xml",
    "health_auto_export": "health_auto_export_v2",
    "yazio": "yazio_export_v1",
    "withings": WITHINGS_ACTIVITY_SOURCE_TYPE,
}
ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS: Final = {
    "google_health": GOOGLE_HEALTH_ACTIVITY_SOURCE_TYPE_GROUP,
    "apple_health": ("apple_health_xml", "health_auto_export_v2"),
    "yazio": ("yazio_export_v1",),
    "withings": (WITHINGS_ACTIVITY_SOURCE_TYPE,),
}
ACTIVITY_SOURCE_TYPES: Final = frozenset(ACTIVITY_PROVIDER_SOURCE_TYPES.values())
