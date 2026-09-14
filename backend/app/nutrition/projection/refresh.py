from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Final
from uuid import UUID

from sqlalchemy import and_, or_, select, union
from sqlalchemy.orm import Session

from app.nutrition.enums import ProjectionStatus
from app.nutrition.models import (
    NutritionConsumptionEvent,
    NutritionDailyProjection,
    NutritionProjectionHead,
    NutritionSourceObservation,
)
from app.nutrition.projection.contracts import ProjectionPersistenceStatus
from app.nutrition.projection.orchestration import rebuild_nutrition_day
from app.nutrition.resolution.sources import DEFAULT_SOURCE_RESOLVERS, resolve_default_provider_sources
from app.source_priority.models import SourcePriorityPolicy
from app.source_priority.repositories import get_effective_policy, list_policies, list_rules

DEFAULT_REFRESH_BATCH_SIZE: Final[int] = 50


@dataclass(frozen=True, slots=True)
class NutritionProjectionRefreshResult:
    policy_version: int
    processed_count: int
    created_count: int
    unchanged_count: int
    has_more: bool
    projection_refresh_required: bool


class NutritionProjectionRefreshError(RuntimeError):
    """A refresh request cannot safely run against the requested policy."""


def _latest_policy(policies: list[SourcePriorityPolicy]) -> SourcePriorityPolicy | None:
    return max(policies, key=lambda policy: (policy.version, str(policy.id))) if policies else None

def _has_usable_effective_policy(
    db: Session,
    *,
    user_id: UUID,
    policy: SourcePriorityPolicy,
) -> bool:
    rules = list_rules(db, user_id, policy.id, data_area="nutrition")
    bindings = resolve_default_provider_sources(
        db,
        user_id=user_id,
        provider_keys=DEFAULT_SOURCE_RESOLVERS.keys(),
    )
    available_provider_keys = {binding.provider_key for binding in bindings}
    if not rules or not available_provider_keys:
        return False

    configured_provider_keys = {rule.provider_key for rule in rules}
    if not configured_provider_keys & available_provider_keys:
        return False

    if any(rule.metric_key is not None for rule in rules):
        return True

    ranks = [rule.priority_rank for rule in rules]
    if len(set(ranks)) != len(ranks) or set(ranks) != set(range(1, len(ranks) + 1)):
        return False
    return (
        configured_provider_keys <= set(DEFAULT_SOURCE_RESOLVERS)
        and available_provider_keys <= configured_provider_keys
    )


def refresh_stale_nutrition_projections(
    *,
    session_factory: Callable[[], Session],
    user_id: UUID,
    expected_version: int,
    batch_size: int = DEFAULT_REFRESH_BATCH_SIZE,
) -> NutritionProjectionRefreshResult:
    """Refresh one bounded batch of stale nutrition projections."""
    if type(batch_size) is not int or not 1 <= batch_size <= DEFAULT_REFRESH_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {DEFAULT_REFRESH_BATCH_SIZE}")

    policy_at = datetime.now(UTC)
    validation_session = session_factory()
    try:
        latest_policy = _latest_policy(list_policies(validation_session, user_id))
        if latest_policy is None or latest_policy.version != expected_version:
            actual_version = latest_policy.version if latest_policy is not None else None
            raise NutritionProjectionRefreshError(
                f"expected policy version {expected_version}, current version is {actual_version}"
            )
        policy = get_effective_policy(validation_session, user_id, policy_at)
        if policy is None or not _has_usable_effective_policy(
            validation_session,
            user_id=user_id,
            policy=policy,
        ):
            raise NutritionProjectionRefreshError("a usable effective nutrition policy is required")
        stale_dates = list_stale_nutrition_projection_dates(
            validation_session,
            user_id=user_id,
            policy=policy,
            limit=batch_size,
        )
    finally:
        validation_session.close()

    created_count = 0
    unchanged_count = 0
    for local_date in stale_dates:
        projection_session = session_factory()
        try:
            result = rebuild_nutrition_day(
                projection_session,
                user_id=user_id,
                local_date=local_date,
                policy_at=policy_at,
            )
            if result.status is ProjectionPersistenceStatus.CREATED:
                created_count += 1
            elif result.status is ProjectionPersistenceStatus.UNCHANGED:
                unchanged_count += 1
            elif result.status is ProjectionPersistenceStatus.POLICY_MISSING:
                raise NutritionProjectionRefreshError(
                    f"effective nutrition policy disappeared while rebuilding {local_date.isoformat()}"
                )
            else:
                raise NutritionProjectionRefreshError(
                    f"unexpected projection persistence status for {local_date.isoformat()}"
                )
        finally:
            projection_session.close()

    current_session = session_factory()
    try:
        current_latest_policy = _latest_policy(list_policies(current_session, user_id))
        current_policy = get_effective_policy(current_session, user_id, policy_at)
        if current_policy is None:
            has_more = False
            projection_refresh_required = False
        else:
            remaining_dates = list_stale_nutrition_projection_dates(
                current_session,
                user_id=user_id,
                policy=current_policy,
                limit=1,
            )
            has_more = bool(remaining_dates)
            projection_refresh_required = has_more or (
                current_latest_policy is not None
                and current_latest_policy.version != current_policy.version
            )
    finally:
        current_session.close()

    return NutritionProjectionRefreshResult(
        policy_version=latest_policy.version,
        processed_count=len(stale_dates),
        created_count=created_count,
        unchanged_count=unchanged_count,
        has_more=has_more,
        projection_refresh_required=projection_refresh_required,
    )


def list_stale_nutrition_projection_dates(
    db: Session,
    *,
    user_id: UUID,
    policy: SourcePriorityPolicy | None,
    limit: int,
) -> tuple[date, ...]:
    """Return bounded canonical nutrition dates whose current projection is stale."""
    if (policy is not None and policy.user_id != user_id) or limit < 1:
        return ()

    bounded_limit = min(limit, DEFAULT_REFRESH_BATCH_SIZE)
    observation_dates = select(NutritionSourceObservation.local_date.label("local_date")).where(
        NutritionSourceObservation.user_id == user_id,
        NutritionSourceObservation.local_date.is_not(None),
    )
    event_dates = select(NutritionConsumptionEvent.local_date.label("local_date")).where(
        NutritionConsumptionEvent.user_id == user_id,
        NutritionConsumptionEvent.local_date.is_not(None),
    )
    head_dates = select(NutritionProjectionHead.local_date.label("local_date")).where(
        NutritionProjectionHead.user_id == user_id,
    )
    canonical_dates = union(observation_dates, event_dates, head_dates).cte(
        "canonical_nutrition_dates"
    )

    if policy is None:
        statement = (
            select(canonical_dates.c.local_date)
            .select_from(canonical_dates)
            .order_by(canonical_dates.c.local_date)
            .limit(bounded_limit)
        )
    else:
        statement = (
            select(canonical_dates.c.local_date)
            .select_from(canonical_dates)
            .outerjoin(
                NutritionProjectionHead,
                and_(
                    NutritionProjectionHead.user_id == user_id,
                    NutritionProjectionHead.local_date == canonical_dates.c.local_date,
                ),
            )
            .outerjoin(
                NutritionDailyProjection,
                and_(
                    NutritionDailyProjection.id == NutritionProjectionHead.current_projection_id,
                    NutritionDailyProjection.user_id == NutritionProjectionHead.user_id,
                    NutritionDailyProjection.local_date == NutritionProjectionHead.local_date,
                ),
            )
            .where(
                or_(
                    NutritionProjectionHead.user_id.is_(None),
                    NutritionDailyProjection.id.is_(None),
                    NutritionDailyProjection.priority_policy_id != policy.id,
                    NutritionDailyProjection.projection_status != ProjectionStatus.READY.value,
                )
            )
            .order_by(canonical_dates.c.local_date)
            .limit(bounded_limit)
        )
    return tuple(db.scalars(statement).all())


__all__ = [
    "DEFAULT_REFRESH_BATCH_SIZE",
    "NutritionProjectionRefreshError",
    "NutritionProjectionRefreshResult",
    "list_stale_nutrition_projection_dates",
    "refresh_stale_nutrition_projections",
]
