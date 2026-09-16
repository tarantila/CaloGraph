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
    serve_canonical,
)
from app.analytics.daily_canonical import (
    DailyCanonicalOutcome,
    DailyCanonicalReason,
    DailyCanonicalState,
    run_daily_canonical_read,
)
from app.schemas import DailyPoint

LOGGER = logging.getLogger(__name__)
TELEMETRY_EVENT = "analytics.calendar.canonical"
TELEMETRY_VERSION = "d4c.v1"
MAX_CANONICAL_READ_DAYS = 31


class CalendarCanonicalTelemetryOutcome(StrEnum):
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
            CalendarCanonicalTelemetryOutcome.CANONICAL_SERVED
            if outcome.points is not None
            else CalendarCanonicalTelemetryOutcome.CANONICAL_MATCH
        )
    if outcome.state in {DailyCanonicalState.DISABLED, DailyCanonicalState.SKIPPED}:
        return CalendarCanonicalTelemetryOutcome.LEGACY_INELIGIBLE
    if outcome.state in {
        DailyCanonicalState.EXPECTED_DIFFERENCE,
        DailyCanonicalState.UNEXPLAINED_MISMATCH,
    }:
        return CalendarCanonicalTelemetryOutcome.FALLBACK_PARITY
    if outcome.state is DailyCanonicalState.NOT_COMPARABLE:
        return CalendarCanonicalTelemetryOutcome.FALLBACK_NOT_READY
    if outcome.state is DailyCanonicalState.FALLBACK:
        if outcome.reason is DailyCanonicalReason.PROJECTION_NO_PRIMARY_VALUES:
            return CalendarCanonicalTelemetryOutcome.FALLBACK_NOT_READY
        if outcome.reason is DailyCanonicalReason.LEGACY_SNAPSHOT_CHANGED:
            return CalendarCanonicalTelemetryOutcome.FALLBACK_PARITY
        return CalendarCanonicalTelemetryOutcome.FALLBACK_SEAL
    return CalendarCanonicalTelemetryOutcome.ERROR


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


def run_calendar_canonical_read(
    user_id: UUID,
    start: date,
    end: date,
    *,
    legacy_points: Sequence[DailyPoint] | None = None,
) -> DailyCanonicalOutcome:
    started = monotonic()
    if legacy_points is not None:
        requested_days = (end - start).days + 1
        request = CanonicalServingRequest(
            user_id=user_id,
            start=start,
            end=end,
            legacy_points=tuple(legacy_points),
            enabled=True,
            endpoint=CanonicalServingEndpoint.CALENDAR,
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
            result = serve_canonical(request)
            detail = getattr(result, "detail", None)
            if result.outcome is CanonicalServingOutcome.FALLBACK_ERROR:
                outcome = DailyCanonicalOutcome(
                    DailyCanonicalState.ERROR,
                    exception_class=result.exception_class,
                )
            elif result.outcome is CanonicalServingOutcome.CANONICAL_SERVED:
                outcome = DailyCanonicalOutcome(
                    DailyCanonicalState.MATCH,
                    points=result.selected_points,
                )
            elif result.outcome is CanonicalServingOutcome.LEGACY_INELIGIBLE:
                outcome = DailyCanonicalOutcome(
                    DailyCanonicalState.SKIPPED,
                    reason=DailyCanonicalReason.RANGE_TOO_LARGE,
                )
            elif detail is CanonicalServingDetail.PROJECTION_NO_PRIMARY_VALUES:
                outcome = DailyCanonicalOutcome(
                    DailyCanonicalState.FALLBACK,
                    reason=DailyCanonicalReason.PROJECTION_NO_PRIMARY_VALUES,
                )
            elif detail is CanonicalServingDetail.NOT_COMPARABLE:
                outcome = DailyCanonicalOutcome(
                    DailyCanonicalState.NOT_COMPARABLE,
                    reason=DailyCanonicalReason.NOT_COMPARABLE,
                )
            elif (
                detail is CanonicalServingDetail.PROJECTION_NOT_READY
                or result.outcome is CanonicalServingOutcome.FALLBACK_NOT_READY
            ):
                outcome = DailyCanonicalOutcome(
                    DailyCanonicalState.NOT_COMPARABLE,
                    reason=DailyCanonicalReason.PROJECTION_NOT_READY,
                )
            elif detail is CanonicalServingDetail.EXPECTED_DIFFERENCE:
                outcome = DailyCanonicalOutcome(
                    DailyCanonicalState.EXPECTED_DIFFERENCE,
                    reason=DailyCanonicalReason.EXPECTED_DIFFERENCE,
                )
            elif detail is CanonicalServingDetail.UNEXPLAINED_MISMATCH:
                outcome = DailyCanonicalOutcome(
                    DailyCanonicalState.UNEXPLAINED_MISMATCH,
                    reason=DailyCanonicalReason.UNEXPLAINED_MISMATCH,
                )
            elif (
                detail is CanonicalServingDetail.LEGACY_SNAPSHOT_CHANGED
                or result.outcome is CanonicalServingOutcome.FALLBACK_PARITY
            ):
                outcome = DailyCanonicalOutcome(
                    DailyCanonicalState.FALLBACK,
                    reason=DailyCanonicalReason.LEGACY_SNAPSHOT_CHANGED,
                )
            elif (
                detail is CanonicalServingDetail.SOURCE_PRIORITY_POLICY_INVALID
                or result.outcome is CanonicalServingOutcome.FALLBACK_SEAL
            ):
                outcome = DailyCanonicalOutcome(
                    DailyCanonicalState.FALLBACK,
                    reason=DailyCanonicalReason.SOURCE_PRIORITY_POLICY_INVALID,
                )
            else:
                outcome = DailyCanonicalOutcome(
                    DailyCanonicalState.ERROR,
                    exception_class=getattr(result, "exception_class", None),
                )
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

    try:
        outcome = run_daily_canonical_read(
            user_id,
            start,
            end,
            None,
            None,
            None,
            enabled=True,
            max_days=MAX_CANONICAL_READ_DAYS,
            emit_telemetry=False,
        )
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
    "CalendarCanonicalTelemetryOutcome",
    "run_calendar_canonical_read",
]
