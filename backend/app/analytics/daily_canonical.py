from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from time import monotonic
from typing import Any
from uuid import UUID

from sqlalchemy import select, true
from sqlalchemy.orm import Session

from app.analytics import canonical_telemetry
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

LOGGER = logging.getLogger(__name__)
TELEMETRY_EVENT = "analytics.daily.canonical"
TELEMETRY_VERSION = "d4b.v1"
MAX_CANONICAL_READ_DAYS = 31


class DailyCanonicalState(StrEnum):
    DISABLED = "disabled"
    SKIPPED = "skipped"
    MATCH = "match"
    FALLBACK = "fallback"
    EXPECTED_DIFFERENCE = "expected_difference"
    UNEXPLAINED_MISMATCH = "unexplained_mismatch"
    NOT_COMPARABLE = "not_comparable"
    ERROR = "error"


class DailyCanonicalReason(StrEnum):
    DISABLED = "disabled"
    PERIOD = "period"
    SOURCE_FILTER = "source_filter"
    TRACKING_FILTER = "tracking_filter"
    WEEKDAY_FILTER = "weekday_filter"
    INVALID_RANGE = "invalid_range"
    RANGE_TOO_LARGE = "range_too_large"
    NOT_COMPARABLE = "not_comparable"
    PROJECTION_NOT_READY = "projection_not_ready"
    PROJECTION_NO_PRIMARY_VALUES = "projection_no_primary_values"
    SOURCE_PRIORITY_POLICY_INVALID = "source_priority_policy_invalid"
    LEGACY_SNAPSHOT_CHANGED = "legacy_snapshot_changed"
    EXPECTED_DIFFERENCE = "expected_difference"
    UNEXPLAINED_MISMATCH = "unexplained_mismatch"


DailyCanonicalDetail = DailyCanonicalReason


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
    reason: DailyCanonicalReason | None = None
    exception_class: str | None = None
    range_bucket: str | None = None
    duration_bucket: str | None = None

    def __post_init__(self) -> None:
        if self.reason is not None:
            try:
                object.__setattr__(self, "reason", DailyCanonicalReason(self.reason))
            except (TypeError, ValueError) as exc:
                raise TypeError("reason must be a known DailyCanonicalReason") from exc

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
        return DailyCanonicalOutcome(DailyCanonicalState.DISABLED, reason=DailyCanonicalReason.DISABLED)
    if period is not None:
        return DailyCanonicalOutcome(DailyCanonicalState.SKIPPED, reason=DailyCanonicalReason.PERIOD)
    if source is not None:
        return DailyCanonicalOutcome(DailyCanonicalState.SKIPPED, reason=DailyCanonicalReason.SOURCE_FILTER)
    if tracking is not None:
        return DailyCanonicalOutcome(DailyCanonicalState.SKIPPED, reason=DailyCanonicalReason.TRACKING_FILTER)
    if weekday is not None:
        return DailyCanonicalOutcome(DailyCanonicalState.SKIPPED, reason=DailyCanonicalReason.WEEKDAY_FILTER)
    if (
        not isinstance(start, date)
        or isinstance(start, datetime)
        or not isinstance(end, date)
        or isinstance(end, datetime)
        or start > end
    ):
        return DailyCanonicalOutcome(DailyCanonicalState.SKIPPED, reason=DailyCanonicalReason.INVALID_RANGE)
    if type(max_days) is not int or max_days < 1 or max_days > MAX_CANONICAL_READ_DAYS:
        return DailyCanonicalOutcome(
            DailyCanonicalState.SKIPPED,
            reason=DailyCanonicalReason.RANGE_TOO_LARGE,
        )
    if (end - start).days + 1 > max_days:
        return DailyCanonicalOutcome(
            DailyCanonicalState.SKIPPED,
            reason=DailyCanonicalReason.RANGE_TOO_LARGE,
        )
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




def _telemetry_outcome(outcome: DailyCanonicalOutcome) -> DailyCanonicalTelemetryOutcome:
    if outcome.state is DailyCanonicalState.MATCH:
        if outcome.points is not None:
            return DailyCanonicalTelemetryOutcome.CANONICAL_SERVED
        return DailyCanonicalTelemetryOutcome.LEGACY_FALLBACK_NOT_READY
    if outcome.state in {DailyCanonicalState.DISABLED, DailyCanonicalState.SKIPPED}:
        return DailyCanonicalTelemetryOutcome.LEGACY_FALLBACK_NOT_ELIGIBLE
    if outcome.state is DailyCanonicalState.NOT_COMPARABLE:
        if outcome.reason is DailyCanonicalReason.PROJECTION_NOT_READY:
            return DailyCanonicalTelemetryOutcome.LEGACY_FALLBACK_NOT_READY
        return DailyCanonicalTelemetryOutcome.LEGACY_FALLBACK_NOT_COMPARABLE
    if outcome.state is DailyCanonicalState.FALLBACK:
        if outcome.reason is DailyCanonicalReason.PROJECTION_NO_PRIMARY_VALUES:
            return DailyCanonicalTelemetryOutcome.LEGACY_FALLBACK_NOT_READY
        if outcome.reason is DailyCanonicalReason.LEGACY_SNAPSHOT_CHANGED:
            return DailyCanonicalTelemetryOutcome.LEGACY_FALLBACK_UNEXPLAINED_MISMATCH
        return DailyCanonicalTelemetryOutcome.LEGACY_FALLBACK_ERROR
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
    fields: dict[str, Any] = {
        "event": TELEMETRY_EVENT,
        "version": TELEMETRY_VERSION,
        "outcome": _telemetry_outcome(outcome).value,
        "range_bucket": canonical_telemetry._range_bucket(start, end),
        "duration_bucket": canonical_telemetry._duration_bucket(elapsed_seconds),
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


def _classification_outcome(day: object) -> DailyCanonicalOutcome | None:
    comparable = getattr(day, "comparable", False)
    classification = getattr(day, "classification", None)
    if not comparable or classification == DailyPointParityClassification.NOT_COMPARABLE:
        return DailyCanonicalOutcome(
            DailyCanonicalState.NOT_COMPARABLE,
            reason=DailyCanonicalReason.NOT_COMPARABLE,
        )
    if classification == DailyPointParityClassification.UNEXPLAINED_MISMATCH:
        return DailyCanonicalOutcome(
            DailyCanonicalState.UNEXPLAINED_MISMATCH,
            reason=DailyCanonicalReason.UNEXPLAINED_MISMATCH,
        )
    if classification != DailyPointParityClassification.MATCH:
        return DailyCanonicalOutcome(
            DailyCanonicalState.EXPECTED_DIFFERENCE,
            reason=DailyCanonicalReason.EXPECTED_DIFFERENCE,
        )
    return None


def _seal_canonical_serving(
    db: Session,
    *,
    user_id: UUID,
    start: date,
    end: date,
    expected_projection_ids: tuple[UUID, ...],
    expected_policy: PriorityPolicySnapshot,
) -> bool:
    """Read and validate every serving dependency in one snapshot-consistent statement."""
    if expected_policy.user_id != user_id:
        return False

    requested_dates = tuple(
        start + timedelta(days=offset) for offset in range((end - start).days + 1)
    )
    if len(expected_projection_ids) != len(requested_dates):
        return False

    try:
        effective_policy = (
            select(
                SourcePriorityPolicy.id.label("effective_policy_id"),
                SourcePriorityPolicy.user_id.label("effective_policy_user_id"),
                SourcePriorityPolicy.version.label("effective_policy_version"),
                SourcePriorityPolicy.effective_from.label("effective_policy_effective_from"),
            )
            .where(
                SourcePriorityPolicy.user_id == user_id,
                SourcePriorityPolicy.effective_from <= datetime.now(UTC),
            )
            .order_by(SourcePriorityPolicy.effective_from.desc())
            .limit(1)
            .subquery("effective_policy")
        )
        rows = db.execute(
            select(
                NutritionProjectionHead.local_date.label("head_local_date"),
                NutritionProjectionHead.current_projection_id.label("head_projection_id"),
                NutritionDailyProjection.id.label("projection_id"),
                NutritionDailyProjection.user_id.label("projection_user_id"),
                NutritionDailyProjection.local_date.label("projection_local_date"),
                NutritionDailyProjection.projection_status.label("projection_status"),
                NutritionDailyProjection.priority_policy_id.label("projection_policy_id"),
                effective_policy.c.effective_policy_id,
                effective_policy.c.effective_policy_user_id,
                effective_policy.c.effective_policy_version,
                effective_policy.c.effective_policy_effective_from,
                SourcePriorityRule.id.label("rule_id"),
                SourcePriorityRule.user_id.label("rule_user_id"),
                SourcePriorityRule.policy_id.label("rule_policy_id"),
                SourcePriorityRule.data_area.label("rule_data_area"),
                SourcePriorityRule.metric_key.label("rule_metric_key"),
                SourcePriorityRule.provider_key.label("rule_provider_key"),
                SourcePriorityRule.priority_rank.label("rule_priority_rank"),
            )
            .select_from(NutritionProjectionHead)
            .outerjoin(
                NutritionDailyProjection,
                (NutritionDailyProjection.id == NutritionProjectionHead.current_projection_id)
                & (NutritionDailyProjection.user_id == NutritionProjectionHead.user_id)
                & (NutritionDailyProjection.local_date == NutritionProjectionHead.local_date),
            )
            .outerjoin(effective_policy, true())
            .outerjoin(
                SourcePriorityRule,
                (SourcePriorityRule.user_id == effective_policy.c.effective_policy_user_id)
                & (SourcePriorityRule.policy_id == effective_policy.c.effective_policy_id),
            )
            .where(
                NutritionProjectionHead.user_id == user_id,
                NutritionProjectionHead.local_date >= start,
                NutritionProjectionHead.local_date <= end,
            )
            .order_by(
                NutritionProjectionHead.local_date,
                SourcePriorityRule.priority_rank,
                SourcePriorityRule.id,
            )
        ).all()
    except Exception:
        return False

    heads: dict[date, tuple[UUID, UUID]] = {}
    effective_policy_values: tuple[UUID, UUID, int, datetime] | None = None
    rules: dict[UUID, PriorityRuleSnapshot] = {}
    try:
        for row in rows:
            head_date = row.head_local_date
            head_projection_id = row.head_projection_id
            projection_id = row.projection_id
            projection_user_id = row.projection_user_id
            projection_local_date = row.projection_local_date
            projection_status = row.projection_status
            projection_policy_id = row.projection_policy_id
            if (
                type(head_date) is not date
                or not isinstance(head_projection_id, UUID)
                or projection_id != head_projection_id
                or projection_user_id != user_id
                or projection_local_date != head_date
                or projection_status != "ready"
                or projection_policy_id != expected_policy.policy_id
            ):
                return False
            previous_head = heads.setdefault(head_date, (head_projection_id, projection_policy_id))
            if previous_head != (head_projection_id, projection_policy_id):
                return False

            policy_id = row.effective_policy_id
            policy_user_id = row.effective_policy_user_id
            policy_version = row.effective_policy_version
            policy_effective_from = row.effective_policy_effective_from
            if not (
                isinstance(policy_id, UUID)
                and policy_user_id == user_id
                and type(policy_version) is int
                and isinstance(policy_effective_from, datetime)
            ):
                return False
            if (
                policy_effective_from.tzinfo is None
                or policy_effective_from.utcoffset() is None
            ):
                policy_effective_from = policy_effective_from.replace(tzinfo=UTC)
            current_policy_values = (
                policy_id,
                policy_user_id,
                policy_version,
                policy_effective_from.astimezone(UTC),
            )
            if (
                effective_policy_values is not None
                and effective_policy_values != current_policy_values
            ):
                return False
            effective_policy_values = current_policy_values

            rule_id = row.rule_id
            if rule_id is None:
                if any(
                    value is not None
                    for value in (
                        row.rule_user_id,
                        row.rule_policy_id,
                        row.rule_data_area,
                        row.rule_metric_key,
                        row.rule_provider_key,
                        row.rule_priority_rank,
                    )
                ):
                    return False
                continue
            if not isinstance(rule_id, UUID):
                return False
            if row.rule_user_id != user_id or row.rule_policy_id != policy_id:
                return False
            rule = PriorityRuleSnapshot(
                rule_id=rule_id,
                data_area=row.rule_data_area,
                metric_key=row.rule_metric_key,
                provider_key=row.rule_provider_key,
                priority_rank=row.rule_priority_rank,
            )
            previous_rule = rules.setdefault(rule_id, rule)
            if previous_rule != rule:
                return False

        if effective_policy_values is None or set(heads) != set(requested_dates):
            return False
        for local_date, expected_projection_id in zip(
            requested_dates, expected_projection_ids, strict=True
        ):
            if heads.get(local_date) != (expected_projection_id, expected_policy.policy_id):
                return False
        actual_policy = PriorityPolicySnapshot(
            policy_id=effective_policy_values[0],
            user_id=effective_policy_values[1],
            version=effective_policy_values[2],
            effective_from=effective_policy_values[3],
            rules=tuple(rules.values()),
        )
    except (AttributeError, TypeError, ValueError):
        return False
    return actual_policy == expected_policy


def _strict_canonical_points(
    db: Session,
    *,
    user_id: UUID,
    parity: DailyPointRangeParity,
) -> DailyCanonicalOutcome:
    days = tuple(parity.days)
    if not days:
        return DailyCanonicalOutcome(
            DailyCanonicalState.NOT_COMPARABLE,
            reason=DailyCanonicalReason.NOT_COMPARABLE,
        )

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
            reason=DailyCanonicalReason.PROJECTION_NOT_READY,
        )
    if len(projection_ready) != len(days) or not all(projection_ready):
        return DailyCanonicalOutcome(
            DailyCanonicalState.NOT_COMPARABLE,
            reason=DailyCanonicalReason.PROJECTION_NOT_READY,
        )
    if len(calorie_usable) != len(days) or not all(calorie_usable):
        return DailyCanonicalOutcome(
            DailyCanonicalState.FALLBACK,
            reason=DailyCanonicalReason.PROJECTION_NO_PRIMARY_VALUES,
        )
    if len(canonical_projection_ids) != len(days) or any(
        not isinstance(projection_id, UUID) for projection_id in canonical_projection_ids
    ):
        return DailyCanonicalOutcome(
            DailyCanonicalState.NOT_COMPARABLE,
            reason=DailyCanonicalReason.PROJECTION_NOT_READY,
        )
    expected_policy = getattr(parity, "canonical_policy_snapshot", None)
    if not isinstance(expected_policy, PriorityPolicySnapshot):
        return DailyCanonicalOutcome(
            DailyCanonicalState.FALLBACK,
            reason=DailyCanonicalReason.SOURCE_PRIORITY_POLICY_INVALID,
        )
    if not _seal_canonical_serving(
        db,
        user_id=user_id,
        start=parity.start,
        end=parity.end,
        expected_projection_ids=canonical_projection_ids,
        expected_policy=expected_policy,
    ):
        return DailyCanonicalOutcome(
            DailyCanonicalState.FALLBACK,
            reason=DailyCanonicalReason.SOURCE_PRIORITY_POLICY_INVALID,
        )
    return DailyCanonicalOutcome(DailyCanonicalState.MATCH, points=canonical_points)


def _strict_canonical_points_without_policy(
    parity: DailyPointRangeParity,
) -> DailyCanonicalOutcome:
    """Classify synthetic parity values without claiming a production read."""
    days = tuple(parity.days)
    if not days:
        return DailyCanonicalOutcome(
            DailyCanonicalState.NOT_COMPARABLE,
            reason=DailyCanonicalReason.NOT_COMPARABLE,
        )
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
    emit_telemetry: bool = True,
    legacy_points: tuple[DailyPoint, ...] | None = None,
) -> DailyCanonicalOutcome:
    """Run the established direct read or the shared snapshot-serving path."""
    from app.analytics.canonical_serving import (
        CanonicalServingDetail,
        CanonicalServingEligibility,
        CanonicalServingEligibilityReason,
        CanonicalServingEndpoint,
        CanonicalServingOutcome,
        CanonicalServingRequest,
        serve_canonical,
    )

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
        if enabled and emit_telemetry:
            _emit_telemetry(
                outcome=eligibility,
                start=start,
                end=end,
                elapsed_seconds=monotonic() - started,
            )
        return eligibility

    if legacy_points is None:
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
                        include_canonical_policy_snapshot=True,
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
                        exception_class=canonical_telemetry._exception_class(exc),
                    )
            if emit_telemetry:
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
                exception_class=canonical_telemetry._exception_class(exc),
            )
            if emit_telemetry:
                _emit_telemetry(
                    outcome=outcome,
                    start=start,
                    end=end,
                    elapsed_seconds=monotonic() - started,
                    exception_class=outcome.exception_class,
                )
            return outcome

    shared_reason = None
    if eligibility.reason is not None:
        shared_reason = CanonicalServingEligibilityReason(eligibility.reason.value)
    request = CanonicalServingRequest(
        user_id=user_id,
        start=start,
        end=end,
        legacy_points=legacy_points,
        enabled=enabled,
        endpoint=CanonicalServingEndpoint.DAILY,
        eligibility=CanonicalServingEligibility(
            eligible=True,
            reason=shared_reason,
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
            outcome = eligibility
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
    if emit_telemetry:
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
    "DailyCanonicalDetail",
    "DailyCanonicalOutcome",
    "DailyCanonicalReadOutcome",
    "DailyCanonicalReadState",
    "DailyCanonicalReason",
    "DailyCanonicalState",
    "DailyCanonicalTelemetryOutcome",
    "check_daily_canonical_read_eligibility",
    "collapse_daily_canonical_range",
    "collapse_daily_canonical_read_range",
    "is_daily_canonical_read_eligible",
    "run_daily_canonical_read",
]
