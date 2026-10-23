from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.nutrition.models import (
    NutritionConsumptionEvent,
    NutritionIngestionRun,
    NutritionProjectionHead,
    NutritionSourceObservation,
)
from app.nutrition.projection.contracts import ProjectionPersistenceStatus
from app.nutrition.projection.orchestration import rebuild_nutrition_day


class NutritionProjectionLifecycleError(RuntimeError):
    """The projection lifecycle input cannot be resolved safely."""


@dataclass(frozen=True, slots=True)
class NutritionProjectionLifecycleResult:
    """Result shape reserved for lifecycle orchestration."""

    user_id: UUID
    ingestion_run_id: UUID
    affected_dates: tuple[date, ...]
    created_dates: tuple[date, ...]
    unchanged_dates: tuple[date, ...]
    policy_missing_dates: tuple[date, ...]


_MISSING_RUN_ERROR = "nutrition ingestion run is unavailable for this user"
_MALFORMED_ANCESTRY_ERROR = "nutrition consumption event ancestry is inconsistent"
_RESOLUTION_ERROR = "nutrition projection lifecycle resolution failed for ingestion run"
_REBUILD_ERROR = "nutrition projection lifecycle rebuild failed for ingestion run"


def _load_run(
    db: Session, *, user_id: UUID, ingestion_run_id: UUID
) -> NutritionIngestionRun:
    run = db.scalar(
        select(NutritionIngestionRun).where(
            NutritionIngestionRun.id == ingestion_run_id,
            NutritionIngestionRun.user_id == user_id,
        )
    )
    if run is None or not run.provider_key or run.source_instance_id is None:
        raise NutritionProjectionLifecycleError(_MISSING_RUN_ERROR)
    return run


def _validate_observation(
    db: Session,
    observation: NutritionSourceObservation,
    *,
    run: NutritionIngestionRun,
) -> None:
    lineage_run = _load_run(
        db, user_id=run.user_id, ingestion_run_id=observation.ingestion_run_id
    )
    if (
        observation.user_id != run.user_id
        or observation.provider_key != run.provider_key
        or observation.source_instance_id != run.source_instance_id
        or lineage_run.provider_key != run.provider_key
        or lineage_run.source_instance_id != run.source_instance_id
    ):
        raise NutritionProjectionLifecycleError(_MALFORMED_ANCESTRY_ERROR)


def _validate_event(
    db: Session, event: NutritionConsumptionEvent, *, run: NutritionIngestionRun
) -> None:
    observation = db.scalar(
        select(NutritionSourceObservation).where(
            NutritionSourceObservation.id == event.source_observation_id,
            NutritionSourceObservation.user_id == run.user_id,
        )
    )
    if (
        observation is None
        or event.user_id != run.user_id
        or event.provider_key != run.provider_key
        or event.source_instance_id != run.source_instance_id
    ):
        raise NutritionProjectionLifecycleError(_MALFORMED_ANCESTRY_ERROR)
    _validate_observation(db, observation, run=run)


def _add_event_dates(
    db: Session,
    event: NutritionConsumptionEvent,
    *,
    run: NutritionIngestionRun,
    dates: set[date],
) -> None:
    _validate_event(db, event, run=run)
    if event.local_date is not None:
        dates.add(event.local_date)
    if not isinstance(event.revision, int) or event.revision < 1:
        raise NutritionProjectionLifecycleError(_MALFORMED_ANCESTRY_ERROR)
    if event.supersedes_event_id is None:
        if event.supersedes_revision is not None or event.revision != 1:
            raise NutritionProjectionLifecycleError(_MALFORMED_ANCESTRY_ERROR)
    else:
        _load_ancestor(db, event, user_id=run.user_id, run=run, dates=dates)


def _load_ancestor(
    db: Session,
    event: NutritionConsumptionEvent,
    *,
    user_id: UUID,
    run: NutritionIngestionRun,
    dates: set[date],
) -> None:
    """Add every superseded event date, rejecting unsafe ancestry."""
    visited: set[UUID] = set()
    cursor = event
    while cursor.supersedes_event_id is not None or cursor.supersedes_revision is not None:
        if cursor.id in visited:
            raise NutritionProjectionLifecycleError(_MALFORMED_ANCESTRY_ERROR)
        visited.add(cursor.id)

        supersedes_event_id = cursor.supersedes_event_id
        supersedes_revision = cursor.supersedes_revision
        if (
            supersedes_event_id is None
            or supersedes_revision is None
            or cursor.logical_event_key is None
            or not isinstance(cursor.revision, int)
            or cursor.revision < 1
            or not isinstance(supersedes_revision, int)
            or supersedes_revision < 1
            or cursor.revision != supersedes_revision + 1
        ):
            raise NutritionProjectionLifecycleError(_MALFORMED_ANCESTRY_ERROR)

        ancestor = db.scalar(
            select(NutritionConsumptionEvent).where(
                NutritionConsumptionEvent.id == supersedes_event_id,
                NutritionConsumptionEvent.user_id == user_id,
                NutritionConsumptionEvent.provider_key == cursor.provider_key,
                NutritionConsumptionEvent.source_instance_id == cursor.source_instance_id,
                NutritionConsumptionEvent.logical_event_key == cursor.logical_event_key,
                NutritionConsumptionEvent.revision == supersedes_revision,
            )
        )
        if ancestor is None:
            raise NutritionProjectionLifecycleError(_MALFORMED_ANCESTRY_ERROR)
        if ancestor.id in visited:
            raise NutritionProjectionLifecycleError(_MALFORMED_ANCESTRY_ERROR)
        _validate_event(db, ancestor, run=run)
        if ancestor.local_date is not None:
            dates.add(ancestor.local_date)
        cursor = ancestor

    if cursor.revision != 1:
        raise NutritionProjectionLifecycleError(_MALFORMED_ANCESTRY_ERROR)


def resolve_affected_nutrition_dates(
    db: Session,
    *,
    user_id: UUID,
    ingestion_run_id: UUID,
) -> tuple[date, ...]:
    """Resolve dates represented by observations and events in one ingestion run.

    Dates come only from persisted observation/event local dates.  Event revisions
    also include every valid superseded ancestor date so a moved event rebuilds
    both its old and current day.
    """
    run = _load_run(db, user_id=user_id, ingestion_run_id=ingestion_run_id)
    dates: set[date] = set()
    observations = db.scalars(
        select(NutritionSourceObservation).where(
            NutritionSourceObservation.ingestion_run_id == ingestion_run_id,
            NutritionSourceObservation.user_id == user_id,
        )
    )
    for observation in observations:
        _validate_observation(db, observation, run=run)
        if observation.local_date is not None:
            dates.add(observation.local_date)

    events = db.scalars(
        select(NutritionConsumptionEvent)
        .join(
            NutritionSourceObservation,
            (NutritionConsumptionEvent.source_observation_id == NutritionSourceObservation.id)
            & (NutritionConsumptionEvent.user_id == NutritionSourceObservation.user_id),
        )
        .where(
            NutritionSourceObservation.ingestion_run_id == ingestion_run_id,
            NutritionSourceObservation.user_id == user_id,
            NutritionConsumptionEvent.user_id == user_id,
        )
    ).all()
    for event in events:
        _add_event_dates(db, event, run=run, dates=dates)

    return tuple(sorted(dates))


def resolve_replay_nutrition_dates(
    db: Session,
    *,
    user_id: UUID,
    ingestion_run_id: UUID,
) -> tuple[date, ...]:
    """Replay persisted range evidence and heads, plus the run's affected dates.

    Heads are user/date scoped and deliberately include READY projections:
    their evidence can have changed since the last successful projection.
    In-range evidence spans all providers/sources for this user, with each
    observation and event validated against its own ingestion-run lineage.
    """
    run = _load_run(db, user_id=user_id, ingestion_run_id=ingestion_run_id)
    start, end = run.requested_start_date, run.requested_end_date
    if start is None or end is None or start > end:
        raise NutritionProjectionLifecycleError("nutrition ingestion run requested range is invalid")
    dates = set(resolve_affected_nutrition_dates(
        db, user_id=user_id, ingestion_run_id=ingestion_run_id
    ))
    observations = db.scalars(
        select(NutritionSourceObservation).where(
            NutritionSourceObservation.user_id == user_id,
            NutritionSourceObservation.local_date.between(start, end),
        )
    )
    for observation in observations:
        if observation.local_date is None:
            continue
        observation_run = _load_run(
            db, user_id=user_id, ingestion_run_id=observation.ingestion_run_id
        )
        _validate_observation(db, observation, run=observation_run)
        dates.add(observation.local_date)

    events = db.scalars(
        select(NutritionConsumptionEvent).where(
            NutritionConsumptionEvent.user_id == user_id,
            NutritionConsumptionEvent.local_date.between(start, end),
        )
    )
    for event in events:
        if event.local_date is None:
            continue
        event_observation = db.scalar(
            select(NutritionSourceObservation).where(
                NutritionSourceObservation.id == event.source_observation_id,
                NutritionSourceObservation.user_id == user_id,
            )
        )
        if event_observation is None:
            raise NutritionProjectionLifecycleError(_MALFORMED_ANCESTRY_ERROR)
        event_run = _load_run(
            db, user_id=user_id, ingestion_run_id=event_observation.ingestion_run_id
        )
        # Historical evidence contributes only its in-range date. Validate its
        # ancestry without extending the range; only this run's affected-date
        # resolver contributes superseded dates outside the requested bounds.
        _add_event_dates(db, event, run=event_run, dates=set())
        dates.add(event.local_date)
    dates.update(db.scalars(
        select(NutritionProjectionHead.local_date).where(
            NutritionProjectionHead.user_id == user_id,
            NutritionProjectionHead.local_date.between(start, end),
        )
    ))
    return tuple(sorted(dates))


def rebuild_affected_nutrition_days(
    *,
    session_factory: Callable[[], Session],
    user_id: UUID,
    ingestion_run_id: UUID,
    policy_at: datetime,
) -> NutritionProjectionLifecycleResult:
    """Resolve and rebuild every affected nutrition date independently."""
    return _rebuild_nutrition_days(
        session_factory=session_factory, user_id=user_id, ingestion_run_id=ingestion_run_id,
        policy_at=policy_at, resolver=resolve_affected_nutrition_dates,
    )


def rebuild_replay_nutrition_days(
    *,
    session_factory: Callable[[], Session],
    user_id: UUID,
    ingestion_run_id: UUID,
    policy_at: datetime,
) -> NutritionProjectionLifecycleResult:
    """Rebuild every persisted replay date in independent clean sessions."""
    return _rebuild_nutrition_days(
        session_factory=session_factory, user_id=user_id, ingestion_run_id=ingestion_run_id,
        policy_at=policy_at, resolver=resolve_replay_nutrition_dates,
    )


def _rebuild_nutrition_days(
    *,
    session_factory: Callable[[], Session],
    user_id: UUID,
    ingestion_run_id: UUID,
    policy_at: datetime,
    resolver: Callable[..., tuple[date, ...]],
) -> NutritionProjectionLifecycleResult:
    if policy_at.tzinfo is None or policy_at.utcoffset() is None:
        raise ValueError("policy_at must be timezone-aware")

    resolver_session = session_factory()
    try:
        try:
            affected_dates = resolver(
                resolver_session,
                user_id=user_id,
                ingestion_run_id=ingestion_run_id,
            )
        except NutritionProjectionLifecycleError:
            raise
        except Exception:
            raise NutritionProjectionLifecycleError(
                f"{_RESOLUTION_ERROR} {ingestion_run_id}"
            ) from None
    finally:
        resolver_session.close()

    sorted_dates = tuple(sorted(set(affected_dates)))
    created_dates: list[date] = []
    unchanged_dates: list[date] = []
    policy_missing_dates: list[date] = []

    for local_date in sorted_dates:
        projection_session = session_factory()
        try:
            projection_result = rebuild_nutrition_day(
                projection_session,
                user_id=user_id,
                local_date=local_date,
                policy_at=policy_at,
            )
            status = projection_result.status
            if status == ProjectionPersistenceStatus.CREATED:
                created_dates.append(local_date)
            elif status == ProjectionPersistenceStatus.UNCHANGED:
                unchanged_dates.append(local_date)
            elif status == ProjectionPersistenceStatus.POLICY_MISSING:
                policy_missing_dates.append(local_date)
            else:
                raise NutritionProjectionLifecycleError(
                    f"{_REBUILD_ERROR} {ingestion_run_id} on {local_date.isoformat()}"
                )
        except NutritionProjectionLifecycleError:
            raise
        except Exception:
            raise NutritionProjectionLifecycleError(
                f"{_REBUILD_ERROR} {ingestion_run_id} on {local_date.isoformat()}"
            ) from None
        finally:
            projection_session.close()

    return NutritionProjectionLifecycleResult(
        user_id=user_id,
        ingestion_run_id=ingestion_run_id,
        affected_dates=sorted_dates,
        created_dates=tuple(created_dates),
        unchanged_dates=tuple(unchanged_dates),
        policy_missing_dates=tuple(policy_missing_dates),
    )
