from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.nutrition.models import NutritionIngestionRun
from app.nutrition.projection.lifecycle import (
    NutritionProjectionLifecycleError,
    NutritionProjectionLifecycleResult,
    rebuild_replay_nutrition_days,
)
from app.source_priority.bootstrap import bootstrap_nutrition_priority

_PROVIDER = "yazio"
_CONNECTOR = "sdk-v22"


def _matching_run(
    db: Session,
    *,
    user_id: UUID,
    source_instance_id: UUID,
    ingestion_run_id: UUID,
    lock: bool = False,
) -> NutritionIngestionRun:
    statement = select(NutritionIngestionRun).where(
        NutritionIngestionRun.id == ingestion_run_id,
        NutritionIngestionRun.user_id == user_id,
        NutritionIngestionRun.provider_key == _PROVIDER,
        NutritionIngestionRun.source_instance_id == source_instance_id,
        NutritionIngestionRun.connector_variant == _CONNECTOR,
    )
    if lock:
        statement = statement.with_for_update()
    run = db.scalar(statement)
    if run is None:
        raise NutritionProjectionLifecycleError("matching YAZIO SDK ingestion run is unavailable")
    if (
        run.requested_start_date is None
        or run.requested_end_date is None
        or run.requested_start_date > run.requested_end_date
    ):
        raise NutritionProjectionLifecycleError("YAZIO SDK ingestion run requested range is invalid")
    return run


def rebuild_yazio_projection_run(
    *,
    session_factory: Callable[[], Session],
    user_id: UUID,
    source_instance_id: UUID,
    ingestion_run_id: UUID,
    policy_at: datetime,
) -> NutritionProjectionLifecycleResult:
    """Replay one canonical SDK run without acknowledging it or changing sync state."""
    if policy_at.tzinfo is None or policy_at.utcoffset() is None:
        raise ValueError("policy_at must be timezone-aware")
    with session_factory() as db:
        _matching_run(
            db, user_id=user_id, source_instance_id=source_instance_id,
            ingestion_run_id=ingestion_run_id,
        )
    # Bootstrap respects existing user policies and can safely require selection.
    # The replay lifecycle selects the policy effective at this same timestamp,
    # never bootstrap's reported (possibly future-only) policy.
    bootstrap_nutrition_priority(
        session_factory=session_factory, user_id=user_id, effective_from=policy_at,
    )
    return rebuild_replay_nutrition_days(
        session_factory=session_factory, user_id=user_id,
        ingestion_run_id=ingestion_run_id, policy_at=policy_at,
    )


def acknowledge_yazio_projection_run(
    db: Session,
    *,
    user_id: UUID,
    source_instance_id: UUID,
    ingestion_run_id: UUID,
) -> None:
    """Lock and acknowledge the matching run within the caller's transaction."""
    run = _matching_run(
        db, user_id=user_id, source_instance_id=source_instance_id,
        ingestion_run_id=ingestion_run_id, lock=True,
    )
    run.projection_pending = False


def drain_pending_yazio_projections(
    *,
    session_factory: Callable[[], Session],
    user_id: UUID,
    source_instance_id: UUID,
) -> tuple[UUID, ...]:
    """Recover prior durable work while the caller holds its user/YAZIO locks.

    Call before fetching/persisting a new run. Each completed older run gets its
    own acknowledgement transaction. A failing replay or acknowledgement stops
    the drain, leaves that run pending, and prevents later work from overtaking it.
    """
    policy_at = datetime.now(UTC)
    with session_factory() as db:
        pending_ids = tuple(db.scalars(
            select(NutritionIngestionRun.id)
            .where(
                NutritionIngestionRun.user_id == user_id,
                NutritionIngestionRun.provider_key == _PROVIDER,
                NutritionIngestionRun.source_instance_id == source_instance_id,
                NutritionIngestionRun.connector_variant == _CONNECTOR,
                NutritionIngestionRun.projection_pending.is_(True),
            )
            .order_by(NutritionIngestionRun.created_at, NutritionIngestionRun.id)
        ))

    acknowledged: list[UUID] = []
    for ingestion_run_id in pending_ids:
        rebuild_yazio_projection_run(
            session_factory=session_factory, user_id=user_id,
            source_instance_id=source_instance_id, ingestion_run_id=ingestion_run_id,
            policy_at=policy_at,
        )
        with session_factory() as db, db.begin():
            acknowledge_yazio_projection_run(
                db, user_id=user_id, source_instance_id=source_instance_id,
                ingestion_run_id=ingestion_run_id,
            )
        acknowledged.append(ingestion_run_id)
    return tuple(acknowledged)
