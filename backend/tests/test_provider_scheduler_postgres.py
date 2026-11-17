from __future__ import annotations

import multiprocessing
import os
import threading
import time
from urllib.parse import urlsplit

import pytest
from sqlalchemy import create_engine

from app.config import settings
from app.services.provider_scheduler import (
    ProviderSchedulerSupervisor,
    acquire_scheduler_leadership,
)


def _require_isolated_postgres_url() -> str:
    url = os.environ.get("CALOGRAPH_POSTGRES_TEST_URL")
    if not url:
        pytest.skip("isolated PostgreSQL test URL is not configured")
    if os.environ.get("CALOGRAPH_ALLOW_DESTRUCTIVE_POSTGRES_TESTS") != "1":
        pytest.skip("explicit PostgreSQL test opt-in is not configured")
    database = urlsplit(url).path.lstrip("/")
    if not database.endswith("_test"):
        pytest.fail("scheduler leadership tests require a database name ending in _test")
    return url


def _hold_scheduler_lock_until_terminated(database_url: str, ready) -> None:
    engine = create_engine(database_url, pool_size=1, max_overflow=0)
    leadership = acquire_scheduler_leadership(engine)
    ready.put(leadership is not None)
    if leadership is None:
        engine.dispose()
        return
    while True:
        time.sleep(0.1)


def test_scheduler_workers_share_one_postgresql_leader_and_drain_before_handover(
    monkeypatch,
) -> None:
    database_url = _require_isolated_postgres_url()
    monkeypatch.setattr(settings, "yazio_enabled", True)
    monkeypatch.setattr(settings, "yazio_scheduler_enabled", True)
    monkeypatch.setattr(settings, "yazio_scheduler_poll_seconds", 10)
    monkeypatch.setattr(settings, "withings_enabled", False)
    monkeypatch.setattr(settings, "withings_scheduler_enabled", False)

    active = 0
    max_active = 0
    call_count = 0
    state_lock = threading.Lock()
    account_started = threading.Event()
    finish_account = threading.Event()

    def runner(*, should_continue, between_connections):
        nonlocal active, max_active, call_count
        del between_connections
        if not should_continue():
            return (0, 0)
        with state_lock:
            active += 1
            call_count += 1
            max_active = max(max_active, active)
        account_started.set()
        finish_account.wait(timeout=5)
        with state_lock:
            active -= 1
        return (1, 1)

    supervisors = [
        ProviderSchedulerSupervisor(
            database_url=database_url,
            yazio_runner=runner,
            withings_runner=lambda **_kwargs: (0, 0),
            maintenance_runner=lambda: {},
            leader_retry_seconds=0.02,
            leadership_check_seconds=0.01,
        )
        for _ in range(2)
    ]
    for supervisor in supervisors:
        supervisor.start()
    try:
        assert account_started.wait(timeout=10)
        time.sleep(0.1)
        with state_lock:
            assert call_count == 1
            assert max_active == 1
    finally:
        for supervisor in supervisors:
            supervisor.stop()
        finish_account.set()
        for supervisor in supervisors:
            assert supervisor.join(timeout=10)

    with state_lock:
        assert active == 0
        assert max_active == 1


def test_postgresql_leader_process_death_releases_lock_for_takeover() -> None:
    database_url = _require_isolated_postgres_url()
    context = multiprocessing.get_context("spawn")
    ready = context.Queue()
    process = context.Process(
        target=_hold_scheduler_lock_until_terminated,
        args=(database_url, ready),
    )
    engine = create_engine(database_url, pool_size=1, max_overflow=0)
    process.start()
    try:
        assert ready.get(timeout=15) is True
        assert acquire_scheduler_leadership(engine) is None
        process.terminate()
        process.join(timeout=15)
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
        assert not process.is_alive()

        leadership = acquire_scheduler_leadership(engine)
        assert leadership is not None
        leadership.close()
    finally:
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
        engine.dispose()
        ready.close()
        ready.join_thread()
