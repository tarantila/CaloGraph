from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pytest

from app.withings.client import WithingsHTTPClient, WithingsHTTPTransport
from app.withings.constants import (
    WITHINGS_ACTIVITY_URL,
    WITHINGS_MEASURE_URL,
)
from app.withings.errors import (
    WithingsAuthenticationError,
    WithingsInvalidResponseError,
)


@dataclass
class FakeResponse:
    status_code: int = 200
    headers: dict[str, str] | None = None
    chunks: tuple[bytes, ...] = (b'{"status":0,"body":{}}',)

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def iter_bytes(self):
        yield from self.chunks


class FakeHTTP:
    def __init__(self, response: FakeResponse | None = None) -> None:
        self.response = response or FakeResponse()
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def stream(self, method: str, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        return self.response

    def close(self) -> None:
        return None


def test_transport_posts_form_data_to_fixed_data_endpoints_and_bearer_header() -> None:
    http = FakeHTTP()
    transport = WithingsHTTPTransport(http_client=http, connect_timeout=1.5, read_timeout=4.0)

    transport.get_measure(access_token="access-token", startdate=1, enddate=2, offset=0)
    transport.get_activity(
        access_token="access-token",
        startdateymd=date(2026, 9, 20),
        enddateymd=date(2026, 9, 21),
        offset=100,
    )

    assert [call[1] for call in http.calls] == [WITHINGS_MEASURE_URL, WITHINGS_ACTIVITY_URL]
    for method, _url, kwargs in http.calls:
        assert method == "POST"
        headers = kwargs["headers"]
        assert headers["Content-Type"] == "application/x-www-form-urlencoded"
        assert headers["Authorization"] == "Bearer access-token"
        assert kwargs["follow_redirects"] is False
        assert kwargs["timeout"].connect == 1.5
        assert kwargs["timeout"].read == 4.0
        assert "data" in kwargs

    assert http.calls[0][2]["data"] == {
        "action": "getmeas",
        "startdate": "1",
        "enddate": "2",
        "offset": "0",
    }
    assert http.calls[1][2]["data"] == {
        "action": "getactivity",
        "startdateymd": "2026-09-20",
        "enddateymd": "2026-09-21",
        "data_fields": "calories",
        "offset": "100",
    }


def test_transport_exposes_only_data_endpoint_operations() -> None:
    transport = WithingsHTTPTransport(http_client=FakeHTTP())

    assert not hasattr(transport, "exchange_token")
    assert not hasattr(transport, "refresh_token")

def test_transport_rejects_oversized_body_without_leaking_token_or_payload() -> None:
    secret = b"access-token-and-provider-payload"
    http = FakeHTTP(FakeResponse(chunks=(b"x" * (2 * 1024 * 1024), secret)))
    transport = WithingsHTTPTransport(http_client=http)

    with pytest.raises(WithingsInvalidResponseError) as caught:
        transport.get_measure(access_token="access-token", startdate=1, enddate=2, offset=0)

    assert caught.value.code == "invalid_response"
    assert "access-token" not in str(caught.value)
    assert "provider" not in str(caught.value)


def test_transport_rejects_invalid_credentials_without_request() -> None:
    http = FakeHTTP()
    transport = WithingsHTTPTransport(http_client=http)

    with pytest.raises(WithingsAuthenticationError) as caught:
        transport.get_activity(
            access_token="token\nleak",
            startdateymd=date(2026, 9, 20),
            enddateymd=date(2026, 9, 21),
            offset=0,
        )

    assert "token" not in str(caught.value)
    assert http.calls == []


def test_high_level_client_fetches_bounded_measure_pages() -> None:
    responses = [
        FakeResponse(chunks=(b'{"status":0,"body":{"measuregrps":[],"more":1,"offset":100}}',)),
        FakeResponse(chunks=(b'{"status":0,"body":{"measuregrps":[],"more":0}}',)),
    ]

    class PagingHTTP(FakeHTTP):
        def stream(self, method: str, url: str, **kwargs: object) -> FakeResponse:
            self.calls.append((method, url, kwargs))
            return responses.pop(0)

    http = PagingHTTP()
    transport = WithingsHTTPTransport(http_client=http)
    client = WithingsHTTPClient(transport=transport, max_pages=2)
    result = client.fetch_measure_pages(access_token="token", startdate=1, enddate=2)

    assert result.pages_read == 2
    assert [call[2]["data"]["offset"] for call in http.calls] == ["0", "100"]
    assert [page.offset for page in result.measure_pages] == [0, 100]
    assert result.truncated is False


def test_high_level_client_uses_activity_response_offset_for_next_page() -> None:
    responses = [
        FakeResponse(
            chunks=(
                b'{"status":0,"body":{"activities":[],"more":true,"offset":175}}',
            )
        ),
        FakeResponse(
            chunks=(
                b'{"status":0,"body":{"activities":[],"more":false,"offset":175}}',
            )
        ),
    ]

    class PagingHTTP(FakeHTTP):
        def stream(self, method: str, url: str, **kwargs: object) -> FakeResponse:
            self.calls.append((method, url, kwargs))
            return responses.pop(0)

    http = PagingHTTP()
    client = WithingsHTTPClient(transport=WithingsHTTPTransport(http_client=http), max_pages=2)
    result = client.fetch_activity_pages(
        access_token="access-token",
        startdateymd=date(2026, 9, 20),
        enddateymd=date(2026, 9, 27),
    )

    assert result.pages_read == 2
    assert [call[2]["data"] for call in http.calls] == [
        {
            "action": "getactivity",
            "startdateymd": "2026-09-20",
            "enddateymd": "2026-09-27",
            "data_fields": "calories",
            "offset": "0",
        },
        {
            "action": "getactivity",
            "startdateymd": "2026-09-20",
            "enddateymd": "2026-09-27",
            "data_fields": "calories",
            "offset": "175",
        },
    ]
    assert [page.offset for page in result.activity_pages] == [175, 175]
