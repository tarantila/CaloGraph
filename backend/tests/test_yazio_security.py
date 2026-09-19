import io
import json
import subprocess
from datetime import date
from typing import ClassVar

import httpx
import pytest

from app.config import settings
from app.schemas import ImportSummary
from app.security_events import log_security_event
from app.services import yazio_sdk_provider, yazio_sync, yazio_transport
from app.services.yazio_guard import YazioOperationBusy, yazio_operation_slot
from app.services.yazio_provider import (
    YazioProviderInvalidResponseError,
    YazioProviderMetadata,
    YazioProviderNetworkTimeoutError,
    YazioProviderRateLimitedError,
    YazioProviderResult,
    YazioProviderUnavailableError,
)
from app.services.yazio_sync import (
    YazioCircuitOpen,
    YazioInvalidResponseError,
    YazioSyncError,
)
from app.services.yazio_transport import (
    YazioTransportAuthenticationError,
    YazioTransportDeadlineError,
    YazioTransportError,
    YazioTransportInvalidResponseError,
    _BoundedYazioClient,
    _execute_worker,
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

class _SdkResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        parsed: object = None,
        content: bytes = b"{}",
    ) -> None:
        self.status_code = status_code
        self.parsed = parsed
        self.content = content
        self.headers: dict[str, str] = {}


def _patch_sdk_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    def new_client() -> object:
        return type("Client", (), {"get_httpx_client": lambda self: self, "close": lambda self: None})()

    monkeypatch.setattr(yazio_sdk_provider, "_new_client", new_client)
    monkeypatch.setattr(yazio_sdk_provider, "_new_authenticated_client", lambda _token: new_client())
    monkeypatch.setattr(
        yazio_sdk_provider.create_token,
        "sync_detailed",
        lambda **_: type("Response", (), {"status_code": 200, "parsed": {"access_token": "token"}})(),
    )
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



def test_sdk_worker_dispatches_provider_payload_without_legacy_client(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    class _Provider:
        def fetch(self, email, password, start_day, end_day, include_micronutrients):
            calls.append((email, include_micronutrients))
            return YazioProviderResult(
                payload={
                    "days": [{"local_date": start_day.isoformat()}],
                    "weight": {"2026-08-01": 72.5},
                },
                metadata=YazioProviderMetadata(
                    micronutrient_complete=False,
                    provider_mode="sdk",
                ),
            )

    monkeypatch.setattr(yazio_transport, "get_yazio_provider", lambda mode: _Provider())
    monkeypatch.setattr(
        yazio_transport,
        "_login",
        lambda *_args: pytest.fail("SDK worker must use the SDK provider authentication"),
    )
    result = _execute_worker(
        {
            "operation": "fetch",
            "provider_mode": "sdk",
            "email": "owner@example.com",
            "password": "private-password",
            "start_day": "2026-08-01",
            "end_day": "2026-08-01",
            "include_micronutrients": True,
            "connect_timeout": 3.05,
            "read_timeout": 15,
            "request_workers": 3,
        }
    )
    assert result == {
        "days": [{"local_date": "2026-08-01"}],
        "weight": {"2026-08-01": 72.5},
    }
    assert calls == [("owner@example.com", True)]


def test_sdk_weight_range_uses_generated_v22_operation_and_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    class _Authenticated:
        def get_httpx_client(self) -> object:
            return type("HTTPClient", (), {"request": object()})()

    def latest(**kwargs: object) -> object:
        calls.append(kwargs)
        return type(
            "Response",
            (),
            {
                "status_code": 200,
                "headers": {},
                "content": b"{}",
                "parsed": type(
                    "WeightEntry",
                    (),
                    {
                        "date": "2026-08-01",
                        "id": "provider-weight-1",
                        "value": 72.5,
                        "external_id": None,
                        "gateway": None,
                        "source": None,
                    },
                )(),
            },
        )()

    monkeypatch.setattr(yazio_sdk_provider.get_latest_weight, "sync_detailed", latest)
    result = yazio_sdk_provider._fetch_weight_range(
        _Authenticated(),
        date(2026, 8, 1),
        date(2026, 8, 1),
        max_workers=1,
    )

    assert result == {
        "provider-weight-1": {
            "id": "provider-weight-1",
            "date": "2026-08-01",
            "value": 72.5,
            "unit": "kg",
            "external_id": None,
            "gateway": None,
            "source": None,
        }
    }
    assert calls[0]["date"] == "2026-08-01"

def test_sdk_weight_classifies_status_transport_and_numeric_failures() -> None:
    class _Response:
        status_code = 429
        headers: ClassVar[dict[str, str]] = {"Retry-After": "999999"}
        content = b""

        @staticmethod
        def json() -> dict[str, float]:
            return {"value": 72.5}

        @staticmethod
        def close() -> None:
            return None

    class _HTTPClient:
        def __init__(self, outcome) -> None:
            self.outcome = outcome

        def get(self, _url, **_kwargs):
            if isinstance(self.outcome, BaseException):
                raise self.outcome
            return self.outcome

    class _Authenticated:
        def __init__(self, http_client) -> None:
            self.http_client = http_client

        def get_httpx_client(self) -> _HTTPClient:
            return self.http_client

    with pytest.raises(YazioProviderRateLimitedError) as rate_limited:
        yazio_sdk_provider._fetch_weight_range(
            _Authenticated(_HTTPClient(_Response())),
            date(2026, 8, 1),
            date(2026, 8, 1),
            max_workers=1,
        )
    assert rate_limited.value.retry_after == 3_600

    with pytest.raises(YazioProviderNetworkTimeoutError) as timed_out:
        yazio_sdk_provider._fetch_weight_range(
            _Authenticated(_HTTPClient(httpx.ReadTimeout("private-password"))),
            date(2026, 8, 1),
            date(2026, 8, 1),
            max_workers=1,
        )
    assert "private-password" not in str(timed_out.value)

    _Response.status_code = 200
    _Response.headers = {}
    _Response.json = staticmethod(lambda: {"value": -1})
    with pytest.raises(YazioProviderInvalidResponseError):
        yazio_sdk_provider._fetch_weight_range(
            _Authenticated(_HTTPClient(_Response())),
            date(2026, 8, 1),
            date(2026, 8, 1),
            max_workers=1,
        )




def test_worker_rejects_missing_provider_mode() -> None:
    with pytest.raises(ValueError, match="Invalid worker input"):
        _execute_worker(
            {
                "operation": "fetch",
                "email": "owner@example.com",
                "password": "private-password",
                "start_day": "2026-08-01",
                "end_day": "2026-08-01",
                "include_micronutrients": False,
                "connect_timeout": 3.05,
                "read_timeout": 15,
                "request_workers": 3,
            }
        )


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


def test_consumed_items_validation_exposes_bounded_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_sdk_clients(monkeypatch)
    monkeypatch.setattr(
        yazio_sdk_provider.list_consumed_items,
        "sync_detailed",
        lambda **_: _SdkResponse(parsed={"products": "invalid"}),
    )

    with pytest.raises(YazioProviderInvalidResponseError) as caught:
        yazio_sdk_provider.YazioSdkProvider().fetch_food_diary(
            "owner@example.com",
            "private-password",
            date(2026, 8, 1),
            date(2026, 8, 1),
        )

    context = caught.value.context
    assert context is not None
    assert context.operation == "consumed_items"
    assert context.endpoint_key == "consumed_items"
    assert context.response_model == "ConsumedItems"
    assert context.validation_location == "products"
    assert "private-password" not in repr(context)


def test_product_lookup_validation_exposes_bounded_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_sdk_clients(monkeypatch)
    monkeypatch.setattr(
        yazio_sdk_provider.list_consumed_items,
        "sync_detailed",
        lambda **_: _SdkResponse(
            parsed={"products": [{"id": "event-1", "product_id": "product-1"}]}
        ),
    )
    monkeypatch.setattr(
        yazio_sdk_provider.get_daily_nutrients,
        "sync_detailed",
        lambda **_: _SdkResponse(parsed=[]),
    )
    monkeypatch.setattr(
        yazio_sdk_provider.get_product,
        "sync_detailed",
        lambda *_args, **_kwargs: _SdkResponse(parsed=None),
    )

    with pytest.raises(YazioProviderInvalidResponseError) as caught:
        yazio_sdk_provider.YazioSdkProvider().fetch_food_diary(
            "owner@example.com",
            "private-password",
            date(2026, 8, 1),
            date(2026, 8, 1),
        )

    context = caught.value.context
    assert context is not None
    assert context.operation == "product_lookup"
    assert context.endpoint_key == "product"
    assert context.response_model == "Product"
    assert context.validation_location == "response"


def test_simple_product_normalization_exposes_bounded_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_sdk_clients(monkeypatch)
    monkeypatch.setattr(
        yazio_sdk_provider.list_consumed_items,
        "sync_detailed",
        lambda **_: _SdkResponse(parsed={"simple_products": ["invalid"]}),
    )

    with pytest.raises(YazioProviderInvalidResponseError) as caught:
        yazio_sdk_provider.YazioSdkProvider().fetch_food_diary(
            "owner@example.com",
            "private-password",
            date(2026, 8, 1),
            date(2026, 8, 1),
        )

    context = caught.value.context
    assert context is not None
    assert context.operation == "simple_product_normalization"
    assert context.endpoint_key == "consumed_items"
    assert context.validation_location == "simple_products[]"


def test_http_error_context_contains_status_without_response_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_sdk_clients(monkeypatch)
    monkeypatch.setattr(
        yazio_sdk_provider.get_daily_nutrients,
        "sync_detailed",
        lambda **_: _SdkResponse(status_code=503, content=b"private-health-response"),
    )

    with pytest.raises(YazioProviderUnavailableError) as caught:
        yazio_sdk_provider.YazioSdkProvider().fetch(
            "owner@example.com",
            "private-password",
            date(2026, 8, 1),
            date(2026, 8, 1),
            False,
        )

    context = caught.value.context
    assert context is not None
    assert context.operation == "daily_nutrients"
    assert context.endpoint_key == "daily_nutrients"
    assert context.upstream_status_code == 503
    assert "private-health-response" not in repr(caught.value)


def test_worker_preserves_bounded_provider_context_without_raw_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = {
        "operation": "consumed_items",
        "endpoint_key": "consumed_items",
        "upstream_status_code": 200,
        "response_model": "ConsumedItems",
        "error_category": "validation",
        "validation_location": "products[].product_id",
        "retryable": False,
    }
    process = _FakeProcess(
        json.dumps({"ok": False, "kind": "invalid_response", "context": context}).encode()
    )
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)

    with pytest.raises(YazioTransportInvalidResponseError) as caught:
        _run_worker({"operation": "fetch_domain"}, deadline_seconds=5)

    assert caught.value.context == context
    assert "private-password" not in repr(caught.value)
    assert "raw" not in repr(caught.value).lower()


def test_provider_context_details_match_security_event_contract() -> None:
    error = YazioInvalidResponseError("generic")
    error.provider_context = {
        "operation": "consumed_items",
        "endpoint_key": "consumed_items",
        "upstream_status_code": 422,
        "response_model": "ConsumedItems",
        "error_category": "validation",
        "validation_location": "products[].product_id",
        "retryable": False,
    }

    details = yazio_sync._provider_context_details(error)
    assert details["provider_validation_location"] == "products.product_id"
    log_security_event(
        "integration.yazio.sync_failed",
        reason="invalid_response",
        details={"mode": "manual", **details},
    )
