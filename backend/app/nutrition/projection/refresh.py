from __future__ import annotations

from datetime import date
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
from app.source_priority.models import SourcePriorityPolicy

DEFAULT_REFRESH_BATCH_SIZE: Final[int] = 50


def list_stale_nutrition_projection_dates(
    db: Session,
    *,
    user_id: UUID,
    policy: SourcePriorityPolicy,
    limit: int,
) -> tuple[date, ...]:
    """Return bounded, canonical nutrition dates whose current projection is stale."""
    if policy.user_id != user_id or limit < 1:
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


__all__ = ["DEFAULT_REFRESH_BATCH_SIZE", "list_stale_nutrition_projection_dates"]
