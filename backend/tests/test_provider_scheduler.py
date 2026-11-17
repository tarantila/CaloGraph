from __future__ import annotations

import logging
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.services.provider_scheduler import ProviderSchedulerSupervisor


class FakeLease:
    def __init__(self) -> None:
        self.closed = threading.Event()
        self.valid = True

    def check(self) -> bool:
        return self.valid

    def close(self) -> None:
        self.closed.set()


class FakeEngine:
    dialect = SimpleNamespace(name="postgresql")

    def __init__(self) -> None:
        self.disposed = False

    def dispose(self) -> None:
        self.disposed = True


def _configure_providers(monkeypatch, *, yazio: bool, withings: bool) -> None:
    monkeypatch.setattr(settings, "yazio_enabled", yazio)
    monkeypatch.setattr(settings, "yazio_scheduler_enabled", yazio)
    monkeypatch.setattr(settings, "yazio_scheduler_poll_seconds", 1)
    monkeypatch.setattr(settings, "withings_enabled", withings)
    monkeypatch.setattr(settings, "withings_scheduler_enabled", withings)
    monkeypatch.setattr(settings, "withings_scheduler_poll_seconds", 1, raising=False)


def _supervisor(
    lease: FakeLease,
    *,
    yazio_runner,
    withings_runner,
    engine: FakeEngine | None = None,
    maintenance_runner=lambda: {},
) -> ProviderSchedulerSupervisor:
    return ProviderSchedulerSupervisor(
        database_url="postgresql+psycopg://scheduler-test",
        engine_factory=lambda: engine or FakeEngine(),
        leadership_factory=lambda _engine: lease,
        yazio_runner=yazio_runner,
        withings_runner=withings_runner,
        maintenance_runner=maintenance_runner,
        leader_retry_seconds=0.01,
        leadership_check_seconds=0.01,
    )


@pytest.mark.parametrize(
    ("yazio", "withings", "expected"),
    [
        (True, False, {"yazio"}),
        (False, True, {"withings"}),
        (True, True, {"yazio", "withings"}),
        (False, False, set()),
    ],
)
def test_provider_lanes_follow_independent_enablement(
    monkeypatch, yazio: bool, withings: bool, expected: set[str]
) -> None:
    _configure_providers(monkeypatch, yazio=yazio, withings=withings)
    lease = FakeLease()
    calls: set[str] = set()
    calls_lock = threading.Lock()

    def runner(name: str):
        def run(*, should_continue, between_connections):
            del between_connections
            if should_continue():
                with calls_lock:
                    calls.add(name)
            return (0, 0)

        return run

    supervisor = _supervisor(
        lease,
        yazio_runner=runner("yazio"),
        withings_runner=runner("withings"),
    )
    supervisor.start()
    try:
        deadline = time.monotonic() + 1
        while calls != expected and expected and time.monotonic() < deadline:
            time.sleep(0.005)
        if not expected:
            time.sleep(0.03)
        assert calls == expected
    finally:
        supervisor.stop()
        assert supervisor.join(timeout=1)
    assert lease.closed.is_set()



@pytest.mark.parametrize(("provider", "expected_delay"), [("yazio", 90), ("withings", 1)])
def test_provider_lanes_keep_provider_specific_inter_account_pacing(
    provider: str, expected_delay: int
) -> None:
    delays: list[float] = []

    class FakeCancellation:
        def is_set(self) -> bool:
            return False

        def wait(self, timeout: float) -> bool:
            delays.append(timeout)
            return True

    def runner(*, should_continue, between_connections):
        assert should_continue()
        between_connections()
        return 1, 1

    supervisor = ProviderSchedulerSupervisor(
        database_url="postgresql+psycopg://scheduler-test",
        engine_factory=FakeEngine,
        leadership_factory=lambda _engine: None,
    )
    supervisor._run_provider_lane(
        provider,
        runner,
        999,
        FakeLease(),
        FakeCancellation(),
    )

    assert delays == [expected_delay, 999]

def test_provider_failure_does_not_block_other_lane_or_log_provider_body(
    monkeypatch, caplog
) -> None:
    _configure_providers(monkeypatch, yazio=True, withings=True)
    lease = FakeLease()
    withings_ran = threading.Event()

    def fail_yazio(*, should_continue, between_connections):
        del should_continue, between_connections
        raise RuntimeError("private provider response body")

    def run_withings(*, should_continue, between_connections):
        del between_connections
        if should_continue():
            withings_ran.set()
        return (1, 1)

    supervisor = _supervisor(
        lease,
        yazio_runner=fail_yazio,
        withings_runner=run_withings,
    )
    with caplog.at_level(logging.WARNING, logger="app.services.provider_scheduler"):
        supervisor.start()
        try:
            assert withings_ran.wait(timeout=1)
        finally:
            supervisor.stop()
            assert supervisor.join(timeout=1)

    assert "provider_scheduler_provider_failed" in caplog.text
    assert "error_type=RuntimeError" in caplog.text
    assert "private provider response body" not in caplog.text


def test_shutdown_does_not_release_leadership_until_active_account_drains(monkeypatch) -> None:
    _configure_providers(monkeypatch, yazio=True, withings=False)
    lease = FakeLease()
    account_started = threading.Event()
    finish_account = threading.Event()

    def blocking_yazio(*, should_continue, between_connections):
        del between_connections
        assert should_continue()
        account_started.set()
        finish_account.wait(timeout=2)
        return (1, 1)

    supervisor = _supervisor(
        lease,
        yazio_runner=blocking_yazio,
        withings_runner=lambda **_kwargs: (0, 0),
    )
    supervisor.start()
    assert account_started.wait(timeout=1)
    supervisor.stop()
    assert not supervisor.join(timeout=0.02)
    assert not lease.closed.is_set()

    finish_account.set()
    assert supervisor.join(timeout=1)
    assert lease.closed.is_set()


def test_backend_lifespan_keeps_http_responsive_while_scheduler_runs(monkeypatch) -> None:
    import app.main as main

    scheduler_running = threading.Event()
    release_scheduler = threading.Event()
    scheduler_stopped = threading.Event()

    class BlockingScheduler:
        def start(self) -> None:
            def work() -> None:
                scheduler_running.set()
                release_scheduler.wait(timeout=2)

            self.worker = threading.Thread(target=work, daemon=True)
            self.worker.start()

        def stop(self) -> None:
            release_scheduler.set()
            scheduler_stopped.set()

        def join(self, timeout: float | None = None) -> bool:
            self.worker.join(timeout)
            return not self.worker.is_alive()

    scheduler = BlockingScheduler()
    monkeypatch.setattr(main, "ProviderSchedulerSupervisor", lambda: scheduler)

    with TestClient(main.app) as client:
        assert scheduler_running.wait(timeout=1)
        response = client.get("/health/live")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    assert scheduler_stopped.is_set()
    assert not scheduler.worker.is_alive()


def test_shutdown_stops_lane_after_provider_failure_and_leaves_no_worker_thread(monkeypatch) -> None:
    _configure_providers(monkeypatch, yazio=True, withings=True)
    lease = FakeLease()
    withings_ran = threading.Event()

    def fail_yazio(*, should_continue, between_connections):
        del should_continue, between_connections
        raise OSError("synthetic provider failure")

    def run_withings(*, should_continue, between_connections):
        del between_connections
        if should_continue():
            withings_ran.set()
        return (0, 0)

    supervisor = _supervisor(
        lease,
        yazio_runner=fail_yazio,
        withings_runner=run_withings,
    )
    supervisor.start()
    assert withings_ran.wait(timeout=1)
    supervisor.stop()
    assert supervisor.join(timeout=1)
    assert lease.closed.is_set()
