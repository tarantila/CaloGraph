from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import date
from enum import StrEnum
from time import monotonic
from typing import Any
from uuid import UUID

from app.analytics.daily_canonical import (
    DailyCanonicalOutcome,
    DailyCanonicalState,
    _duration_bucket,
    _exception_class,
    _range_bucket,
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
        if outcome.reason == "projection_no_primary_values":
            return CalendarCanonicalTelemetryOutcome.FALLBACK_NOT_READY
        if outcome.reason == "legacy_snapshot_changed":
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
        "range_bucket": _range_bucket(start, end),
        "duration_bucket": _duration_bucket(elapsed_seconds),
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
            exception_class=_exception_class(exc),
        )
    if outcome.points is not None and legacy_points is not None and list(outcome.points) != list(
        legacy_points
    ):
        outcome = DailyCanonicalOutcome(
            DailyCanonicalState.FALLBACK,
            reason="legacy_snapshot_changed",
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
