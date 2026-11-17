"""Recurring Withings synchronization through the canonical domain service."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import User, WithingsConnection
from app.services.user_operation_lock import (
    UserOperationBusy,
    exclusive_user_lifecycle_operation,
)
from app.withings.constants import WITHINGS_REQUIRED_SCOPES
from app.withings.sync import WithingsSyncService

LOGGER = logging.getLogger(__name__)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _eligible(user: User | None, connection: WithingsConnection | None, now: datetime) -> bool:
    if user is None or not user.is_active or connection is None:
        return False
    if not (
        connection.state == "active"
        and connection.client_id
        and connection.client_id.strip()
        and connection.encrypted_client_secret
        and connection.encrypted_access_token
        and connection.encrypted_refresh_token
        and WITHINGS_REQUIRED_SCOPES.issubset(set(connection.granted_scopes or ()))
    ):
        return False
    return connection.last_attempt_at is None or _utc(connection.last_attempt_at) <= (
        now - timedelta(hours=settings.withings_sync_interval_hours)
    )


def _sleep_between_connections() -> None:
    time.sleep(1)


def run_due_withings_syncs(
    *,
    session_factory: Callable[[], Session] | None = None,
    sync_service: WithingsSyncService | None = None,
    now: datetime | None = None,
    between_connections: Callable[[], None] | None = None,
    should_continue: Callable[[], bool] | None = None,
) -> tuple[int, int]:
    """Return attempted/succeeded counts; partial-domain failures are not successes.

    The caller owns the scheduler leader epoch. Its continuation gate is checked
    before pacing, after pacing, and under the user lock before any attempt.
    Already-started canonical work finishes normally, preserving domain commits.
    """
    if not settings.withings_enabled or not settings.withings_scheduler_enabled:
        return 0, 0
    if should_continue is not None and not should_continue():
        return 0, 0
    if session_factory is None:
        from app.database import SessionLocal

        session_factory = SessionLocal
    service = sync_service or WithingsSyncService(session_factory=session_factory)
    timestamp = _utc(now or datetime.now(UTC))
    cutoff = timestamp - timedelta(hours=settings.withings_sync_interval_hours)
    with session_factory() as db:
        rows = db.execute(
            select(User, WithingsConnection)
            .join(WithingsConnection, WithingsConnection.user_id == User.id)
            .where(
                User.is_active.is_(True),
                WithingsConnection.state == "active",
                WithingsConnection.client_id.is_not(None),
                func.trim(WithingsConnection.client_id) != "",
                WithingsConnection.encrypted_client_secret.is_not(None),
                WithingsConnection.encrypted_access_token.is_not(None),
                WithingsConnection.encrypted_refresh_token.is_not(None),
                or_(
                    WithingsConnection.last_attempt_at.is_(None),
                    WithingsConnection.last_attempt_at <= cutoff,
                ),
            )
            .order_by(WithingsConnection.created_at, WithingsConnection.id)
        )
        candidates = [
            (connection.id, user.id)
            for user, connection in rows
            if _eligible(user, connection, timestamp)
        ]

    attempted = succeeded = 0
    pace = between_connections or _sleep_between_connections
    for index, (connection_id, user_id) in enumerate(candidates):
        if should_continue is not None and not should_continue():
            break
        if index:
            pace()
            if should_continue is not None and not should_continue():
                break
        if not settings.withings_enabled or not settings.withings_scheduler_enabled:
            break
        try:
            with session_factory() as db, exclusive_user_lifecycle_operation(
                db, user_id
            ):
                user = db.get(User, user_id, populate_existing=True)
                connection = db.get(WithingsConnection, connection_id, populate_existing=True)
                if (
                    user is None
                    or connection is None
                    or not _eligible(user, connection, timestamp)
                ):
                    continue
                if should_continue is not None and not should_continue():
                    break
                end_day = timestamp.astimezone(ZoneInfo(user.timezone)).date()
                start_day = end_day - timedelta(days=settings.withings_sync_days - 1)
                connection.last_attempt_at = timestamp
                db.commit()
                attempted += 1
                try:
                    result = service.sync(
                        user_id=user_id,
                        requested_start=start_day,
                        requested_end=end_day,
                        db=db,
                        _operation_locked=True,
                    )
                except Exception as exc:
                    db.rollback()
                    connection = db.get(
                        WithingsConnection, connection_id, populate_existing=True
                    )
                    if connection is not None:
                        connection.last_attempt_at = timestamp
                        connection.last_error_category = "provider_error"
                        connection.last_error = "provider_error"
                        db.commit()
                    LOGGER.warning(
                        "withings.periodic.sync_failed exception_type=%s", type(exc).__name__
                    )
                else:
                    if result.status in {"success", "no_data"}:
                        succeeded += 1
        except UserOperationBusy:
            continue
        except Exception as exc:
            LOGGER.warning(
                "withings.periodic.sync_failed exception_type=%s", type(exc).__name__
            )
    return attempted, succeeded
