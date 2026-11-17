from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import User, WithingsConnection
from app.services.user_operation_lock import (
    InactiveUserOperation,
    UserOperationBusy,
    exclusive_user_lifecycle_operation,
)
from app.withings.constants import WITHINGS_REQUIRED_SCOPES
from app.withings.sync import WithingsSyncResult, WithingsSyncService

LOGGER = logging.getLogger(__name__)
WITHINGS_INTER_ACCOUNT_DELAY_SECONDS = 1.0

SessionFactory = Callable[[], Session]


class _SyncService(Protocol):
    def sync(
        self,
        *,
        user_id: UUID,
        requested_start: date,
        requested_end: date,
        db: Session,
        _operation_locked: bool,
    ) -> WithingsSyncResult: ...


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _connected(connection: WithingsConnection) -> bool:
    return (
        connection.state == "active"
        and bool(connection.client_id and connection.client_id.strip())
        and bool(connection.encrypted_client_secret)
        and bool(connection.encrypted_access_token)
        and bool(connection.encrypted_refresh_token)
        and WITHINGS_REQUIRED_SCOPES.issubset(set(connection.granted_scopes or ()))
    )


def _due(connection: WithingsConnection, *, cutoff: datetime) -> bool:
    last_attempt = connection.last_attempt_at
    return last_attempt is None or _as_utc(last_attempt) <= cutoff


def _ordered_connections(session_factory: SessionFactory, *, cutoff: datetime) -> list[UUID]:
    with session_factory() as db:
        connections = list(
            db.scalars(
                select(WithingsConnection)
                .join(User, User.id == WithingsConnection.user_id)
                .where(
                    User.is_active.is_(True),
                    WithingsConnection.state == "active",
                )
            )
        )
    eligible = [
        (connection.user_id, connection.last_attempt_at)
        for connection in connections
        if _connected(connection) and _due(connection, cutoff=cutoff)
    ]
    eligible.sort(
        key=lambda item: (
            item[1] is not None,
            _as_utc(item[1]) if item[1] is not None else datetime.min.replace(tzinfo=UTC),
            str(item[0]),
        )
    )
    return [user_id for user_id, _last_attempt_at in eligible]


def _sync_one(
    user_id: UUID,
    *,
    session_factory: SessionFactory,
    sync_service: _SyncService,
    now: datetime,
    cutoff: datetime,
) -> WithingsSyncResult | None:
    with session_factory() as db, exclusive_user_lifecycle_operation(db, user_id):
        user = db.scalar(select(User).where(User.id == user_id))
        connection = db.scalar(
            select(WithingsConnection).where(WithingsConnection.user_id == user_id)
        )
        if (
            user is None
            or not user.is_active
            or connection is None
            or not _connected(connection)
            or not _due(connection, cutoff=cutoff)
        ):
            return None

        connection.last_attempt_at = now
        db.commit()

        end = now.astimezone(ZoneInfo(user.timezone)).date()
        start = end - timedelta(days=settings.withings_sync_days - 1)
        return sync_service.sync(
            user_id=user.id,
            requested_start=start,
            requested_end=end,
            db=db,
            _operation_locked=True,
        )


def run_due_withings_syncs(
    *,
    session_factory: SessionFactory | None = None,
    sync_service: _SyncService | None = None,
    now: datetime | None = None,
    between_connections: Callable[[], None] | None = None,
) -> tuple[int, int]:
    """Run due accounts through the canonical, separately committed Withings sync."""
    if not settings.withings_enabled or not settings.withings_scheduler_enabled:
        return 0, 0

    factory = session_factory or SessionLocal
    service = sync_service or WithingsSyncService(session_factory=factory)
    cycle_now = _as_utc(now or datetime.now(UTC))
    cutoff = cycle_now - timedelta(hours=settings.withings_sync_interval_hours)
    user_ids = _ordered_connections(factory, cutoff=cutoff)
    attempted = succeeded = 0

    for index, user_id in enumerate(user_ids):
        if index:
            if between_connections is None:
                time.sleep(WITHINGS_INTER_ACCOUNT_DELAY_SECONDS)
            else:
                between_connections()
        try:
            result = _sync_one(
                user_id,
                session_factory=factory,
                sync_service=service,
                now=cycle_now,
                cutoff=cutoff,
            )
        except (InactiveUserOperation, UserOperationBusy):
            continue
        except Exception as exc:
            attempted += 1
            LOGGER.warning(
                "withings_scheduler_account_failed error_type=%s",
                type(exc).__name__,
            )
            continue
        if result is None:
            continue
        attempted += 1
        if result.status in {"success", "no_data"}:
            succeeded += 1
    return attempted, succeeded


__all__ = ["run_due_withings_syncs"]
