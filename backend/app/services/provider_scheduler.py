from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from typing import Protocol

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import Session

from app.auth.security import purge_expired_sessions
from app.config import settings
from app.database import SessionLocal
from app.services.account_recovery import purge_account_recovery_tokens
from app.services.passkeys import purge_expired_webauthn_challenges
from app.services.rate_limit import purge_expired_rate_limit_buckets
from app.services.security_audit import purge_expired_security_audit_events
from app.services.user_operation_lock import advisory_lock_id
from app.services.withings_scheduler import run_due_withings_syncs
from app.services.yazio_sync import (
    YAZIO_INTER_ACCOUNT_DELAY_SECONDS,
    run_due_yazio_syncs,
)

WITHINGS_INTER_ACCOUNT_DELAY_SECONDS = 1.0
INTER_ACCOUNT_DELAY_SECONDS_BY_PROVIDER = {
    "yazio": YAZIO_INTER_ACCOUNT_DELAY_SECONDS,
    "withings": WITHINGS_INTER_ACCOUNT_DELAY_SECONDS,
}

LOGGER = logging.getLogger(__name__)
LEADER_RETRY_SECONDS = 5.0
LEADERSHIP_CHECK_SECONDS = 1.0
SECURITY_CLEANUP_INTERVAL_SECONDS = 3600.0
INTER_ACCOUNT_DELAY_SECONDS = 1.0
SCHEDULER_LOCK_ID = advisory_lock_id("provider-scheduler", "singleton")



class _Leadership(Protocol):
    def check(self) -> bool: ...

    def close(self) -> None: ...

ProviderRunner = Callable[..., tuple[int, int]]
EngineFactory = Callable[[], Engine]
LeadershipFactory = Callable[[Engine], _Leadership | None]
MaintenanceRunner = Callable[[], dict[str, int]]

def _create_scheduler_engine(database_url: str) -> Engine:
    # Keep the session-level leader lock out of the request engine's pool.
    return create_engine(
        database_url,
        pool_size=1,
        max_overflow=0,
        pool_timeout=5,
        pool_pre_ping=False,
        hide_parameters=True,
    )


class SchedulerLeadership:
    """Own a PostgreSQL session-level advisory lock on one dedicated connection."""

    def __init__(self, connection: Connection, lock_id: int) -> None:
        self._connection = connection
        self._lock_id = lock_id
        self._guard = threading.Lock()
        self._lost = False
        self._closed = False

    def check(self) -> bool:
        with self._guard:
            if self._lost or self._closed:
                return False
            try:
                self._connection.execute(text("SELECT 1")).scalar_one()
                self._connection.commit()
            except Exception as exc:
                self._lost = True
                self._invalidate_connection()
                LOGGER.warning(
                    "provider_scheduler_leadership_lost error_type=%s",
                    type(exc).__name__,
                )
                return False
            return True

    def close(self) -> None:
        with self._guard:
            if self._closed:
                return
            self._closed = True
            if not self._lost:
                try:
                    released = bool(
                        self._connection.scalar(
                            text("SELECT pg_advisory_unlock(:lock_id)"),
                            {"lock_id": self._lock_id},
                        )
                    )
                    self._connection.commit()
                    if not released:
                        self._lost = True
                        self._invalidate_connection()
                        LOGGER.warning("provider_scheduler_leadership_unlock_missing")
                except Exception as exc:
                    self._lost = True
                    self._invalidate_connection()
                    LOGGER.warning(
                        "provider_scheduler_leadership_release_failed error_type=%s",
                        type(exc).__name__,
                    )
            else:
                self._invalidate_connection()
            try:
                self._connection.close()
            except Exception as exc:
                LOGGER.warning(
                    "provider_scheduler_leadership_connection_close_failed error_type=%s",
                    type(exc).__name__,
                )

    def _invalidate_connection(self) -> None:
        with suppress(Exception):
            self._connection.invalidate()


def acquire_scheduler_leadership(engine: Engine) -> SchedulerLeadership | None:
    """Try one database-scoped lock; a returned handle owns its checked-out session."""
    connection = engine.connect()
    try:
        acquired = bool(
            connection.scalar(
                text("SELECT pg_try_advisory_lock(:lock_id)"),
                {"lock_id": SCHEDULER_LOCK_ID},
            )
        )
        connection.commit()
        if not acquired:
            connection.close()
            return None
        return SchedulerLeadership(connection, SCHEDULER_LOCK_ID)
    except Exception:
        try:
            connection.invalidate()
        finally:
            connection.close()
        raise


def run_scheduler_housekeeping(
    *, session_factory: Callable[[], Session] = SessionLocal,
) -> dict[str, int]:
    """Run the hourly cleanup previously owned by the YAZIO scheduler process."""
    with session_factory() as db:
        deleted = {
            "rate_limit_buckets": purge_expired_rate_limit_buckets(
                db, settings.rate_limit_retention_hours
            ),
            "recovery_tokens": purge_account_recovery_tokens(db),
            "sessions": purge_expired_sessions(db),
            "webauthn_challenges": purge_expired_webauthn_challenges(db),
            "security_audit_events": purge_expired_security_audit_events(db),
        }
        db.commit()
    return deleted


class ProviderSchedulerSupervisor:
    """Lifespan-owned scheduler leader with independent provider polling lanes."""

    def __init__(
        self,
        *,
        database_url: str | None = None,
        engine_factory: EngineFactory | None = None,
        leadership_factory: LeadershipFactory = acquire_scheduler_leadership,
        yazio_runner: ProviderRunner = run_due_yazio_syncs,
        withings_runner: ProviderRunner = run_due_withings_syncs,
        maintenance_runner: MaintenanceRunner = run_scheduler_housekeeping,
        leader_retry_seconds: float = LEADER_RETRY_SECONDS,
        leadership_check_seconds: float = LEADERSHIP_CHECK_SECONDS,
    ) -> None:
        self._database_url = database_url or settings.database_url
        self._engine_factory = engine_factory or (
            lambda: _create_scheduler_engine(self._database_url)
        )
        self._leadership_factory = leadership_factory
        self._yazio_runner = yazio_runner
        self._withings_runner = withings_runner
        self._maintenance_runner = maintenance_runner
        self._leader_retry_seconds = leader_retry_seconds
        self._leadership_check_seconds = leadership_check_seconds
        self._stop_event = threading.Event()
        self._epoch_guard = threading.Lock()
        self._epoch_cancel: threading.Event | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        try:
            backend = make_url(self._database_url).get_backend_name()
        except Exception as exc:
            LOGGER.warning(
                "provider_scheduler_not_started error_type=%s",
                type(exc).__name__,
            )
            return
        if backend != "postgresql":
            LOGGER.info("provider_scheduler_not_started reason=postgresql_required")
            return
        thread = threading.Thread(
            target=self._run,
            name="calograph-provider-scheduler-supervisor",
            daemon=True,
        )
        self._thread = thread
        try:
            thread.start()
        except Exception as exc:
            self._thread = None
            LOGGER.warning(
                "provider_scheduler_thread_start_failed error_type=%s",
                type(exc).__name__,
            )

    def stop(self) -> None:
        self._stop_event.set()
        with self._epoch_guard:
            if self._epoch_cancel is not None:
                self._epoch_cancel.set()

    def join(self, timeout: float | None = None) -> bool:
        thread = self._thread
        if thread is None:
            return True
        if thread is threading.current_thread():
            return False
        thread.join(timeout)
        return not thread.is_alive()

    def _run(self) -> None:
        engine: Engine | None = None
        try:
            engine = self._engine_factory()
            if engine.dialect.name != "postgresql":
                LOGGER.info("provider_scheduler_not_started reason=postgresql_required")
                return
            while not self._stop_event.is_set():
                leadership: _Leadership | None = None
                epoch_cancel: threading.Event | None = None
                try:
                    leadership = self._leadership_factory(engine)
                except Exception as exc:
                    LOGGER.warning(
                        "provider_scheduler_leadership_attempt_failed error_type=%s",
                        type(exc).__name__,
                    )
                if leadership is None:
                    self._stop_event.wait(self._leader_retry_seconds)
                    continue
                epoch_cancel = threading.Event()
                with self._epoch_guard:
                    self._epoch_cancel = epoch_cancel
                    if self._stop_event.is_set():
                        epoch_cancel.set()
                if epoch_cancel.is_set():
                    leadership.close()
                    self._clear_epoch(epoch_cancel)
                    break
                LOGGER.info("provider_scheduler_leader_acquired")
                try:
                    self._serve_leader(leadership, epoch_cancel)
                except Exception as exc:
                    LOGGER.warning(
                        "provider_scheduler_iteration_failed error_type=%s",
                        type(exc).__name__,
                    )
                finally:
                    epoch_cancel.set()
                    leadership.close()
                    self._clear_epoch(epoch_cancel)
                    LOGGER.info("provider_scheduler_leader_released")
                if not self._stop_event.is_set():
                    self._stop_event.wait(self._leader_retry_seconds)
        except Exception as exc:
            LOGGER.warning(
                "provider_scheduler_supervisor_failed error_type=%s",
                type(exc).__name__,
            )
        finally:
            if engine is not None:
                try:
                    engine.dispose()
                except Exception as exc:
                    LOGGER.warning(
                        "provider_scheduler_engine_dispose_failed error_type=%s",
                        type(exc).__name__,
                    )

    def _serve_leader(
        self,
        leadership: _Leadership,
        epoch_cancel: threading.Event,
    ) -> None:
        lanes: list[threading.Thread] = []
        try:
            if settings.yazio_enabled and settings.yazio_scheduler_enabled:
                lanes.append(
                    threading.Thread(
                        target=self._run_provider_lane,
                        name="calograph-yazio-scheduler-lane",
                        daemon=True,
                        args=(
                            "yazio",
                            self._yazio_runner,
                            settings.yazio_scheduler_poll_seconds,
                            leadership,
                            epoch_cancel,
                        ),
                    )
                )
            if settings.withings_enabled and settings.withings_scheduler_enabled:
                lanes.append(
                    threading.Thread(
                        target=self._run_provider_lane,
                        name="calograph-withings-scheduler-lane",
                        daemon=True,
                        args=(
                            "withings",
                            self._withings_runner,
                            settings.withings_scheduler_poll_seconds,
                            leadership,
                            epoch_cancel,
                        ),
                    )
                )
            for lane in lanes:
                lane.start()

            next_cleanup = 0.0
            while not self._stop_event.is_set() and not epoch_cancel.is_set():
                if not leadership.check():
                    epoch_cancel.set()
                    break
                now = time.monotonic()
                if now >= next_cleanup:
                    if self._can_continue(leadership, epoch_cancel):
                        self._run_maintenance()
                    next_cleanup = time.monotonic() + SECURITY_CLEANUP_INTERVAL_SECONDS
                if self._stop_event.wait(self._leadership_check_seconds):
                    break
        finally:
            epoch_cancel.set()
            for lane in lanes:
                if lane.is_alive():
                    lane.join()

    def _run_provider_lane(
        self,
        provider: str,
        runner: ProviderRunner,
        poll_seconds: int,
        leadership: _Leadership,
        epoch_cancel: threading.Event,
    ) -> None:
        def should_continue() -> bool:
            if self._stop_event.is_set() or epoch_cancel.is_set():
                return False
            if not leadership.check():
                epoch_cancel.set()
                return False
            return not self._stop_event.is_set() and not epoch_cancel.is_set()

        while should_continue():
            try:
                attempted, succeeded = runner(
                    should_continue=should_continue,
                    between_connections=lambda: epoch_cancel.wait(
                        INTER_ACCOUNT_DELAY_SECONDS_BY_PROVIDER[provider]
                    ),
                )
            except Exception as exc:
                LOGGER.warning(
                    "provider_scheduler_provider_failed provider=%s error_type=%s",
                    provider,
                    type(exc).__name__,
                )
            else:
                if attempted:
                    LOGGER.info(
                        "provider_scheduler_cycle provider=%s attempted=%s succeeded=%s",
                        provider,
                        attempted,
                        succeeded,
                    )
            if epoch_cancel.wait(poll_seconds):
                return

    def _can_continue(
        self,
        leadership: _Leadership,
        epoch_cancel: threading.Event,
    ) -> bool:
        if self._stop_event.is_set() or epoch_cancel.is_set():
            return False
        if not leadership.check():
            epoch_cancel.set()
            return False
        return not self._stop_event.is_set() and not epoch_cancel.is_set()

    def _run_maintenance(self) -> None:
        try:
            deleted = self._maintenance_runner()
        except Exception as exc:
            LOGGER.warning(
                "provider_scheduler_maintenance_failed error_type=%s",
                type(exc).__name__,
            )
            return
        for category, count in deleted.items():
            if count:
                LOGGER.info("scheduler_maintenance_cleanup category=%s deleted=%s", category, count)

    def _clear_epoch(self, epoch_cancel: threading.Event) -> None:
        with self._epoch_guard:
            if self._epoch_cancel is epoch_cancel:
                self._epoch_cancel = None
