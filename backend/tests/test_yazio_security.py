import ast
import io
import json
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from typing import ClassVar

import httpx
import pytest

from app.config import settings
from app.models import YazioConnection
from app.security_events import log_security_event
from app.services import yazio_sdk_provider, yazio_sync, yazio_transport
from app.services.credential_crypto import encrypt_credential
from app.services.yazio_guard import YazioOperationBusy, yazio_operation_slot
from app.services.yazio_provider import (
    YazioFoodDiary,
    YazioProviderErrorContext,
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
    YazioTransportUnavailableError,
    _execute_worker,
    _run_worker,
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
@pytest.mark.parametrize(
    ("outcome", "error_type"),
    [
        (401, yazio_sdk_provider.YazioProviderAuthenticationError),
        (302, YazioProviderUnavailableError),
        ("timeout", YazioProviderNetworkTimeoutError),
    ],
)
def test_sdk_login_uses_explicit_timeout_without_redirects_or_auth_retries(
    monkeypatch: pytest.MonkeyPatch, outcome: int | str, error_type: type[Exception],
) -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if outcome == "timeout":
            raise httpx.ReadTimeout("private-password", request=request)
        assert isinstance(outcome, int)
        return httpx.Response(
            outcome,
            json={"error": "invalid_grant", "error_description": "synthetic"},
            headers={"Location": "https://redirect.invalid/oauth/token"},
        )

    monkeypatch.setattr(settings, "yazio_connect_timeout_seconds", 3.05)
    monkeypatch.setattr(settings, "yazio_read_timeout_seconds", 15)
    monkeypatch.setattr(
        yazio_sdk_provider, "_httpx_args",
        lambda: {"transport": httpx.MockTransport(handle)},
    )

    with pytest.raises(error_type) as caught:
        yazio_sdk_provider.YazioSdkProvider().validate_credentials(
            "owner@example.com", "private-password",
        )

    assert len(requests) == 1
    assert requests[0].extensions["timeout"] == {
        "connect": 3.05, "read": 15, "write": 15, "pool": 3.05,
    }
    assert requests[0].url.host != "redirect.invalid"
    assert "private-password" not in str(caught.value)


def test_authenticated_sdk_client_does_not_follow_redirects(monkeypatch) -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            302, headers={"Location": "https://redirect.invalid/private"},
        )

    monkeypatch.setattr(
        yazio_sdk_provider, "_httpx_args",
        lambda: {"transport": httpx.MockTransport(handle)},
    )
    client = yazio_sdk_provider._new_authenticated_client("synthetic-token")
    try:
        response = client.get_httpx_client().get("/v22/user/daily-nutrients")
    finally:
        yazio_sdk_provider._close_client(client)

    assert response.status_code == 302
    assert len(requests) == 1
    assert requests[0].url.host != "redirect.invalid"
    assert requests[0].headers["Authorization"] == "Bearer synthetic-token"


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
    assert "provider_mode" not in worker_input
    assert worker_input["operation"] == "validate"
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


def test_transport_bounds_input_before_starting_worker(monkeypatch) -> None:
    monkeypatch.setattr(yazio_transport, "MAX_WORKER_INPUT_BYTES", 32)
    monkeypatch.setattr(
        subprocess, "Popen",
        lambda *_args, **_kwargs: pytest.fail("Oversized input must not spawn a worker"),
    )
    with pytest.raises(YazioTransportError, match="input is too large"):
        _run_worker({"operation": "validate", "password": "x" * 64}, deadline_seconds=5)


def test_worker_bounds_stdin_before_dispatch(monkeypatch) -> None:
    monkeypatch.setattr(yazio_transport, "MAX_WORKER_INPUT_BYTES", 32)
    monkeypatch.setattr(
        yazio_transport.sys, "stdin",
        type("Input", (), {"buffer": io.BytesIO(b"x" * 33)})(),
    )
    stdout = io.StringIO()
    monkeypatch.setattr(yazio_transport.sys, "stdout", stdout)
    monkeypatch.setattr(
        yazio_transport, "_execute_worker",
        lambda *_args: pytest.fail("Oversized input must not reach the SDK"),
    )

    assert yazio_transport._worker_main() == 0
    assert json.loads(stdout.getvalue()) == {"ok": False, "kind": "unavailable"}



def test_sdk_worker_has_no_provider_selector_or_aggregate_only_operation(monkeypatch) -> None:
    sdk_calls: list[str] = []
    instances: list[object] = []

    class _Provider:
        def __init__(self):
            instances.append(self)
            self.aggregate_read = False

        def validate_credentials(self, email, password):
            assert (email, password) == ("owner@example.com", "private-password")
            sdk_calls.append("validate")

        def fetch(self, email, password, start_day, end_day):
            assert (email, password) == ("owner@example.com", "private-password")
            assert (start_day, end_day) == (date(2026, 8, 1), date(2026, 8, 1))
            self.aggregate_read = True
            sdk_calls.append("aggregate")
            return YazioProviderResult(
                payload={"days": {"2026-08-01": {"energy": 1900.0}}, "weight": {}},
                metadata=YazioProviderMetadata(micronutrient_complete=False),
            )

        def fetch_food_diary(self, email, password, start_day, end_day):
            assert self.aggregate_read, "Aggregate and diary must share the SDK provider instance"
            assert (email, password) == ("owner@example.com", "private-password")
            assert (start_day, end_day) == (date(2026, 8, 1), date(2026, 8, 1))
            sdk_calls.append("diary")
            return YazioFoodDiary(start_day, end_day, (), (), (), ())

    monkeypatch.setattr(yazio_sdk_provider, "YazioSdkProvider", _Provider)
    request = {
        "email": "owner@example.com",
        "password": "private-password",
        "connect_timeout": 3.05,
        "read_timeout": 15,
        "request_workers": 3,
    }
    assert _execute_worker({"operation": "validate", **request}) is None
    assert sdk_calls == ["validate"]
    sdk_calls.clear()
    domain_result = _execute_worker({
        "operation": "fetch_domain", **request,
        "start_day": "2026-08-01", "end_day": "2026-08-01",
    })
    assert sdk_calls == ["aggregate", "diary"]
    assert len(instances) == 2
    assert set(domain_result) == {"aggregate", "diary"}
    assert domain_result["aggregate"] == {
        "days": {"2026-08-01": {"energy": 1900.0}}, "weight": {},
    }
    assert yazio_transport._decode_food_diary(domain_result["diary"]) == YazioFoodDiary(
        date(2026, 8, 1), date(2026, 8, 1), (), (), (), (),
    )


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




@pytest.mark.parametrize(
    "obsolete",
    [
        {"operation": "fetch"},
        {"operation": "invalid"},
        {"provider_mode": "sdk"},
        {"provider_mode": "legacy"},
        {"provider_mode": None},
        {"mode": "sdk"},
        {"yazio_provider": "sdk"},
        {"include_micronutrients": True},
    ],
)
def test_worker_rejects_old_operations_and_selector_fields(obsolete) -> None:
    with pytest.raises(ValueError, match="Invalid worker input"):
        _execute_worker({
            "operation": "fetch_domain",
            "email": "owner@example.com",
            "password": "private-password",
            "start_day": "2026-08-01",
            "end_day": "2026-08-01",
            "connect_timeout": 3.05,
            "read_timeout": 15,
            "request_workers": 3,
            **obsolete,
        })


@pytest.mark.parametrize(
    "invalid",
    [
        {"connect_timeout": 0.09},
        {"connect_timeout": float("nan")},
        {"read_timeout": 121},
        {"read_timeout": True},
        {"request_workers": 0},
        {"request_workers": 11},
        {"request_workers": True},
    ],
)
def test_worker_retains_bounded_sdk_options(invalid) -> None:
    with pytest.raises(ValueError, match="Invalid worker options"):
        _execute_worker({
            "operation": "validate",
            "email": "owner@example.com",
            "password": "private-password",
            "connect_timeout": 3.05,
            "read_timeout": 15,
            "request_workers": 3,
            **invalid,
        })


@pytest.mark.parametrize(
    ("start_day", "end_day"),
    [
        ("not-a-date", "2026-08-01"),
        ("2026-08-02", "2026-08-01"),
        ("2026-01-01", "2027-01-02"),
    ],
)
def test_worker_rejects_invalid_sdk_ranges_without_fetching(monkeypatch, start_day, end_day) -> None:
    class _Provider:
        def fetch(self, *_args):
            pytest.fail("Invalid ranges must never reach SDK fetch")

    monkeypatch.setattr(yazio_sdk_provider, "YazioSdkProvider", _Provider)
    with pytest.raises(ValueError, match="Invalid YAZIO date range"):
        _execute_worker({
            "operation": "fetch_domain",
            "email": "owner@example.com",
            "password": "private-password",
            "start_day": start_day,
            "end_day": end_day,
            "connect_timeout": 3.05,
            "read_timeout": 15,
            "request_workers": 3,
        })


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


def _configured_connection(db, user, monkeypatch):
    monkeypatch.setattr(settings, "yazio_sdk_client_secret", "synthetic-sdk-secret")
    connection = YazioConnection(
        user_id=user.id, source_identifier=f"yazio:{user.id}",
        encrypted_email=encrypt_credential("owner@example.com"),
        encrypted_password=encrypt_credential("password"),
        sync_enabled=True, sync_days=1,
    )
    db.add(connection)
    db.commit()
    return connection


def test_user_slot_remains_held_while_yazio_evidence_is_persisted(
    db, user, monkeypatch,
) -> None:
    _configured_connection(db, user, monkeypatch)
    persisted = False
    original = yazio_sync._persist_import_locked

    def persist_while_locked(*args, **kwargs):
        nonlocal persisted
        with pytest.raises(YazioOperationBusy), yazio_operation_slot(user.id):
            pass
        persisted = True
        return original(*args, **kwargs)

    def fetch(_email, _password, start_day, end_day):
        return {"days": {end_day.isoformat(): {"energy": 1800}}}, YazioFoodDiary(
            requested_start_day=start_day, requested_end_day=end_day,
            consumed_products=(), consumed_simple_products=(), product_profiles=(),
            daily_summaries=(),
        )

    monkeypatch.setattr(yazio_sync, "_persist_import_locked", persist_while_locked)
    monkeypatch.setattr(yazio_sync, "fetch_yazio_domain_transport", fetch)
    summary = yazio_sync.run_manual_yazio_sync(
        user.id, now=datetime(2026, 7, 28, tzinfo=UTC),
    )
    assert persisted is True
    assert summary.status == "completed"
    assert summary.inserted == 1


def test_provider_failures_open_the_shared_circuit(db, user, monkeypatch) -> None:
    _configured_connection(db, user, monkeypatch)
    monkeypatch.setattr(settings, "yazio_circuit_failure_limit", 2)
    monkeypatch.setattr(settings, "yazio_circuit_window_seconds", 600)
    calls = 0

    def provider_failure(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise YazioProviderUnavailableError()

    monkeypatch.setattr(yazio_sync, "fetch_yazio_domain_transport", provider_failure)
    for _ in range(2):
        with pytest.raises(YazioSyncError):
            yazio_sync.run_manual_yazio_sync(user.id)
    with pytest.raises(YazioCircuitOpen):
        yazio_sync.run_manual_yazio_sync(user.id)
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
    assert context.response_model == "Response[ConsumedItems]"
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
    assert context.response_model == "Response[Product]"
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


def test_invalid_response_requires_bounded_context() -> None:
    with pytest.raises(TypeError):
        YazioProviderInvalidResponseError()


def test_production_invalid_response_raises_are_contextualized() -> None:
    source_root = Path(__file__).parents[1] / "app"
    violations: list[str] = []
    for source_path in source_root.rglob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
                continue
            callee = node.exc.func
            name = callee.id if isinstance(callee, ast.Name) else None
            if name != "YazioProviderInvalidResponseError":
                continue
            if not any(keyword.arg == "context" for keyword in node.exc.keywords):
                violations.append(f"{source_path}:{node.lineno}")
    assert violations == []

def test_worker_entrypoint_parent_and_sync_preserve_context(monkeypatch) -> None:
    context = YazioProviderErrorContext(
        operation="consumed_items",
        endpoint_key="consumed_items",
        upstream_status_code=None,
        response_model="Response[ConsumedItems]",
        error_category="response_validation",
        validation_location="products[].some_field",
        retryable=False,
    )
    monkeypatch.setattr(
        yazio_transport,
        "_execute_worker",
        lambda _payload: (_ for _ in ()).throw(
            YazioProviderInvalidResponseError(context=context)
        ),
    )
    stdin = type("Input", (), {"buffer": io.BytesIO(b'{"operation":"fetch_domain"}')})()
    stdout = io.StringIO()
    monkeypatch.setattr(yazio_transport.sys, "stdin", stdin)
    monkeypatch.setattr(yazio_transport.sys, "stdout", stdout)

    assert yazio_transport._worker_main() == 0
    worker_envelope = json.loads(stdout.getvalue())


    assert worker_envelope == {
        "ok": False,
        "kind": "invalid_response",
        "context": context.to_dict(),
    }

    process = _FakeProcess(stdout.getvalue().encode())
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)
    with pytest.raises(YazioTransportInvalidResponseError) as caught:
        _run_worker({"operation": "fetch_domain"}, deadline_seconds=5)
    assert caught.value.context == context.to_dict()

    with pytest.raises(YazioProviderInvalidResponseError) as provider_caught:
        yazio_transport._raise_domain_provider_error(caught.value)
    provider_error = provider_caught.value
    assert provider_error.context == context
    mapped = yazio_sync._map_yazio_provider_error(provider_error)
    assert yazio_sync._provider_context_details(mapped) == {
        "provider_operation": "consumed_items",
        "provider_endpoint": "consumed_items",
        "provider_model": "ResponseConsumedItems",
        "provider_error_category": "response_validation",
        "provider_validation_location": "products.some_field",
        "provider_retryable": False,
    }


def test_transport_invalid_response_rewrap_gets_bounded_domain_context() -> None:
    with pytest.raises(YazioProviderInvalidResponseError) as caught:
        yazio_transport._raise_domain_provider_error(YazioTransportInvalidResponseError())

    context = caught.value.context
    assert context is not None
    assert context.operation == "domain_worker"
    assert context.endpoint_key == "domain_worker"
    assert context.response_model == "YazioFoodDiary"
    assert context.validation_location == "transport"


def test_worker_parent_preserves_product_lookup_context(monkeypatch) -> None:
    context = {
        "operation": "product_lookup",
        "endpoint_key": "product",
        "upstream_status_code": None,
        "response_model": "Product",
        "error_category": "response_validation",
        "validation_location": "response",
        "retryable": False,
    }
    process = _FakeProcess(
        json.dumps(
            {
                "ok": False,
                "kind": "invalid_response",
                "context": context,
                "message": "test-food-name-secret-marker test-token-secret-marker",
            }
        ).encode()
    )
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)

    with pytest.raises(YazioTransportInvalidResponseError) as caught:
        _run_worker({"operation": "fetch_domain"}, deadline_seconds=5)
    assert caught.value.context == context
    assert "test-food-name-secret-marker" not in repr(caught.value)
    assert "test-token-secret-marker" not in repr(caught.value)


def test_worker_invalid_response_without_context_gets_bounded_context(monkeypatch) -> None:
    process = _FakeProcess(b'{"ok":false,"kind":"invalid_response"}')
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)

    with pytest.raises(YazioTransportInvalidResponseError) as caught:
        _run_worker({"operation": "fetch_domain"}, deadline_seconds=5)

    assert caught.value.context == {
        "operation": "domain_worker",
        "endpoint_key": "domain_worker",
        "upstream_status_code": None,
        "response_model": "YazioFoodDiary",
        "error_category": "response_validation",
        "validation_location": "worker",
        "retryable": False,
    }


def test_worker_parent_preserves_http_status_without_response_body(monkeypatch) -> None:
    context = {
        "operation": "consumed_items",
        "endpoint_key": "consumed_items",
        "upstream_status_code": 502,
        "response_model": "Response[ConsumedItems]",
        "error_category": "http",
        "validation_location": None,
        "retryable": True,
    }
    process = _FakeProcess(
        json.dumps(
            {
                "ok": False,
                "kind": "unavailable",
                "context": context,
                "body": "test-weight-value-marker",
            }
        ).encode()
    )
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)

    with pytest.raises(YazioTransportUnavailableError) as caught:
        _run_worker({"operation": "fetch_domain"}, deadline_seconds=5)
    assert caught.value.context == context
    assert "test-weight-value-marker" not in repr(caught.value)


def test_domain_transport_rewrapping_preserves_provider_context(db, user, monkeypatch) -> None:
    context = YazioProviderErrorContext(
        operation="daily_summary",
        endpoint_key="daily_summary",
        upstream_status_code=502,
        response_model="Response[DailySummary]",
        error_category="http",
        validation_location=None,
        retryable=True,
    )
    connection = _configured_connection(db, user, monkeypatch)
    monkeypatch.setattr(
        yazio_transport,
        "_run_worker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            YazioTransportUnavailableError(context=context.to_dict())
        ),
    )

    with pytest.raises(YazioSyncError) as caught:
        yazio_sync.run_manual_yazio_sync(
            user.id, start_day=date(2026, 8, 1), end_day=date(2026, 8, 1),
        )
    assert caught.value.provider_context == context.to_dict()
    db.refresh(connection)
    assert connection.last_success_at is None
    assert connection.last_error == "YAZIO ist vorübergehend nicht erreichbar."


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
