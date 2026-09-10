from enum import StrEnum


class EvidenceKind(StrEnum):
    CONSUMPTION_EVENT = "event"
    EVENT = CONSUMPTION_EVENT
    SOURCE_OBSERVATION = "summary"
    SUMMARY = SOURCE_OBSERVATION
    FIELD_OBSERVATION = "field"
    FIELD = FIELD_OBSERVATION
    UNKNOWN = "unknown"


class ReasonCode(StrEnum):
    EVENT_COMPLETE_CONFIRMED = "event_complete_confirmed"
    EVENT_COMPLETE_UNCERTAIN_NO_SUMMARY = "event_complete_uncertain_no_summary"
    SUMMARY_ONLY = "summary_only"
    SUMMARY_FALLBACK_UNCERTAIN_LINEAGE = "summary_fallback_uncertain_lineage"
    SUMMARY_FALLBACK_PARTIAL_EVENTS = "summary_fallback_partial_events"
    SUMMARY_FALLBACK_EVENT_CONFLICT = "summary_fallback_event_conflict"
    SUMMARY_FALLBACK_DUPLICATE_CANDIDATE = "summary_fallback_duplicate_candidate"
    PARTIAL_EVENTS_NO_SUMMARY = "partial_events_no_summary"
    SUMMARY_UNUSABLE = "summary_unusable"
    ALL_SOURCES_MISSING = "all_sources_missing"
    PROVIDER_NOT_AVAILABLE = "provider_not_available"
    UNSUPPORTED_METRIC = "unsupported_metric"
    INVALID_UNIT = "invalid_unit"
    METRIC_UNIT_MISMATCH = "metric_unit_mismatch"
    MISSING_AMOUNT = "missing_amount"
    UNRESOLVED_PRODUCT = "unresolved_product"
