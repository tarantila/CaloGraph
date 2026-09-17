from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from time import monotonic
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.analytics.micronutrient_shadow import (
    MicronutrientPeriodResult,
    resolve_shadow_source_mapping,
)
from app.database import SessionLocal
from app.nutrition.resolution.discovery import (
    NutritionProviderMetadataSet,
    discover_nutrition_provider_metadata,
)

LOGGER = logging.getLogger(__name__)
TELEMETRY_EVENT = "analytics.micronutrients.metadata.shadow"
TELEMETRY_VERSION = "source_metadata.v1"
_MAX_EXCEPTION_CLASS_LENGTH = 64
_MAX_DAYS = 31
_COMPARABLE_CANONICAL_PROVIDERS = frozenset({"yazio", "apple_health"})


class MicronutrientMetadataProviderSetClassification(StrEnum):
    MATCH = "match"
    LEGACY_ONLY = "legacy_only"
    CANONICAL_ONLY = "canonical_only"
    PROVIDER_SET_MISMATCH = "provider_set_mismatch"
    NOT_COMPARABLE = "not_comparable"
    NO_DATA = "no_data"


class MicronutrientMetadataTimestampRelation(StrEnum):
    BOTH_MISSING = "both_missing"
    BOTH_PRESENT_SAME_VALUE = "both_present_same_value"
    BOTH_PRESENT_DIFFERENT_VALUE = "both_present_different_value"
    LEGACY_ONLY = "legacy_only"
    CANONICAL_ONLY = "canonical_only"
    NOT_COMPARABLE = "not_comparable"


@dataclass(frozen=True, slots=True)
class MicronutrientMetadataEvaluation:
    provider_set_classification: MicronutrientMetadataProviderSetClassification
    timestamp_relation: MicronutrientMetadataTimestampRelation
    reason: str | None = None
    exception_class: str | None = None


@dataclass(frozen=True, slots=True)
class MicronutrientMetadataShadowOutcome:
    outcome: str
    provider_set_classification: MicronutrientMetadataProviderSetClassification | None = None
    timestamp_relation: MicronutrientMetadataTimestampRelation | None = None
    reason: str | None = None
    exception_class: str | None = None


def _range_bucket(start: date, end: date) -> str:
    if type(start) is not date or type(end) is not date or start > end:
        return "invalid"
    days = (end - start).days + 1
    if days == 1:
        return "1"
    if days <= 7:
        return "2-7"
    if days <= 31:
        return "8-31"
    return "32+"


def _duration_bucket(duration_seconds: float) -> str:
    milliseconds = duration_seconds * 1_000
    if milliseconds < 10:
        return "<10ms"
    if milliseconds < 50:
        return "10-49ms"
    if milliseconds < 200:
        return "50-199ms"
    return "200ms+"


def _exception_class(exc: BaseException) -> str:
    return type(exc).__name__[:_MAX_EXCEPTION_CLASS_LENGTH]


def _source_context(source: str | None) -> str:
    if source is None:
        return "none"
    if source == "":
        return "empty"
    if source == "yazio_export_v1":
        return "yazio"
    if source == "apple_health_xml":
        return "apple"
    return "unmapped"


def _timestamp_relation(
    legacy_timestamp: datetime | None,
    canonical_timestamp: datetime | None,
) -> MicronutrientMetadataTimestampRelation:
    if legacy_timestamp is None and canonical_timestamp is None:
        return MicronutrientMetadataTimestampRelation.BOTH_MISSING
    if legacy_timestamp is None:
        return MicronutrientMetadataTimestampRelation.CANONICAL_ONLY
    if canonical_timestamp is None:
        return MicronutrientMetadataTimestampRelation.LEGACY_ONLY
    if legacy_timestamp == canonical_timestamp:
        return MicronutrientMetadataTimestampRelation.BOTH_PRESENT_SAME_VALUE
    return MicronutrientMetadataTimestampRelation.BOTH_PRESENT_DIFFERENT_VALUE


def _provider_set_classification(
    legacy_provider_mappings: Mapping[str, str | None],
    canonical_provider_keys: set[str],
) -> MicronutrientMetadataProviderSetClassification:
    legacy_provider_keys = {
        provider_key
        for provider_key in legacy_provider_mappings.values()
        if provider_key is not None
    }
    has_unmappable_legacy = any(
        provider_key is None for provider_key in legacy_provider_mappings.values()
    )
    non_comparable_canonical = canonical_provider_keys - _COMPARABLE_CANONICAL_PROVIDERS
    if has_unmappable_legacy or non_comparable_canonical:
        return MicronutrientMetadataProviderSetClassification.NOT_COMPARABLE
    if not legacy_provider_keys and not canonical_provider_keys:
        return MicronutrientMetadataProviderSetClassification.NO_DATA
    if legacy_provider_keys == canonical_provider_keys:
        return MicronutrientMetadataProviderSetClassification.MATCH
    if legacy_provider_keys and not canonical_provider_keys:
        return MicronutrientMetadataProviderSetClassification.LEGACY_ONLY
    if canonical_provider_keys and not legacy_provider_keys:
        return MicronutrientMetadataProviderSetClassification.CANONICAL_ONLY
    return MicronutrientMetadataProviderSetClassification.PROVIDER_SET_MISMATCH


def compare_micronutrient_metadata(
    legacy: MicronutrientPeriodResult,
    canonical: NutritionProviderMetadataSet,
    *,
    source: str | None,
    legacy_provider_mappings: Mapping[str, str | None],
) -> MicronutrientMetadataEvaluation:
    canonical_by_provider = {item.provider_key: item for item in canonical.providers}
    provider_set_classification = _provider_set_classification(
        legacy_provider_mappings,
        set(canonical_by_provider),
    )
    if not source:
        return MicronutrientMetadataEvaluation(
            provider_set_classification,
            MicronutrientMetadataTimestampRelation.NOT_COMPARABLE,
        )

    provider_key = legacy_provider_mappings.get(source)
    if provider_key is None:
        return MicronutrientMetadataEvaluation(
            provider_set_classification,
            MicronutrientMetadataTimestampRelation.NOT_COMPARABLE,
            reason="source_mapping",
        )
    canonical_timestamp = (
        canonical_by_provider[provider_key].latest_evidence_observed_at
        if provider_key in canonical_by_provider
        else None
    )
    return MicronutrientMetadataEvaluation(
        provider_set_classification,
        _timestamp_relation(legacy.filtered_updated_at, canonical_timestamp),
    )


def _metadata_eligibility(
    *,
    enabled: bool,
    start: date,
    end: date,
    max_days: int,
) -> str | None:
    if not enabled:
        return "disabled"
    if (
        type(start) is not date
        or type(end) is not date
        or start > end
        or max_days < 1
        or max_days > _MAX_DAYS
        or (end - start).days + 1 > max_days
    ):
        return "range_too_long"
    return None


def _emit_telemetry(
    *,
    outcome: MicronutrientMetadataShadowOutcome,
    start: date,
    end: date,
    source: str | None,
    elapsed_seconds: float,
) -> None:
    fields: dict[str, Any] = {
        "event": TELEMETRY_EVENT,
        "version": TELEMETRY_VERSION,
        "outcome": outcome.outcome,
        "range_bucket": _range_bucket(start, end),
        "duration_bucket": _duration_bucket(elapsed_seconds),
        "source_context": _source_context(source),
    }
    if outcome.provider_set_classification is not None:
        fields["provider_set_classification"] = outcome.provider_set_classification.value
    if outcome.timestamp_relation is not None:
        fields["timestamp_relation"] = outcome.timestamp_relation.value
    if outcome.exception_class is not None:
        fields["exception_class"] = outcome.exception_class
    try:
        LOGGER.info(json.dumps(fields, sort_keys=True, separators=(",", ":")), extra=fields)
    except Exception:
        return


def _rollback_safely(session: object) -> None:
    rollback = getattr(session, "rollback", None)
    if callable(rollback):
        try:
            rollback()
        except Exception:
            return


def run_micronutrient_metadata_shadow(
    user_id: UUID,
    start: date,
    end: date,
    source: str | None,
    period: str | None,
    legacy: MicronutrientPeriodResult,
    *,
    enabled: bool,
    max_days: int = _MAX_DAYS,
) -> MicronutrientMetadataShadowOutcome:
    del period
    started = monotonic()
    ineligible_reason = _metadata_eligibility(
        enabled=enabled,
        start=start,
        end=end,
        max_days=max_days,
    )
    if ineligible_reason is not None:
        outcome = MicronutrientMetadataShadowOutcome(ineligible_reason)
        _emit_telemetry(
            outcome=outcome,
            start=start,
            end=end,
            source=source,
            elapsed_seconds=monotonic() - started,
        )
        return outcome

    session: Session | None = None
    try:
        with SessionLocal() as session:
            mappings: dict[str, str | None] = {}
            for source_type, _ in legacy.available_sources:
                mapping = resolve_shadow_source_mapping(
                    session,
                    user_id=user_id,
                    source=source_type,
                )
                mappings[source_type] = mapping.provider_key if mapping is not None else None
            canonical = discover_nutrition_provider_metadata(
                session,
                user_id=user_id,
                start=start,
                end=end,
            )
            evaluation = compare_micronutrient_metadata(
                legacy,
                canonical,
                source=source,
                legacy_provider_mappings=mappings,
            )
    except Exception as exc:
        if session is not None:
            _rollback_safely(session)
        outcome = MicronutrientMetadataShadowOutcome(
            "error",
            exception_class=_exception_class(exc),
        )
    else:
        outcome = MicronutrientMetadataShadowOutcome(
            "evaluated",
            provider_set_classification=evaluation.provider_set_classification,
            timestamp_relation=evaluation.timestamp_relation,
            reason=evaluation.reason,
        )

    _emit_telemetry(
        outcome=outcome,
        start=start,
        end=end,
        source=source,
        elapsed_seconds=monotonic() - started,
    )
    return outcome


__all__ = [
    "MicronutrientMetadataEvaluation",
    "MicronutrientMetadataProviderSetClassification",
    "MicronutrientMetadataShadowOutcome",
    "MicronutrientMetadataTimestampRelation",
    "compare_micronutrient_metadata",
    "run_micronutrient_metadata_shadow",
]
