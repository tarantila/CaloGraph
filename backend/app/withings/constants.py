from __future__ import annotations

from typing import Final

WITHINGS_API_BASE_URL: Final = "https://wbsapi.withings.net"
WITHINGS_AUTHORIZE_URL: Final = "https://account.withings.com/oauth2_user/authorize2"
WITHINGS_AUTH_URI: Final = WITHINGS_AUTHORIZE_URL
WITHINGS_TOKEN_URL: Final = f"{WITHINGS_API_BASE_URL}/v2/oauth2"
WITHINGS_TOKEN_URI: Final = WITHINGS_TOKEN_URL
WITHINGS_MEASURE_URL: Final = f"{WITHINGS_API_BASE_URL}/measure"
WITHINGS_ACTIVITY_URL: Final = f"{WITHINGS_API_BASE_URL}/v2/measure"

# Withings OAuth requires these exact domain scopes for the two supported imports.
WITHINGS_SCOPES: Final = ("user.metrics", "user.activity")
WITHINGS_REQUIRED_SCOPES: Final = frozenset(WITHINGS_SCOPES)

WITHINGS_ACTIVITY_SOURCE_TYPE: Final = "withings_activity_v2"
WITHINGS_WEIGHT_SOURCE_TYPE: Final = "withings_measure_v1"

# Values are canonical metric names and units used by HealthSample persistence.
WITHINGS_MEASURE_TYPES: Final[dict[int, tuple[str, str]]] = {
    1: ("weight_kg", "kg"),
    5: ("fat_free_mass_kg", "kg"),
    6: ("fat_ratio_pct", "%"),
    8: ("fat_mass_kg", "kg"),
    76: ("muscle_mass_kg", "kg"),
    77: ("hydration_kg", "kg"),
    88: ("bone_mass_kg", "kg"),
}
WITHINGS_SUPPORTED_MEASURES: Final = WITHINGS_MEASURE_TYPES

# The upstream API supports a page size of 100. Keep both page and aggregate limits
# explicit so malformed pagination cannot create an unbounded import.
WITHINGS_MAX_PAGE_SIZE: Final = 100
WITHINGS_MAX_PAGES: Final = 31
WITHINGS_MAX_RECORDS: Final = WITHINGS_MAX_PAGE_SIZE * WITHINGS_MAX_PAGES
WITHINGS_PAGE_SIZE: Final = WITHINGS_MAX_PAGE_SIZE

WITHINGS_API_STATUS_OK: Final = 0
WITHINGS_API_STATUS_ERROR: Final = 1
WITHINGS_SUCCESS_STATUS: Final = WITHINGS_API_STATUS_OK

WITHINGS_STATUS_DISABLED: Final = "disabled"
WITHINGS_STATUS_NOT_CONFIGURED: Final = "not_configured"
WITHINGS_STATUS_NOT_CONNECTED: Final = "not_connected"
WITHINGS_STATUS_ACTIVE: Final = "active"
WITHINGS_STATUS_REAUTH_REQUIRED: Final = "reauth_required"
WITHINGS_STATUS_ERROR: Final = "error"
WITHINGS_CONNECTION_STATUSES: Final = frozenset(
    {
        WITHINGS_STATUS_DISABLED,
        WITHINGS_STATUS_NOT_CONFIGURED,
        WITHINGS_STATUS_NOT_CONNECTED,
        WITHINGS_STATUS_ACTIVE,
        WITHINGS_STATUS_REAUTH_REQUIRED,
        WITHINGS_STATUS_ERROR,
    }
)

__all__ = [
    "WITHINGS_ACTIVITY_SOURCE_TYPE",
    "WITHINGS_ACTIVITY_URL",
    "WITHINGS_API_BASE_URL",
    "WITHINGS_API_STATUS_ERROR",
    "WITHINGS_API_STATUS_OK",
    "WITHINGS_AUTHORIZE_URL",
    "WITHINGS_AUTH_URI",
    "WITHINGS_CONNECTION_STATUSES",
    "WITHINGS_MAX_PAGES",
    "WITHINGS_MAX_PAGE_SIZE",
    "WITHINGS_MAX_RECORDS",
    "WITHINGS_MEASURE_TYPES",
    "WITHINGS_MEASURE_URL",
    "WITHINGS_PAGE_SIZE",
    "WITHINGS_REQUIRED_SCOPES",
    "WITHINGS_SCOPES",
    "WITHINGS_STATUS_ACTIVE",
    "WITHINGS_STATUS_DISABLED",
    "WITHINGS_STATUS_ERROR",
    "WITHINGS_STATUS_NOT_CONFIGURED",
    "WITHINGS_STATUS_NOT_CONNECTED",
    "WITHINGS_STATUS_REAUTH_REQUIRED",
    "WITHINGS_SUCCESS_STATUS",
    "WITHINGS_SUPPORTED_MEASURES",
    "WITHINGS_TOKEN_URI",
    "WITHINGS_TOKEN_URL",
    "WITHINGS_WEIGHT_SOURCE_TYPE",
]
