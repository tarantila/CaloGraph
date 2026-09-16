from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from uuid import UUID

from sqlalchemy.orm import Session

from app.analytics import canonical_telemetry
from app.analytics.daily_canonical import (
    DailyCanonicalOutcome,
    DailyCanonicalReason,
    DailyCanonicalState,
    collapse_daily_canonical_range,
)
from app.analytics.daily_point_parity import (
    DailyPointRangeParity,
    compare_daily_point_range,
)
from app.database import SessionLocal
from app.schemas import DailyPoint

MAX_CANONICAL_SERVING_DAYS = 31


class CanonicalServingSource(StrEnum):
    LEGACY_SELECTED = "legacy_selected"
    CANONICAL_SELECTED = "canonical_selected"


class CanonicalServingOutcome(StrEnum):
    LEGACY_INELIGIBLE = "legacy_ineligible"
    FALLBACK_NOT_READY = "fallback_not_ready"
    FALLBACK_PARITY = "fallback_parity"
    FALLBACK_SEAL = "fallback_seal"
    FALLBACK_ERROR = "fallback_error"
    CANONICAL_SERVED = "canonical_served"


class CanonicalServingDetail(StrEnum):
    NOT_COMPARABLE = "not_comparable"
    PROJECTION_NOT_READY = "projection_not_ready"
    PROJECTION_NO_PRIMARY_VALUES = "projection_no_primary_values"
    EXPECTED_DIFFERENCE = "expected_difference"
    UNEXPLAINED_MISMATCH = "unexplained_mismatch"
    LEGACY_SNAPSHOT_CHANGED = "legacy_snapshot_changed"
    SOURCE_PRIORITY_POLICY_INVALID = "source_priority_policy_invalid"
    ERROR = "error"


class CanonicalServingEligibilityReason(StrEnum):
    DISABLED = "disabled"
    PERIOD = "period"
    SOURCE_FILTER = "source_filter"
    TRACKING_FILTER = "tracking_filter"
    WEEKDAY_FILTER = "weekday_filter"
    INVALID_RANGE = "invalid_range"
    RANGE_TOO_LARGE = "range_too_large"
    ENDPOINT_FILTER = "endpoint_filter"


class CanonicalServingEndpoint(StrEnum):
    DAILY = "daily"
    CALENDAR = "calendar"
    WEEKLY = "weekly"
    WEEKDAYS = "weekdays"

@dataclass(frozen=True, slots=True)
class CanonicalServingEligibility:
    eligible: bool
    reason: CanonicalServingEligibilityReason | None = None

    def __post_init__(self) -> None:
        if type(self.eligible) is not bool:
            raise TypeError("eligible must be bool")
        if self.reason is not None and not isinstance(
            self.reason, CanonicalServingEligibilityReason
        ):
            raise TypeError("reason must be CanonicalServingEligibilityReason or None")

    @property
    def is_eligible(self) -> bool:
        return self.eligible


@dataclass(frozen=True, slots=True)
class CanonicalServingRequest:
    user_id: UUID
    start: date
    end: date
    legacy_points: tuple[DailyPoint, ...]
    enabled: bool
    endpoint: CanonicalServingEndpoint
    eligibility: CanonicalServingEligibility

    def __post_init__(self) -> None:
        if not isinstance(self.user_id, UUID):
            raise TypeError("user_id must be UUID")
        if not isinstance(self.start, date) or isinstance(self.start, datetime):
            raise TypeError("start must be a date")
        if not isinstance(self.end, date) or isinstance(self.end, datetime):
            raise TypeError("end must be a date")
        if self.start > self.end:
            raise ValueError("start must not be after end")
        if type(self.enabled) is not bool:
            raise TypeError("enabled must be bool")
        if not isinstance(self.endpoint, CanonicalServingEndpoint):
            raise TypeError("endpoint must be CanonicalServingEndpoint")
        if not isinstance(self.eligibility, CanonicalServingEligibility):
            raise TypeError("eligibility must be CanonicalServingEligibility")
        points = tuple(self.legacy_points)
        if any(not isinstance(point, DailyPoint) for point in points):
            raise TypeError("legacy_points must contain DailyPoint values")
        object.__setattr__(self, "legacy_points", points)


@dataclass(frozen=True, slots=True)
class CanonicalServingResult:
    selected_points: tuple[DailyPoint, ...]
    source: CanonicalServingSource
    outcome: CanonicalServingOutcome
    detail: CanonicalServingDetail | None = None
    exception_class: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "selected_points", tuple(self.selected_points))
        if any(not isinstance(point, DailyPoint) for point in self.selected_points):
            raise TypeError("selected_points must contain DailyPoint values")
        if not isinstance(self.source, CanonicalServingSource):
            raise TypeError("source must be CanonicalServingSource")
        if not isinstance(self.outcome, CanonicalServingOutcome):
            raise TypeError("outcome must be CanonicalServingOutcome")
        if self.detail is not None and not isinstance(self.detail, CanonicalServingDetail):
            raise TypeError("detail must be CanonicalServingDetail or None")
        if self.exception_class is not None:
            if type(self.exception_class) is not str:
                raise TypeError("exception_class must be str or None")
            if not 1 <= len(self.exception_class) <= 64:
                raise ValueError("exception_class must contain 1 to 64 characters")

# Explicit alias names keep the internal contract discoverable without introducing
# a second envelope implementation.
CanonicalServingContext = CanonicalServingRequest
CanonicalServingResponse = CanonicalServingResult


def _legacy_result(
    request: CanonicalServingRequest,
    outcome: CanonicalServingOutcome,
    *,
    detail: CanonicalServingDetail | None = None,
    exception_class: str | None = None,
) -> CanonicalServingResult:
    return CanonicalServingResult(
        selected_points=request.legacy_points,
        source=CanonicalServingSource.LEGACY_SELECTED,
        outcome=outcome,
        detail=detail,
        exception_class=exception_class,
    )


def _daily_point_snapshot_equal(
    legacy_points: tuple[DailyPoint, ...],
    canonical_points: tuple[DailyPoint, ...],
    *,
    start: date,
    end: date,
) -> bool:
    requested_dates = tuple(
        start + timedelta(days=offset)
        for offset in range((end - start).days + 1)
    )
    if len(legacy_points) != len(requested_dates) or len(canonical_points) != len(requested_dates):
        return False
    if tuple(point.date for point in legacy_points) != requested_dates:
        return False
    if tuple(point.date for point in canonical_points) != requested_dates:
        return False
    # DailyPoint.model_dump compares every serving-relevant field, including
    # tracking reasons and None/Decimal values, without mutating either object.
    return all(
        legacy.model_dump(mode="python") == canonical.model_dump(mode="python")
        for legacy, canonical in zip(legacy_points, canonical_points, strict=True)
    )


def _outcome_for_daily_canonical(outcome: DailyCanonicalOutcome) -> CanonicalServingOutcome:
    if outcome.state is DailyCanonicalState.MATCH and outcome.points is not None:
        return CanonicalServingOutcome.CANONICAL_SERVED
    if outcome.state in {DailyCanonicalState.DISABLED, DailyCanonicalState.SKIPPED}:
        return CanonicalServingOutcome.LEGACY_INELIGIBLE
    if outcome.state is DailyCanonicalState.NOT_COMPARABLE:
        return CanonicalServingOutcome.FALLBACK_NOT_READY
    if outcome.state in {
        DailyCanonicalState.EXPECTED_DIFFERENCE,
        DailyCanonicalState.UNEXPLAINED_MISMATCH,
    }:
        return CanonicalServingOutcome.FALLBACK_PARITY
    if outcome.state is DailyCanonicalState.FALLBACK:
        if outcome.reason == DailyCanonicalReason.PROJECTION_NO_PRIMARY_VALUES:
            return CanonicalServingOutcome.FALLBACK_NOT_READY
        if outcome.reason == DailyCanonicalReason.LEGACY_SNAPSHOT_CHANGED:
            return CanonicalServingOutcome.FALLBACK_PARITY
        return CanonicalServingOutcome.FALLBACK_SEAL
    return CanonicalServingOutcome.FALLBACK_ERROR


def _detail_for_daily_canonical(outcome: DailyCanonicalOutcome) -> CanonicalServingDetail:
    if outcome.state is DailyCanonicalState.MATCH and outcome.points is None:
        return CanonicalServingDetail.PROJECTION_NOT_READY
    if outcome.state is DailyCanonicalState.NOT_COMPARABLE:
        if outcome.reason == DailyCanonicalReason.PROJECTION_NOT_READY:
            return CanonicalServingDetail.PROJECTION_NOT_READY
        return CanonicalServingDetail.NOT_COMPARABLE
    if outcome.state is DailyCanonicalState.EXPECTED_DIFFERENCE:
        return CanonicalServingDetail.EXPECTED_DIFFERENCE
    if outcome.state is DailyCanonicalState.UNEXPLAINED_MISMATCH:
        return CanonicalServingDetail.UNEXPLAINED_MISMATCH
    if outcome.state is DailyCanonicalState.FALLBACK:
        if outcome.reason == DailyCanonicalReason.PROJECTION_NO_PRIMARY_VALUES:
            return CanonicalServingDetail.PROJECTION_NO_PRIMARY_VALUES
        if outcome.reason == DailyCanonicalReason.LEGACY_SNAPSHOT_CHANGED:
            return CanonicalServingDetail.LEGACY_SNAPSHOT_CHANGED
        return CanonicalServingDetail.SOURCE_PRIORITY_POLICY_INVALID
    if outcome.state is DailyCanonicalState.ERROR:
        return CanonicalServingDetail.ERROR
    return CanonicalServingDetail.SOURCE_PRIORITY_POLICY_INVALID


def run_canonical_parity(
    db: Session,
    *,
    user_id: UUID,
    start: date,
    end: date,
    legacy_points: tuple[DailyPoint, ...],
) -> DailyPointRangeParity:
    """Run D3B parity using the already captured request Legacy snapshot."""
    return compare_daily_point_range(
        db,
        user_id=user_id,
        start=start,
        end=end,
        max_days=MAX_CANONICAL_SERVING_DAYS,
        include_canonical_policy_snapshot=True,
        legacy_points=legacy_points,
    )


def serve_canonical(
    request: CanonicalServingRequest,
    *,
    session_factory: Callable[[], Session] | None = None,
    parity_runner: Callable[..., DailyPointRangeParity] | None = None,
    collapse_runner: Callable[..., DailyCanonicalOutcome] | None = None,
) -> CanonicalServingResult:
    """Select canonical points only after complete parity, seal, and snapshot gates."""
    if session_factory is None:
        session_factory = SessionLocal
    if parity_runner is None:
        parity_runner = run_canonical_parity
    if collapse_runner is None:
        collapse_runner = collapse_daily_canonical_range
    requested_days = (request.end - request.start).days + 1
    if (
        not request.enabled
        or not request.eligibility.eligible
        or requested_days > MAX_CANONICAL_SERVING_DAYS
        or len(request.legacy_points) != requested_days
        or tuple(point.date for point in request.legacy_points)
        != tuple(
            request.start + timedelta(days=offset)
            for offset in range(requested_days)
        )
    ):
        return _legacy_result(request, CanonicalServingOutcome.LEGACY_INELIGIBLE)

    session: Session | None = None
    entered = False
    try:
        session = session_factory()
        with session as canonical_session:
            entered = True
            try:
                parity = parity_runner(
                    canonical_session,
                    user_id=request.user_id,
                    start=request.start,
                    end=request.end,
                    legacy_points=request.legacy_points,
                )
                canonical_outcome = collapse_runner(
                    parity,
                    db=canonical_session,
                    user_id=request.user_id,
                )
                canonical_points = tuple(canonical_outcome.points or ())
                outcome = _outcome_for_daily_canonical(canonical_outcome)
                detail = _detail_for_daily_canonical(canonical_outcome)
                if outcome is not CanonicalServingOutcome.CANONICAL_SERVED:
                    return _legacy_result(request, outcome, detail=detail)
                if not _daily_point_snapshot_equal(
                    request.legacy_points,
                    canonical_points,
                    start=request.start,
                    end=request.end,
                ):
                    return _legacy_result(
                        request,
                        CanonicalServingOutcome.FALLBACK_PARITY,
                        detail=CanonicalServingDetail.LEGACY_SNAPSHOT_CHANGED,
                    )
                return CanonicalServingResult(
                    selected_points=canonical_points,
                    source=CanonicalServingSource.CANONICAL_SELECTED,
                    outcome=CanonicalServingOutcome.CANONICAL_SERVED,
                )
            except Exception as exc:
                rollback = getattr(canonical_session, "rollback", None)
                if callable(rollback):
                    with suppress(Exception):
                        rollback()
                return _legacy_result(
                    request,
                    CanonicalServingOutcome.FALLBACK_ERROR,
                    detail=CanonicalServingDetail.ERROR,
                    exception_class=canonical_telemetry._exception_class(exc),
                )
    except Exception as exc:
        if session is not None:
            rollback = getattr(session, "rollback", None)
            if callable(rollback):
                with suppress(Exception):
                    rollback()
        return _legacy_result(
            request,
            CanonicalServingOutcome.FALLBACK_ERROR,
            detail=CanonicalServingDetail.ERROR,
            exception_class=canonical_telemetry._exception_class(exc),
        )
    finally:
        if session is not None and not entered:
            close = getattr(session, "close", None)
            if callable(close):
                with suppress(Exception):
                    close()


run_canonical_serving = serve_canonical
select_canonical_points = serve_canonical


__all__ = [
    "MAX_CANONICAL_SERVING_DAYS",
    "CanonicalServingContext",
    "CanonicalServingDetail",
    "CanonicalServingEligibility",
    "CanonicalServingEligibilityReason",
    "CanonicalServingOutcome",
    "CanonicalServingRequest",
    "CanonicalServingResponse",
    "CanonicalServingResult",
    "CanonicalServingSource",
    "run_canonical_parity",
    "run_canonical_serving",
    "select_canonical_points",
    "serve_canonical",
]
