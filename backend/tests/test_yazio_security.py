import io
import json
import subprocess
from datetime import date

import pytest

from app.config import settings
from app.schemas import ImportSummary
from app.services import yazio_sync, yazio_transport
from app.services.yazio_guard import YazioOperationBusy, yazio_operation_slot
from app.services.yazio_sync import YazioCircuitOpen, YazioSyncError
from app.services.yazio_transport import (
    YazioTransportAuthenticationError,
    YazioTransportDeadlineError,
    YazioTransportError,
    _BoundedYazioClient,
    _login,
    _run_worker,
    _TransportOptions,
    validate_yazio_credentials_transport,
)


class _CapturedInput(io.BytesIO):
    value = b""

    def close(self) -> None:
        self.value = self.getvalue()
        super().close()


class _FakeProcess:
    def __init__(self, output: bytes, *, times_out: bool = False) -> None:
        self.stdin = _CapturedInput()
        self.stdout = io.BytesIO(output)
        self.times_out = times_out
        self.killed = False
        self.wait_timeouts: list[int | None] = []

    def wait(self, timeout: int | None = None) -> int:
        self.wait_timeouts.append(timeout)
        if self.times_out and not self.killed:
            raise subprocess.TimeoutExpired(cmd=["python"], timeout=timeout or 0)
        return 0

    def kill(self) -> None:
        self.killed = True


def test_login_uses_explicit_timeout_without_redirects_or_retries(monkeypatch) -> None:
    client = _BoundedYazioClient(
        _TransportOptions(
            connect_timeout=3.05,
            read_timeout=15,
            request_workers=3,
        )
    )
    calls: list[dict[str, object]] = []

    class Response:
        status_code = 200

        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict[str, str]:
            return {"access_token": "token"}

    def fake_post(_url, **kwargs):
        calls.append(kwargs)
        return Response()

    monkeypatch.setattr(client.session, "post", fake_post)
    try:
        assert _login(client, "owner@example.com", "password") == "token"
    finally:
        client.session.close()

    assert len(calls) == 1
    assert calls[0]["timeout"] == (3.05, 15)
    assert calls[0]["allow_redirects"] is False


def test_transport_deadline_kills_the_isolated_worker(monkeypatch) -> None:
    process = _FakeProcess(b"", times_out=True)

    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)

    with pytest.raises(YazioTransportDeadlineError):
        validate_yazio_credentials_transport("owner@example.com", "password")

    assert process.killed is True
    assert process.wait_timeouts == [settings.yazio_login_deadline_seconds, None]


def test_transport_keeps_credentials_out_of_process_arguments(monkeypatch) -> None:
    captured: dict[str, object] = {}
    process = _FakeProcess(b'{"ok":false,"kind":"authentication"}')

    def start_process(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return process

    monkeypatch.setattr(subprocess, "Popen", start_process)

    with pytest.raises(YazioTransportAuthenticationError):
        validate_yazio_credentials_transport("owner@example.com", "very-secret")

    assert "owner@example.com" not in " ".join(captured["command"])
    assert "very-secret" not in " ".join(captured["command"])
    worker_input = json.loads(process.stdin.value)
    assert worker_input["password"] == "very-secret"
    assert process.wait_timeouts == [settings.yazio_login_deadline_seconds]


def test_transport_stops_reading_worker_output_at_the_configured_limit(
    monkeypatch,
) -> None:
    monkeypatch.setattr(yazio_transport, "MAX_WORKER_OUTPUT_BYTES", 32)
    process = _FakeProcess(b"x" * 64)
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)

    with pytest.raises(YazioTransportError, match="output is too large"):
        _run_worker({"operation": "invalid"}, deadline_seconds=5)

    assert process.killed is True


def test_isolated_transport_worker_can_be_started() -> None:
    with pytest.raises(YazioTransportError):
        _run_worker({"operation": "invalid"}, deadline_seconds=5)


def test_local_yazio_slots_are_per_user_and_globally_bounded(monkeypatch) -> None:
    monkeypatch.setattr(settings, "yazio_max_parallel_operations", 2)

    with yazio_operation_slot("user-1"):
        with pytest.raises(YazioOperationBusy), yazio_operation_slot("user-1"):
            pass
        with (
            yazio_operation_slot("user-2"),
            pytest.raises(YazioOperationBusy),
            yazio_operation_slot("user-3"),
        ):
            pass

    with yazio_operation_slot("user-3"):
        pass


def test_user_slot_remains_held_while_yazio_payload_is_persisted(
    user,
    monkeypatch,
) -> None:
    persisted = False

    def persist_while_locked(*_args, **_kwargs):
        nonlocal persisted
        with pytest.raises(YazioOperationBusy), yazio_operation_slot(user.id):
            pass
        persisted = True
        return ImportSummary(
            status="completed",
            received=0,
            inserted=0,
            updated=0,
            skipped=0,
        )

    monkeypatch.setattr(yazio_sync, "import_yazio_payload", persist_while_locked)

    summary = yazio_sync.sync_yazio_user(
        user,
        "owner@example.com",
        "password",
        date(2026, 7, 28),
        date(2026, 7, 28),
        fetcher=lambda *_args: {},
    )

    assert persisted is True
    assert summary.status == "completed"


def test_provider_failures_open_the_shared_circuit(db, monkeypatch) -> None:
    del db
    monkeypatch.setattr(settings, "yazio_circuit_failure_limit", 2)
    monkeypatch.setattr(settings, "yazio_circuit_window_seconds", 600)
    calls = 0

    def provider_failure(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise yazio_sync.YazioTransportError("provider unavailable")

    monkeypatch.setattr(yazio_sync, "fetch_yazio_payload_transport", provider_failure)

    for _ in range(2):
        with pytest.raises(YazioSyncError):
            yazio_sync.fetch_yazio_payload(
                "owner@example.com",
                "password",
                date(2026, 7, 28),
                date(2026, 7, 28),
                False,
                operation_key="user-id",
            )

    with pytest.raises(YazioCircuitOpen):
        yazio_sync.fetch_yazio_payload(
            "owner@example.com",
            "password",
            date(2026, 7, 28),
            date(2026, 7, 28),
            False,
            operation_key="user-id",
        )
    assert calls == 2
