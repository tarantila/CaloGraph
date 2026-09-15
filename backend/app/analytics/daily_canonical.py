from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from time import monotonic
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.daily_point_parity import (
    DailyPointParityClassification,
    DailyPointRangeParity,
    compare_daily_point_range,
)
from app.database import SessionLocal
from app.nutrition.models import NutritionDailyProjection, NutritionProjectionHead
from app.schemas import DailyPoint
from app.source_priority.contracts import PriorityPolicySnapshot, PriorityRuleSnapshot
from app.source_priority.models import SourcePriorityPolicy, SourcePriorityRule
from app.source_priority.repositories import get_effective_policy

LOGGER = logging.getLogger(__name__)
TELEMETRY_EVENT = "analytics.daily.canonical"
TELEMETRY_VERSION = "d4b.v1"
MAX_CANONICAL_READ_DAYS = 31
_MAX_EXCEPTION_CLASS_LENGTH = 64


class DailyCanonicalState(StrEnum):
    DISABLED = "disabled"
    SKIPPED = "skipped"
    MATCH = "match"
    FALLBACK = "fallback"
    EXPECTED_DIFFERENCE = "expected_difference"
    UNEXPLAINED_MISMATCH = "unexplained_mismatch"
    NOT_COMPARABLE = "not_comparable"
    ERROR = "error"


DailyCanonicalReadState = DailyCanonicalState


class DailyCanonicalTelemetryOutcome(StrEnum):
    CANONICAL_SERVED = "canonical_served"
    LEGACY_FALLBACK_NOT_ELIGIBLE = "legacy_fallback_not_eligible"
    LEGACY_FALLBACK_NOT_READY = "legacy_fallback_not_ready"
    LEGACY_FALLBACK_NOT_COMPARABLE = "legacy_fallback_not_comparable"
    LEGACY_FALLBACK_EXPECTED_DIFFERENCE = "legacy_fallback_expected_difference"
    LEGACY_FALLBACK_UNEXPLAINED_MISMATCH = "legacy_fallback_unexplained_mismatch"
    LEGACY_FALLBACK_ERROR = "legacy_fallback_error"


@dataclass(frozen=True, slots=True)
class DailyCanonicalOutcome:
    state: DailyCanonicalState
    points: tuple[DailyPoint, ...] | None = None
    reason: str | None = None
    exception_class: str | None = None
    range_bucket: str | None = None
    duration_bucket: str | None = None

    @property
    def canonical_points(self) -> tuple[DailyPoint, ...] | None:
        return self.points


DailyCanonicalReadOutcome = DailyCanonicalOutcome


def check_daily_canonical_read_eligibility(
    enabled: bool,
    source: str | None,
    tracking: str | None,
    weekday: int | None,
    start: date,
    end: date,
    max_days: int = MAX_CANONICAL_READ_DAYS,
    *,
    period: str | None = None,
) -> DailyCanonicalOutcome:
    """Return a request-independent D4B eligibility outcome."""
    if not enabled:
        return DailyCanonicalOutcome(DailyCanonicalState.DISABLED, reason="disabled")
    if period is not None:
        return DailyCanonicalOutcome(DailyCanonicalState.SKIPPED, reason="period")
    if source is not None:
        return DailyCanonicalOutcome(DailyCanonicalState.SKIPPED, reason="source_filter")
    if tracking is not None:
        return DailyCanonicalOutcome(DailyCanonicalState.SKIPPED, reason="tracking_filter")
    if weekday is not None:
        return DailyCanonicalOutcome(DailyCanonicalState.SKIPPED, reason="weekday_filter")
    if (
        not isinstance(start, date)
        or isinstance(start, datetime)
        or not isinstance(end, date)
        or isinstance(end, datetime)
        or start > end
    ):
        return DailyCanonicalOutcome(DailyCanonicalState.SKIPPED, reason="invalid_range")
    if type(max_days) is not int or max_days < 1 or max_days > MAX_CANONICAL_READ_DAYS:
        return DailyCanonicalOutcome(DailyCanonicalState.SKIPPED, reason="range_too_large")
    if (end - start).days + 1 > max_days:
        return DailyCanonicalOutcome(DailyCanonicalState.SKIPPED, reason="range_too_large")
    return DailyCanonicalOutcome(DailyCanonicalState.MATCH)


def is_daily_canonical_read_eligible(
    enabled: bool,
    source: str | None,
    tracking: str | None,
    weekday: int | None,
    start: date,
    end: date,
    max_days: int = MAX_CANONICAL_READ_DAYS,
    *,
    period: str | None = None,
) -> bool:
    return check_daily_canonical_read_eligibility(
        enabled=enabled,
        period=period,
        source=source,
        tracking=tracking,
        weekday=weekday,
        start=start,
        end=end,
        max_days=max_days,
    ).state is DailyCanonicalState.MATCH


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


def _telemetry_outcome(outcome: DailyCanonicalOutcome) -> DailyCanonicalTelemetryOutcome:
    if outcome.state is DailyCanonicalState.MATCH:
        if outcome.points is not None:
            return DailyCanonicalTelemetryOutcome.CANONICAL_SERVED
        return DailyCanonicalTelemetryOutcome.LEGACY_FALLBACK_NOT_READY
    if outcome.state in {DailyCanonicalState.DISABLED, DailyCanonicalState.SKIPPED}:
        return DailyCanonicalTelemetryOutcome.LEGACY_FALLBACK_NOT_ELIGIBLE
    if outcome.state is DailyCanonicalState.NOT_COMPARABLE:
        if outcome.reason == "projection_not_ready":
            return DailyCanonicalTelemetryOutcome.LEGACY_FALLBACK_NOT_READY
        return DailyCanonicalTelemetryOutcome.LEGACY_FALLBACK_NOT_COMPARABLE
    if outcome.state is DailyCanonicalState.EXPECTED_DIFFERENCE:
        return DailyCanonicalTelemetryOutcome.LEGACY_FALLBACK_EXPECTED_DIFFERENCE
    if outcome.state is DailyCanonicalState.UNEXPLAINED_MISMATCH:
        return DailyCanonicalTelemetryOutcome.LEGACY_FALLBACK_UNEXPLAINED_MISMATCH
    if outcome.state is DailyCanonicalState.ERROR:
        return DailyCanonicalTelemetryOutcome.LEGACY_FALLBACK_ERROR
    return DailyCanonicalTelemetryOutcome.LEGACY_FALLBACK_ERROR


def _emit_telemetry(
    *,
    outcome: DailyCanonicalOutcome,
    start: date,
    end: date,
    elapsed_seconds: float,
    exception_class: str | None = None,
) -> None:
    """Emit only fixed and bounded fields; never attach values or exception details."""
    fields: dict[str, Any] = {
        "event": TELEMETRY_EVENT,
        "version": TELEMETRY_VERSION,
        "outcome": _telemetry_outcome(outcome).value,
        "range_bucket": _range_bucket(start, end),
        "duration_bucket": _duration_bucket(elapsed_seconds),
    }
    if exception_class is not None:
        fields["exception_class"] = exception_class
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


def _valid_source_priority_policy(
    db: Session,
    *,
    user_id: UUID,
    policy_id: object,
) -> bool:
    """Validate the persisted policy referenced by a current projection."""
    if not isinstance(policy_id, UUID):
        return False
    try:
        policy = db.scalar(
            select(SourcePriorityPolicy).where(
                SourcePriorityPolicy.id == policy_id,
                SourcePriorityPolicy.user_id == user_id,
            )
        )
        if policy is None:
            return False
        current_policy = get_effective_policy(db, user_id, datetime.now(UTC))
        if current_policy is None or current_policy.id != policy_id:
            return False

        rules = tuple(
            db.scalars(
                select(SourcePriorityRule)
                .where(
                    SourcePriorityRule.policy_id == policy_id,
                    SourcePriorityRule.user_id == user_id,
                )
                .order_by(SourcePriorityRule.priority_rank, SourcePriorityRule.id)
            ).all()
        )
        effective_from = policy.effective_from
        if effective_from.tzinfo is None or effective_from.utcoffset() is None:
            effective_from = effective_from.replace(tzinfo=UTC)
        PriorityPolicySnapshot(
            policy_id=policy.id,
            user_id=policy.user_id,
            version=policy.version,
            effective_from=effective_from,
            rules=tuple(
                PriorityRuleSnapshot(
                    rule_id=rule.id,
                    data_area=rule.data_area,
                    metric_key=rule.metric_key,
                    provider_key=rule.provider_key,
                    priority_rank=rule.priority_rank,
                )
                for rule in rules
            ),
        )
    except (AttributeError, TypeError, ValueError):
        return False
    return True


def _classification_outcome(day: object) -> DailyCanonicalOutcome | None:
    comparable = getattr(day, "comparable", False)
    classification = getattr(day, "classification", None)
    if not comparable or classification == DailyPointParityClassification.NOT_COMPARABLE:
        return DailyCanonicalOutcome(
            DailyCanonicalState.NOT_COMPARABLE,
            reason="not_comparable",
        )
    if classification == DailyPointParityClassification.UNEXPLAINED_MISMATCH:
        return DailyCanonicalOutcome(
            DailyCanonicalState.UNEXPLAINED_MISMATCH,
            reason="unexplained_mismatch",
        )
    if classification != DailyPointParityClassification.MATCH:
        return DailyCanonicalOutcome(
            DailyCanonicalState.EXPECTED_DIFFERENCE,
            reason="expected_difference",
        )
    return None


def _read_canonical_head_metadata(
    db: Session,
    *,
    user_id: UUID,
    start: date,
    end: date,
) -> dict[date, tuple[UUID, UUID]]:
    """Read current-head projection and policy IDs in one private query."""
    rows = db.execute(
        select(
            NutritionProjectionHead.local_date,
            NutritionProjectionHead.current_projection_id,
            NutritionDailyProjection.priority_policy_id,
        )
        .join(
            NutritionDailyProjection,
            (NutritionDailyProjection.id == NutritionProjectionHead.current_projection_id)
            & (NutritionDailyProjection.user_id == NutritionProjectionHead.user_id)
            & (NutritionDailyProjection.local_date == NutritionProjectionHead.local_date),
        )
        .where(
            NutritionProjectionHead.user_id == user_id,
            NutritionProjectionHead.local_date >= start,
            NutritionProjectionHead.local_date <= end,
        )
    ).all()
    return {
        local_date: (projection_id, policy_id)
        for local_date, projection_id, policy_id in rows
    }


def _strict_canonical_points(
    db: Session,
    *,
    user_id: UUID,
    parity: DailyPointRangeParity,
) -> DailyCanonicalOutcome:
    days = tuple(parity.days)
    if not days:
        return DailyCanonicalOutcome(DailyCanonicalState.NOT_COMPARABLE, reason="not_comparable")

    # Keep the evaluator usable with older immutable parity doubles while the
    # production D3B range carries the strict-read metadata below.
    if not hasattr(parity, "canonical_projection_ready"):
        return _strict_canonical_points_without_policy(parity)

    for day in days:
        classification_outcome = _classification_outcome(day)
        if classification_outcome is not None:
            return classification_outcome

    canonical_points = tuple(getattr(parity, "canonical_points", ()))
    canonical_projection_ids = tuple(getattr(parity, "canonical_projection_ids", ()))
    projection_ready = tuple(getattr(parity, "canonical_projection_ready", ()))
    calorie_usable = tuple(getattr(parity, "canonical_calorie_usable", ()))
    if len(canonical_points) != len(days):
        return DailyCanonicalOutcome(
            DailyCanonicalState.NOT_COMPARABLE,
            reason="projection_not_ready",
        )
    if len(projection_ready) != len(days) or not all(projection_ready):
        return DailyCanonicalOutcome(
            DailyCanonicalState.NOT_COMPARABLE,
            reason="projection_not_ready",
        )
    if len(calorie_usable) != len(days) or not all(calorie_usable):
        return DailyCanonicalOutcome(
            DailyCanonicalState.FALLBACK,
            reason="projection_no_primary_values",
        )
    if len(canonical_projection_ids) != len(days) or any(
        not isinstance(projection_id, UUID) for projection_id in canonical_projection_ids
    ):
        return DailyCanonicalOutcome(
            DailyCanonicalState.NOT_COMPARABLE,
            reason="projection_not_ready",
        )
    head_metadata_by_date = _read_canonical_head_metadata(
        db,
        user_id=user_id,
        start=parity.start,
        end=parity.end,
    )
    requested_dates = tuple(
        parity.start + timedelta(days=offset) for offset in range(len(days))
    )
    if len(head_metadata_by_date) != len(days):
        return DailyCanonicalOutcome(
            DailyCanonicalState.NOT_COMPARABLE,
            reason="projection_not_ready",
        )
    policy_ids: list[UUID] = []
    for local_date, carried_projection_id in zip(
        requested_dates, canonical_projection_ids, strict=True
    ):
        current_metadata = head_metadata_by_date.get(local_date)
        if current_metadata is None or current_metadata[0] != carried_projection_id:
            return DailyCanonicalOutcome(
                DailyCanonicalState.NOT_COMPARABLE,
                reason="projection_not_ready",
            )
        policy_ids.append(current_metadata[1])
    valid_policy_ids: set[UUID] = set()
    for policy_id in policy_ids:
        if not isinstance(policy_id, UUID):
            return DailyCanonicalOutcome(
                DailyCanonicalState.FALLBACK,
                reason="source_priority_policy_invalid",
            )
        if policy_id not in valid_policy_ids:
            if not _valid_source_priority_policy(
                db,
                user_id=user_id,
                policy_id=policy_id,
            ):
                return DailyCanonicalOutcome(
                    DailyCanonicalState.FALLBACK,
                    reason="source_priority_policy_invalid",
                )
            valid_policy_ids.add(policy_id)
    return DailyCanonicalOutcome(DailyCanonicalState.MATCH, points=canonical_points)


def _strict_canonical_points_without_policy(
    parity: DailyPointRangeParity,
) -> DailyCanonicalOutcome:
    """Classify synthetic parity values without claiming a production read."""
    days = tuple(parity.days)
    if not days:
        return DailyCanonicalOutcome(DailyCanonicalState.NOT_COMPARABLE, reason="not_comparable")
    for day in days:
        classification_outcome = _classification_outcome(day)
        if classification_outcome is not None:
            return classification_outcome
    canonical_points = tuple(getattr(parity, "canonical_points", ()))
    return DailyCanonicalOutcome(
        DailyCanonicalState.MATCH,
        points=canonical_points or None,
    )


def collapse_daily_canonical_range(
    parity: DailyPointRangeParity,
    *,
    db: Session | None = None,
    user_id: UUID | None = None,
) -> DailyCanonicalOutcome:
    """Serve canonical points only for a strict all-days D3B match."""
    if db is None or user_id is None:
        return _strict_canonical_points_without_policy(parity)
    return _strict_canonical_points(db, user_id=user_id, parity=parity)


collapse_daily_canonical_read_range = collapse_daily_canonical_range


def run_daily_canonical_read(
    user_id: UUID,
    start: date,
    end: date,
    source: str | None,
    tracking: str | None,
    weekday: int | None,
    *,
    period: str | None = None,
    enabled: bool,
    max_days: int = MAX_CANONICAL_READ_DAYS,
) -> DailyCanonicalOutcome:
    """Run one bounded, read-only D4B comparison and fail open on every error."""
    started = monotonic()
    eligibility = check_daily_canonical_read_eligibility(
        enabled=enabled,
        period=period,
        source=source,
        tracking=tracking,
        weekday=weekday,
        start=start,
        end=end,
        max_days=max_days,
    )
    if eligibility.state is not DailyCanonicalState.MATCH:
        if enabled:
            _emit_telemetry(
                outcome=eligibility,
                start=start,
                end=end,
                elapsed_seconds=monotonic() - started,
            )
        return eligibility

    session: object | None = None
    try:
        session = SessionLocal()
        with session as canonical_session:
            try:
                parity = compare_daily_point_range(
                    canonical_session,
                    user_id=user_id,
                    start=start,
                    end=end,
                    max_days=max_days,
                )
                outcome = collapse_daily_canonical_range(
                    parity,
                    db=canonical_session,
                    user_id=user_id,
                )
            except Exception as exc:
                _rollback_safely(canonical_session)
                outcome = DailyCanonicalOutcome(
                    DailyCanonicalState.ERROR,
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
        outcome = DailyCanonicalOutcome(
            DailyCanonicalState.ERROR,
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
    "MAX_CANONICAL_READ_DAYS",
    "TELEMETRY_EVENT",
    "TELEMETRY_VERSION",
    "DailyCanonicalOutcome",
    "DailyCanonicalReadOutcome",
    "DailyCanonicalReadState",
    "DailyCanonicalState",
    "DailyCanonicalTelemetryOutcome",
    "check_daily_canonical_read_eligibility",
    "collapse_daily_canonical_range",
    "collapse_daily_canonical_read_range",
    "is_daily_canonical_read_eligible",
    "run_daily_canonical_read",
]
