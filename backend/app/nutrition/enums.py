from enum import StrEnum


class ObservationKind(StrEnum):
    CONSUMPTION_EVENT = "consumption_event"
    PRODUCT = "product"
    SIMPLE_PRODUCT = "simple_product"
    PRODUCT_PROFILE = "product_profile"
    DAILY_SUMMARY = "daily_summary"
    RECIPE = "recipe"
    RECIPE_PORTION = "recipe_portion"
    TOMBSTONE = "tombstone"
    UNKNOWN = "unknown"


class ConsumptionEventKind(StrEnum):
    PRODUCT = "product"
    SIMPLE_PRODUCT = "simple_product"
    RECIPE_PORTION = "recipe_portion"
    UNKNOWN = "unknown"


class ServingScope(StrEnum):
    EVENT = "event"
    PROFILE = "profile"


class PresenceState(StrEnum):
    SUPPLIED = "supplied"
    EXPLICIT_ZERO = "explicit_zero"
    MISSING = "missing"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class CoverageState(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class ResolutionState(StrEnum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    CONFLICT = "conflict"
    DUPLICATE_CANDIDATE = "duplicate_candidate"


class LineageState(StrEnum):
    CONFIRMED = "confirmed"
    UNCERTAIN = "uncertain"
    UNKNOWN = "unknown"

class ProjectionStatus(StrEnum):
    READY = "ready"
    FAILED = "failed"


class ProjectionGranularity(StrEnum):
    EVENT = "event"
    SUMMARY = "summary"
    PARTIAL_EVENT = "partial_event"


class ProjectionLineageRole(StrEnum):
    SELECTED = "selected"
    FALLBACK = "fallback"
    REJECTED = "rejected"
    DIAGNOSTIC = "diagnostic"


PROJECTION_STATUS_VALUES = tuple(item.value for item in ProjectionStatus)
PROJECTION_GRANULARITY_VALUES = tuple(item.value for item in ProjectionGranularity)
PROJECTION_LINEAGE_ROLE_VALUES = tuple(item.value for item in ProjectionLineageRole)



class ObservationRole(StrEnum):
    PROVIDER = "provider"
    CANONICAL = "canonical"
    DERIVED = "derived"



RUN_STATUS_VALUES = ("pending", "running", "completed", "partial", "failed")
PRESENCE_VALUES = tuple(item.value for item in PresenceState)
COVERAGE_VALUES = tuple(item.value for item in CoverageState)
RESOLUTION_VALUES = tuple(item.value for item in ResolutionState)
LINEAGE_VALUES = tuple(item.value for item in LineageState)
OBSERVATION_KIND_VALUES = tuple(item.value for item in ObservationKind)
CONSUMPTION_EVENT_KIND_VALUES = tuple(item.value for item in ConsumptionEventKind)
SERVING_SCOPE_VALUES = tuple(item.value for item in ServingScope)
OBSERVATION_ROLE_VALUES = tuple(item.value for item in ObservationRole)
