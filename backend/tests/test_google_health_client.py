from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.google_health.client import (
    GOOGLE_HEALTH_NUTRITION_LOG_PATH,
    GoogleHealthAuthenticationError,
    GoogleHealthClient,
    GoogleHealthHTTPTransport,
    GoogleHealthInvalidResponseError,
    GoogleHealthProviderUnavailableError,
    GoogleHealthRateLimitedError,
    GoogleHealthScopeError,
    GoogleHealthTransientError,
)
from app.google_health.constants import GOOGLE_HEALTH_API_BASE_URL


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

class RefreshingCredentials:
    valid = False
    expired = True
    token: str | None = None
    request: object | None = None

    def refresh(self, request: object) -> None:
        self.request = request
        self.token = "refreshed-access-token"


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

        def get(self, url: str, **kwargs: object) -> FakeResponse:
            self.calls.append({"url": url, **kwargs})
            return FakeResponse()

    http_client = RecordingHTTPClient()
    transport = GoogleHealthHTTPTransport(http_client=http_client)

    transport.get_nutrition_log(
        access_token="access-token",
        page_size=10,
        page_token=None,
        start_time=None,
        end_time=None,
    )

    assert http_client.calls[0]["url"] == (
        f"{GOOGLE_HEALTH_API_BASE_URL}{GOOGLE_HEALTH_NUTRITION_LOG_PATH}"
    )
    assert not hasattr(transport, "request")
    assert not hasattr(transport, "post")
    assert http_client.calls[0]["follow_redirects"] is False


def test_transport_has_explicit_timeout_and_safe_query_encoding() -> None:
    class RecordingHTTPClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def get(self, url: str, **kwargs: object) -> FakeResponse:
            self.calls.append({"url": url, **kwargs})
            return FakeResponse()

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
        "startTime": start.isoformat(),
        "endTime": end.isoformat(),
    }
    assert call["headers"] == {"Authorization": "Bearer access-token"}


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
    if isinstance(raised.value, GoogleHealthRateLimitedError):
        assert 0 <= raised.value.retry_after <= 300


def test_malformed_json_is_rejected_without_raw_body_or_persistence(caplog) -> None:
    response = FakeResponse(payload=ValueError('{"secret":"do-not-leak"}'))
    with pytest.raises(GoogleHealthInvalidResponseError) as raised:
        GoogleHealthClient(FakeTransport(response), FakeCredentials()).get_nutrition_log_page(
            page_size=10
        )
    assert "do-not-leak" not in str(raised.value)
    assert "secret" not in str(raised.value)
    assert "do-not-leak" not in caplog.text
