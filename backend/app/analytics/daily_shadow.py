from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from time import monotonic
from typing import Any
from uuid import UUID

from app.analytics.daily_point_parity import (
    DailyPointParityClassification,
    DailyPointRangeParity,
    compare_daily_point_range,
)
from app.database import SessionLocal

LOGGER = logging.getLogger(__name__)
TELEMETRY_EVENT = "analytics.daily.shadow"
TELEMETRY_VERSION = "d4a.v1"
_MAX_EXCEPTION_CLASS_LENGTH = 64


class DailyShadowState(StrEnum):
    DISABLED = "disabled"
    SKIPPED = "skipped"
    MATCH = "match"
    EXPECTED_DIFFERENCE = "expected_difference"
    UNEXPLAINED_MISMATCH = "unexplained_mismatch"
    NOT_COMPARABLE = "not_comparable"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class DailyShadowOutcome:
    state: DailyShadowState
    reason: str | None = None
    exception_class: str | None = None
    range_bucket: str | None = None
    duration_bucket: str | None = None


def check_daily_shadow_eligibility(
    enabled: bool,
    source: str | None,
    tracking: str | None,
    weekday: int | None,
    start: date,
    end: date,
    max_days: int,
) -> DailyShadowOutcome:
    """Return a request-independent eligibility outcome without database access."""
    if not enabled:
        return DailyShadowOutcome(DailyShadowState.DISABLED, reason="disabled")
    if source is not None:
        return DailyShadowOutcome(DailyShadowState.SKIPPED, reason="source_filter")
    if tracking is not None:
        return DailyShadowOutcome(DailyShadowState.SKIPPED, reason="tracking_filter")
    if weekday is not None:
        return DailyShadowOutcome(DailyShadowState.SKIPPED, reason="weekday_filter")
    if (
        not isinstance(start, date)
        or isinstance(start, datetime)
        or not isinstance(end, date)
        or isinstance(end, datetime)
        or start > end
    ):
        return DailyShadowOutcome(DailyShadowState.SKIPPED, reason="invalid_range")

    requested_days = (end - start).days + 1
    if max_days < 1 or max_days > 366 or requested_days > max_days:
        return DailyShadowOutcome(DailyShadowState.SKIPPED, reason="range_too_large")
    return DailyShadowOutcome(DailyShadowState.MATCH)


def is_daily_shadow_eligible(
    enabled: bool,
    source: str | None,
    tracking: str | None,
    weekday: int | None,
    start: date,
    end: date,
    max_days: int,
) -> bool:
    return (
        check_daily_shadow_eligibility(
            enabled=enabled,
            source=source,
            tracking=tracking,
            weekday=weekday,
            start=start,
            end=end,
            max_days=max_days,
        ).state
        is DailyShadowState.MATCH
    )


def collapse_daily_point_range(parity: DailyPointRangeParity) -> DailyShadowOutcome:
    """Collapse immutable D3B day results, preserving day-level precedence."""
    has_not_comparable = False
    has_unexplained_mismatch = False
    has_expected_difference = False
    for day in parity.days:
        classification = day.classification
        if not day.comparable or classification == DailyPointParityClassification.NOT_COMPARABLE:
            has_not_comparable = True
        elif classification == DailyPointParityClassification.UNEXPLAINED_MISMATCH:
            has_unexplained_mismatch = True
        elif classification != DailyPointParityClassification.MATCH:
            has_expected_difference = True
    if has_not_comparable:
        return DailyShadowOutcome(DailyShadowState.NOT_COMPARABLE)
    if has_unexplained_mismatch:
        return DailyShadowOutcome(DailyShadowState.UNEXPLAINED_MISMATCH)
    if has_expected_difference:
        return DailyShadowOutcome(DailyShadowState.EXPECTED_DIFFERENCE)
    return DailyShadowOutcome(DailyShadowState.MATCH)


def _range_bucket(start: date, end: date) -> str:
    if (
        not isinstance(start, date)
        or isinstance(start, datetime)
        or not isinstance(end, date)
        or isinstance(end, datetime)
        or start > end
    ):
        return "invalid"
    days = (end - start).days + 1
    if days == 1:
        return "1"
    if days <= 7:
        return "2-7"
    if days <= 31:
        return "8-31"
    if days <= 366:
        return "32-366"
    return "367+"


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


def _emit_telemetry(
    *,
    outcome: DailyShadowOutcome,
    start: date,
    end: date,
    elapsed_seconds: float,
    exception_class: str | None = None,
) -> None:
    """Emit only fixed and bounded fields; never attach exception details or context."""
    fields: dict[str, Any] = {
        "event": TELEMETRY_EVENT,
        "version": TELEMETRY_VERSION,
        "outcome": outcome.state.value,
        "range_bucket": _range_bucket(start, end),
        "duration_bucket": _duration_bucket(elapsed_seconds),
    }
    if exception_class is not None:
        fields["exception_class"] = exception_class
    try:
        LOGGER.info(json.dumps(fields, sort_keys=True, separators=(",", ":")), extra=fields)
    except Exception:
        # Telemetry must not change the Legacy request or shadow result.
        return


def _rollback_safely(session: object) -> None:
    rollback = getattr(session, "rollback", None)
    if callable(rollback):
        try:
            rollback()
        except Exception:
            return


def run_daily_shadow(
    user_id: UUID,
    start: date,
    end: date,
    source: str | None,
    tracking: str | None,
    weekday: int | None,
    *,
    enabled: bool,
    max_days: int,
) -> DailyShadowOutcome:
    """Run one bounded, read-only D3B comparison after eligibility passes."""
    eligibility = check_daily_shadow_eligibility(
        enabled=enabled,
        source=source,
        tracking=tracking,
        weekday=weekday,
        start=start,
        end=end,
        max_days=max_days,
    )
    if eligibility.state is not DailyShadowState.MATCH:
        return eligibility

    started = monotonic()
    session: object | None = None
    try:
        session = SessionLocal()
        with session as shadow_session:
            try:
                parity = compare_daily_point_range(
                    shadow_session,
                    user_id=user_id,
                    start=start,
                    end=end,
                    max_days=max_days,
                )
                outcome = collapse_daily_point_range(parity)
            except Exception as exc:
                _rollback_safely(shadow_session)
                outcome = DailyShadowOutcome(
                    DailyShadowState.ERROR,
                    exception_class=_exception_class(exc),
                )
        _emit_telemetry(
            outcome=outcome,
            start=start,
            end=end,
            elapsed_seconds=monotonic() - started,
            exception_class=outcome.exception_class,
        )
        return outcome
    except Exception as exc:
        if session is not None:
            _rollback_safely(session)
        outcome = DailyShadowOutcome(
            DailyShadowState.ERROR,
            exception_class=_exception_class(exc),
        )
        _emit_telemetry(
            outcome=outcome,
            start=start,
            end=end,
            elapsed_seconds=monotonic() - started,
            exception_class=outcome.exception_class,
        )
        return outcome


__all__ = [
    "TELEMETRY_EVENT",
    "TELEMETRY_VERSION",
    "DailyShadowOutcome",
    "DailyShadowState",
    "check_daily_shadow_eligibility",
    "collapse_daily_point_range",
    "is_daily_shadow_eligible",
    "run_daily_shadow",
]
