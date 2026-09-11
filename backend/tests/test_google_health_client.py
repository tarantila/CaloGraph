from __future__ import annotations

import json
import traceback
from datetime import UTC, datetime

import pytest

from app.google_health.client import (
    GOOGLE_HEALTH_API_BASE_URL,
    GOOGLE_HEALTH_MAX_RESPONSE_BYTES,
    GoogleHealthAuthenticationError,
    GoogleHealthClient,
    GoogleHealthHTTPTransport,
    GoogleHealthInvalidResponseError,
    GoogleHealthProviderUnavailableError,
    GoogleHealthRateLimitedError,
    GoogleHealthScopeError,
    GoogleHealthTransientError,
)



class FakeCredentials:
    valid = True
    expired = False
    token = "access-token"


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: object | None = None) -> None:
        self.status_code = status_code
        self.headers = {"content-type": "application/json"}
        self._payload = payload if payload is not None else {"nutritionLog": []}
        self.text = '{"secret":"do-not-leak"}'

    def json(self) -> object:
        if isinstance(self._payload, BaseException):
            raise self._payload
        return self._payload

    def iter_bytes(self):
        yield json.dumps(self._payload).encode()


 


class StreamContext:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response

    def __enter__(self) -> FakeResponse:
        return self.response

    def __exit__(self, *_: object) -> None:
        return None


class RefreshingCredentials:
    valid = False
    expired = True
    token: str | None = None
    request: object | None = None

    def refresh(self, request: object) -> None:
        self.request = request
        self.token = "refreshed-access-token"


class FailingCredentials:
    valid = False
    expired = True
    token: str | None = None

    def refresh(self, request: object) -> None:
        raise RuntimeError("refresh-secret")


def test_client_suppresses_refresh_failure_cause() -> None:
    with pytest.raises(GoogleHealthAuthenticationError) as raised:
        GoogleHealthClient(FakeTransport(), FailingCredentials()).get_nutrition_log_page(page_size=10)
    assert raised.value.__cause__ is None
    assert "refresh-secret" not in "".join(traceback.format_exception(raised.value))


def test_client_refreshes_expired_google_credentials_in_memory() -> None:
    credentials = RefreshingCredentials()
    transport = FakeTransport()

    GoogleHealthClient(transport, credentials).get_nutrition_log_page(page_size=10)

    assert credentials.token == "refreshed-access-token"
    assert credentials.request.__class__.__name__ == "Request"


class FakeTransport:
    def __init__(self, response: FakeResponse | None = None) -> None:
        self.response = response or FakeResponse()
        self.calls: list[dict[str, object]] = []

    def get_nutrition_log(self, **kwargs: object) -> FakeResponse:
        self.calls.append(kwargs)
        return self.response


def test_client_uses_only_fixed_nutrition_log_get_operation() -> None:
    transport = FakeTransport()
    client = GoogleHealthClient(transport, FakeCredentials())

    client.get_nutrition_log_page(page_size=25, page_token="next page")

    assert not hasattr(transport, "request")
    assert len(transport.calls) == 1
    assert transport.calls[0]["page_size"] == 25
    assert transport.calls[0]["page_token"] == "next page"


def test_http_transport_uses_exact_fixed_host_and_path_and_get_only() -> None:
    class RecordingHTTPClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def stream(self, method: str, url: str, **kwargs: object) -> StreamContext:
            self.calls.append({"method": method, "url": url, **kwargs})
            return StreamContext(FakeResponse())

    http_client = RecordingHTTPClient()
    transport = GoogleHealthHTTPTransport(http_client=http_client)

    transport.get_nutrition_log(
        access_token="access-token",
        page_size=10,
        page_token=None,
        start_time=None,
        end_time=None,
    )

    assert http_client.calls[0]["method"] == "GET"
    assert http_client.calls[0]["url"] == (
        "https://health.googleapis.com/v4/users/me/dataTypes/nutrition-log/dataPoints"
    )
    assert not hasattr(transport, "request")
    assert not hasattr(transport, "post")
    assert http_client.calls[0]["follow_redirects"] is False


def test_transport_has_explicit_timeout_and_safe_query_encoding() -> None:
    class RecordingHTTPClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def stream(self, method: str, url: str, **kwargs: object) -> StreamContext:
            self.calls.append({"method": method, "url": url, **kwargs})
            return StreamContext(FakeResponse())

    http_client = RecordingHTTPClient()
    transport = GoogleHealthHTTPTransport(http_client=http_client, connect_timeout=2.0, read_timeout=7.0)
    start = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    end = datetime(2026, 1, 3, 3, 4, 5, tzinfo=UTC)

    transport.get_nutrition_log(
        access_token="access-token",
        page_size=10,
        page_token="a token/&",
        start_time=start,
        end_time=end,
    )

    call = http_client.calls[0]
    assert call["timeout"].connect == 2.0
    assert call["timeout"].read == 7.0
    assert call["params"] == {
        "pageSize": "10",
        "pageToken": "a token/&",
        "filter": (
            "nutrition_log.interval.start_time >= 2026-01-02T03:04:05+00:00 AND "
            "nutrition_log.interval.start_time < 2026-01-03T03:04:05+00:00"
        ),
    }
    assert call["headers"] == {"Authorization": "Bearer access-token"}


def test_transport_caps_chunked_response_before_json_buffering() -> None:
    class OversizedResponse:
        status_code = 200
        headers = {"content-length": "1"}

        def iter_bytes(self):
            yield b"x" * GOOGLE_HEALTH_MAX_RESPONSE_BYTES
            yield b"raw-provider-body"

    class RecordingHTTPClient:
        def stream(self, method: str, url: str, **kwargs: object) -> StreamContext:
            return StreamContext(OversizedResponse())  # type: ignore[arg-type]

    with pytest.raises(GoogleHealthInvalidResponseError) as raised:
        GoogleHealthHTTPTransport(http_client=RecordingHTTPClient()).get_nutrition_log(
            access_token="access-token",
            page_size=10,
            page_token=None,
            start_time=None,
            end_time=None,
        )

    assert raised.value.__cause__ is None
    assert "raw-provider-body" not in "".join(traceback.format_exception(raised.value))


@pytest.mark.parametrize("page_size", [0, -1, 101])
def test_client_rejects_unbounded_page_size(page_size: int) -> None:
    with pytest.raises(ValueError):
        GoogleHealthClient(FakeTransport(), FakeCredentials()).get_nutrition_log_page(
            page_size=page_size
        )


def test_client_validates_page_token_and_time_boundaries() -> None:
    client = GoogleHealthClient(FakeTransport(), FakeCredentials())
    with pytest.raises(ValueError):
        client.get_nutrition_log_page(page_size=10, page_token="\nnot-safe")
    with pytest.raises(ValueError):
        client.get_nutrition_log_page(page_size=10, start_time=datetime(2026, 1, 1))
    with pytest.raises(ValueError):
        client.get_nutrition_log_page(
            page_size=10,
            start_time=datetime(2026, 1, 2, tzinfo=UTC),
            end_time=datetime(2026, 1, 1, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (401, GoogleHealthAuthenticationError),
        (403, GoogleHealthScopeError),
        (429, GoogleHealthRateLimitedError),
        (500, GoogleHealthProviderUnavailableError),
        (302, GoogleHealthInvalidResponseError),
        (418, GoogleHealthInvalidResponseError),
    ],
)
def test_client_maps_provider_status_without_response_leakage(status: int, error_type: type[Exception]) -> None:
    response = FakeResponse(status)
    with pytest.raises(error_type) as raised:
        GoogleHealthClient(FakeTransport(response), FakeCredentials()).get_nutrition_log_page(
            page_size=10
        )
    assert "do-not-leak" not in str(raised.value)
    assert "access-token" not in str(raised.value)
    assert raised.value.__cause__ is None
    if isinstance(raised.value, GoogleHealthRateLimitedError):
        assert 0 <= raised.value.retry_after <= 300


def test_client_maps_transport_failure_without_provider_cause() -> None:
    class FailingTransport:
        def get_nutrition_log(self, **_: object) -> FakeResponse:
            raise RuntimeError("provider-secret-body")

    with pytest.raises(GoogleHealthTransientError) as raised:
        GoogleHealthClient(FailingTransport(), FakeCredentials()).get_nutrition_log_page(page_size=10)
    assert raised.value.__cause__ is None
    assert "provider-secret-body" not in "".join(traceback.format_exception(raised.value))


def test_malformed_json_is_rejected_without_raw_body_or_persistence(caplog) -> None:
    response = FakeResponse(payload=ValueError('{"secret":"do-not-leak"}'))
    with pytest.raises(GoogleHealthInvalidResponseError) as raised:
        GoogleHealthClient(FakeTransport(response), FakeCredentials()).get_nutrition_log_page(
            page_size=10
        )
    assert "do-not-leak" not in str(raised.value)
    assert "secret" not in str(raised.value)
    assert raised.value.__cause__ is None
    assert "do-not-leak" not in "".join(traceback.format_exception(raised.value))
    assert "do-not-leak" not in caplog.text
