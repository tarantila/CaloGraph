from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import date
from enum import StrEnum
from time import monotonic
from typing import Any
from uuid import UUID

from app.analytics import canonical_telemetry
from app.analytics.canonical_serving import (
    CanonicalServingDetail,
    CanonicalServingEligibility,
    CanonicalServingEligibilityReason,
    CanonicalServingEndpoint,
    CanonicalServingOutcome,
    CanonicalServingRequest,
    CanonicalServingResult,
    serve_canonical,
)
from app.analytics.daily_canonical import (
    DailyCanonicalOutcome,
    DailyCanonicalReason,
    DailyCanonicalState,
)
from app.schemas import DailyPoint

LOGGER = logging.getLogger(__name__)
TELEMETRY_EVENT = "analytics.weekdays.canonical"
TELEMETRY_VERSION = "d4f.v1"
MAX_CANONICAL_READ_DAYS = 31


class WeekdaysCanonicalTelemetryOutcome(StrEnum):
    LEGACY_INELIGIBLE = "legacy_ineligible"
    CANONICAL_MATCH = "canonical_match"
    CANONICAL_SERVED = "canonical_served"
    FALLBACK_PARITY = "fallback_parity"
    FALLBACK_NOT_READY = "fallback_not_ready"
    FALLBACK_SEAL = "fallback_seal"
    ERROR = "error"


def _telemetry_outcome(outcome: DailyCanonicalOutcome) -> str:
    if outcome.state is DailyCanonicalState.MATCH:
        return (
            WeekdaysCanonicalTelemetryOutcome.CANONICAL_SERVED
            if outcome.points is not None
            else WeekdaysCanonicalTelemetryOutcome.CANONICAL_MATCH
        )
    if outcome.state is DailyCanonicalState.SKIPPED:
        return WeekdaysCanonicalTelemetryOutcome.LEGACY_INELIGIBLE
    if outcome.state in {
        DailyCanonicalState.EXPECTED_DIFFERENCE,
        DailyCanonicalState.UNEXPLAINED_MISMATCH,
    }:
        return WeekdaysCanonicalTelemetryOutcome.FALLBACK_PARITY
    if outcome.state is DailyCanonicalState.NOT_COMPARABLE:
        return WeekdaysCanonicalTelemetryOutcome.FALLBACK_NOT_READY
    if outcome.state is DailyCanonicalState.FALLBACK:
        if outcome.reason in {
            DailyCanonicalReason.PROJECTION_NOT_READY,
            DailyCanonicalReason.PROJECTION_NO_PRIMARY_VALUES,
        }:
            return WeekdaysCanonicalTelemetryOutcome.FALLBACK_NOT_READY
        if outcome.reason is DailyCanonicalReason.LEGACY_SNAPSHOT_CHANGED:
            return WeekdaysCanonicalTelemetryOutcome.FALLBACK_PARITY
        return WeekdaysCanonicalTelemetryOutcome.FALLBACK_SEAL
    return WeekdaysCanonicalTelemetryOutcome.ERROR


def _emit_telemetry(
    *,
    outcome: DailyCanonicalOutcome,
    start: date,
    end: date,
    elapsed_seconds: float,
) -> None:
    fields: dict[str, Any] = {
        "event": TELEMETRY_EVENT,
        "version": TELEMETRY_VERSION,
        "outcome": _telemetry_outcome(outcome),
        "range_bucket": canonical_telemetry._range_bucket(start, end),
        "duration_bucket": canonical_telemetry._duration_bucket(elapsed_seconds),
    }
    if outcome.exception_class is not None:
        fields["exception_class"] = outcome.exception_class
    try:
        LOGGER.info(json.dumps(fields, sort_keys=True, separators=(",", ":")), extra=fields)
    except Exception:
        return


def _outcome_from_result(result: CanonicalServingResult) -> DailyCanonicalOutcome:
    detail = result.detail
    if result.outcome is CanonicalServingOutcome.FALLBACK_ERROR:
        return DailyCanonicalOutcome(
            DailyCanonicalState.ERROR,
            exception_class=result.exception_class,
        )
    if result.outcome is CanonicalServingOutcome.CANONICAL_SERVED:
        return DailyCanonicalOutcome(
            DailyCanonicalState.MATCH,
            points=result.selected_points,
        )
    if result.outcome is CanonicalServingOutcome.LEGACY_INELIGIBLE:
        return DailyCanonicalOutcome(
            DailyCanonicalState.SKIPPED,
            reason=DailyCanonicalReason.RANGE_TOO_LARGE,
        )
    if detail is CanonicalServingDetail.PROJECTION_NO_PRIMARY_VALUES:
        return DailyCanonicalOutcome(
            DailyCanonicalState.FALLBACK,
            reason=DailyCanonicalReason.PROJECTION_NO_PRIMARY_VALUES,
        )
    if detail is CanonicalServingDetail.NOT_COMPARABLE:
        return DailyCanonicalOutcome(
            DailyCanonicalState.NOT_COMPARABLE,
            reason=DailyCanonicalReason.NOT_COMPARABLE,
        )
    if (
        detail is CanonicalServingDetail.PROJECTION_NOT_READY
        or result.outcome is CanonicalServingOutcome.FALLBACK_NOT_READY
    ):
        return DailyCanonicalOutcome(
            DailyCanonicalState.NOT_COMPARABLE,
            reason=DailyCanonicalReason.PROJECTION_NOT_READY,
        )
    if detail is CanonicalServingDetail.EXPECTED_DIFFERENCE:
        return DailyCanonicalOutcome(
            DailyCanonicalState.EXPECTED_DIFFERENCE,
            reason=DailyCanonicalReason.EXPECTED_DIFFERENCE,
        )
    if detail is CanonicalServingDetail.UNEXPLAINED_MISMATCH:
        return DailyCanonicalOutcome(
            DailyCanonicalState.UNEXPLAINED_MISMATCH,
            reason=DailyCanonicalReason.UNEXPLAINED_MISMATCH,
        )
    if (
        detail is CanonicalServingDetail.LEGACY_SNAPSHOT_CHANGED
        or result.outcome is CanonicalServingOutcome.FALLBACK_PARITY
    ):
        return DailyCanonicalOutcome(
            DailyCanonicalState.FALLBACK,
            reason=DailyCanonicalReason.LEGACY_SNAPSHOT_CHANGED,
        )
    return DailyCanonicalOutcome(
        DailyCanonicalState.FALLBACK,
        reason=DailyCanonicalReason.SOURCE_PRIORITY_POLICY_INVALID,
    )


def run_weekdays_canonical_read(
    user_id: UUID,
    start: date,
    end: date,
    *,
    legacy_points: Sequence[DailyPoint],
) -> DailyCanonicalOutcome:
    started = monotonic()
    requested_days = (end - start).days + 1
    request = CanonicalServingRequest(
        user_id=user_id,
        start=start,
        end=end,
        legacy_points=tuple(legacy_points),
        enabled=True,
        endpoint=CanonicalServingEndpoint.WEEKDAYS,
        eligibility=CanonicalServingEligibility(
            eligible=requested_days <= MAX_CANONICAL_READ_DAYS,
            reason=(
                CanonicalServingEligibilityReason.RANGE_TOO_LARGE
                if requested_days > MAX_CANONICAL_READ_DAYS
                else None
            ),
        ),
    )
    try:
        outcome = _outcome_from_result(serve_canonical(request))
    except Exception as exc:
        outcome = DailyCanonicalOutcome(
            DailyCanonicalState.ERROR,
            exception_class=canonical_telemetry._exception_class(exc),
        )
    _emit_telemetry(
        outcome=outcome,
        start=start,
        end=end,
        elapsed_seconds=monotonic() - started,
    )
    return outcome


__all__ = [
    "MAX_CANONICAL_READ_DAYS",
    "TELEMETRY_EVENT",
    "TELEMETRY_VERSION",
    "WeekdaysCanonicalTelemetryOutcome",
    "run_weekdays_canonical_read",
]
