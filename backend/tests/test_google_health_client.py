from __future__ import annotations

import json
import traceback
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import ClassVar

import pytest

from app.google_health.client import (
    GOOGLE_HEALTH_ACTIVE_ENERGY_BURNED_PATH,
    GOOGLE_HEALTH_MAX_RESPONSE_BYTES,
    GOOGLE_HEALTH_WEIGHT_PATH,
    GoogleHealthAuthenticationError,
    GoogleHealthClient,
    GoogleHealthDataPointPage,
    GoogleHealthHTTPTransport,
    GoogleHealthInvalidResponseError,
    GoogleHealthProviderUnavailableError,
    GoogleHealthRateLimitedError,
    GoogleHealthScopeError,
    GoogleHealthTransientError,
    _parse_nonnegative_number,
)


class FakeCredentials:
    valid = True
    expired = False
    token = "access-token"


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: object | None = None) -> None:
        self.status_code = status_code
        self.headers = {"content-type": "application/json"}
        self._payload = payload if payload is not None else {"dataPoints": []}
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
        GoogleHealthClient(FakeTransport(), FailingCredentials()).get_nutrition_log_page(
            page_size=10
        )
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


class FakeDataTransport:
    def __init__(self, response: FakeResponse | None = None) -> None:
        self.response = response or FakeResponse()
        self.calls: list[dict[str, object]] = []

    def get_data_points(self, **kwargs: object) -> FakeResponse:
        self.calls.append(kwargs)
        return self.response


def _activity_point() -> dict[str, object]:
    return {
        "name": "users/me/dataTypes/active-energy-burned/dataPoints/activity-1",
        "activeEnergyBurned": {
            "interval": {
                "startTime": "2026-01-02T10:00:00Z",
                "endTime": "2026-01-02T11:00:00Z",
                "startUtcOffset": "3600s",
                "endUtcOffset": "3600s",
            },
            "kcal": Decimal("123.45"),
        },
        "dataSource": {"recordingMethod": "AUTOMATIC"},
    }


def _weight_point() -> dict[str, object]:
    return {
        "name": "users/me/dataTypes/weight/dataPoints/weight-1",
        "weight": {
            "sampleTime": {
                "physicalTime": "2026-01-02T10:00:00Z",
                "utcOffset": "3600s",
            },
            "weightGrams": Decimal("72500"),
        },
        "dataSource": {"recordingMethod": "MANUAL"},
    }


def test_http_transport_uses_exact_activity_and_weight_paths() -> None:
    class RecordingHTTPClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def stream(self, method: str, url: str, **kwargs: object) -> StreamContext:
            self.calls.append({"method": method, "url": url, **kwargs})
            return StreamContext(FakeResponse())

    http_client = RecordingHTTPClient()
    transport = GoogleHealthHTTPTransport(http_client=http_client)

    for data_type, expected_path in (
        ("active-energy-burned", GOOGLE_HEALTH_ACTIVE_ENERGY_BURNED_PATH),
        ("weight", GOOGLE_HEALTH_WEIGHT_PATH),
    ):
        transport.get_data_points(
            data_type=data_type,
            access_token="access-token",
            page_size=10,
            page_token=None,
            start_time=None,
            end_time=None,
        )
    assert [call["method"] for call in http_client.calls] == ["GET", "GET"]
    assert [call["url"] for call in http_client.calls] == [
        f"https://health.googleapis.com/v4{GOOGLE_HEALTH_ACTIVE_ENERGY_BURNED_PATH}",
        f"https://health.googleapis.com/v4{GOOGLE_HEALTH_WEIGHT_PATH}",
    ]


def test_http_transport_uses_official_type_specific_time_filters() -> None:
    class RecordingHTTPClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def stream(self, method: str, url: str, **kwargs: object) -> StreamContext:
            self.calls.append({"method": method, "url": url, **kwargs})
            return StreamContext(FakeResponse())

    http_client = RecordingHTTPClient()
    transport = GoogleHealthHTTPTransport(http_client=http_client)
    start = datetime(2026, 1, 2, tzinfo=UTC)
    end = datetime(2026, 1, 3, tzinfo=UTC)

    transport.get_data_points(
        data_type="active-energy-burned",
        access_token="access-token",
        page_size=10,
        page_token=None,
        start_time=start,
        end_time=end,
    )
    transport.get_data_points(
        data_type="weight",
        access_token="access-token",
        page_size=10,
        page_token=None,
        start_time=start,
        end_time=end,
    )

    assert http_client.calls[0]["params"]["filter"] == (
        'active_energy_burned.interval.start_time >= "2026-01-02T00:00:00Z" AND '
        'active_energy_burned.interval.start_time < "2026-01-03T00:00:00Z"'
    )
    assert http_client.calls[1]["params"]["filter"] == (
        'weight.sample_time.physical_time >= "2026-01-02T00:00:00Z" AND '
        'weight.sample_time.physical_time < "2026-01-03T00:00:00Z"'
    )



def test_client_parses_activity_and_weight_into_typed_pages() -> None:
    activity_transport = FakeDataTransport(FakeResponse(payload={"dataPoints": [_activity_point()]}))
    activity_page = GoogleHealthClient(activity_transport, FakeCredentials()).get_data_points_page(
        "active-energy-burned",
        start_time=datetime(2026, 1, 1, tzinfo=UTC),
        end_time=datetime(2026, 1, 3, tzinfo=UTC),
        page_token=None,
        page_size=10,
    )
    activity = activity_page.data_points[0]
    assert activity.name.endswith("/activity-1")
    assert activity.start_time == datetime(2026, 1, 2, 10, tzinfo=UTC)
    assert activity.end_time == datetime(2026, 1, 2, 11, tzinfo=UTC)
    assert activity.value == Decimal("123.45")
    assert activity.unit == "kcal"
    assert activity.start_utc_offset == "3600s"
    assert activity.data_source is not None
    assert activity.data_source.recording_method == "AUTOMATIC"

    weight_transport = FakeDataTransport(FakeResponse(payload={"dataPoints": [_weight_point()]}))
    weight_page = GoogleHealthClient(weight_transport, FakeCredentials()).get_data_points_page(
        "weight",
        start_time=datetime(2026, 1, 1, tzinfo=UTC),
        end_time=datetime(2026, 1, 3, tzinfo=UTC),
        page_token=None,
        page_size=10,
    )
    weight = weight_page.data_points[0]
    assert weight.name.endswith("/weight-1")
    assert weight.value == Decimal("72.5")
    assert weight.unit == "kilograms"
    assert weight.start_time == datetime(2026, 1, 2, 10, tzinfo=UTC)
    assert weight.end_time == weight.start_time
    assert weight.start_utc_offset == "3600s"


def test_client_rejects_unallowlisted_data_type_and_invalid_physical_bounds() -> None:
    client = GoogleHealthClient(FakeDataTransport(), FakeCredentials())
    with pytest.raises(ValueError):
        client.get_data_points_page(
            "steps",
            start_time=datetime(2026, 1, 1, tzinfo=UTC),
            end_time=datetime(2026, 1, 2, tzinfo=UTC),
            page_token=None,
            page_size=10,
        )
    with pytest.raises(ValueError):
        client.get_data_points_page(
            "weight",
            start_time=datetime(2026, 1, 2),
            end_time=None,
            page_token=None,
            page_size=10,
        )
    with pytest.raises(ValueError):
        client.get_data_points_page(
            "weight",
            start_time=datetime(2026, 1, 2, tzinfo=UTC),
            end_time=datetime(2026, 1, 1, tzinfo=UTC),
            page_token=None,
            page_size=10,
        )


@pytest.mark.parametrize("page_size", [0, 101])
def test_client_rejects_unbounded_datapoint_pages(page_size: int) -> None:
    with pytest.raises(ValueError):
        GoogleHealthClient(FakeDataTransport(), FakeCredentials()).get_data_points_page(
            "weight",
            start_time=None,
            end_time=None,
            page_token=None,
            page_size=page_size,
        )


def test_client_rejects_invalid_datapoint_token_numeric_value_and_page_length() -> None:
    with pytest.raises(ValueError):
        GoogleHealthClient(FakeDataTransport(), FakeCredentials()).get_data_points_page(
            "weight", start_time=None, end_time=None, page_token="\nunsafe", page_size=10
        )

    malformed = _weight_point()
    malformed["weight"] = {
        "sampleTime": {"physicalTime": "2026-01-02T10:00:00Z", "utcOffset": "3600s"},
        "weightGrams": "nan",
    }
    with pytest.raises(GoogleHealthInvalidResponseError):
        GoogleHealthClient(
            FakeDataTransport(FakeResponse(payload={"dataPoints": [malformed]})),
            FakeCredentials(),
        ).get_data_points_page(
            "weight", start_time=None, end_time=None, page_token=None, page_size=10
        )

    too_many = {"dataPoints": [_weight_point()] * 11}
    with pytest.raises(GoogleHealthInvalidResponseError):
        GoogleHealthClient(FakeDataTransport(FakeResponse(payload=too_many)), FakeCredentials()).get_data_points_page(
            "weight", start_time=None, end_time=None, page_token=None, page_size=10
        )


def test_client_maps_datapoint_provider_errors_without_raw_payload() -> None:
    response = FakeResponse(status_code=500, payload={"secret": "do-not-leak"})
    with pytest.raises(GoogleHealthProviderUnavailableError) as raised:
        GoogleHealthClient(FakeDataTransport(response), FakeCredentials()).get_data_points_page(
            "weight", start_time=None, end_time=None, page_token=None, page_size=10
        )
    assert "do-not-leak" not in "".join(traceback.format_exception(raised.value))
    assert raised.value.__cause__ is None


def test_client_iterates_finite_pages_and_rejects_repeated_tokens() -> None:
    class PagingTransport:
        def __init__(self, responses: list[FakeResponse]) -> None:
            self.responses = responses
            self.calls: list[dict[str, object]] = []

        def get_data_points(self, **kwargs: object) -> FakeResponse:
            self.calls.append(kwargs)
            return self.responses.pop(0)

    first = FakeResponse(payload={"dataPoints": [_weight_point()], "nextPageToken": "page-2"})
    second = FakeResponse(payload={"dataPoints": [_weight_point()]})
    transport = PagingTransport([first, second])
    client = GoogleHealthClient(transport, FakeCredentials())
    pages = list(client.iter_data_points_pages("weight", page_size=1, max_pages=2))
    assert len(pages) == 2
    assert len(transport.calls) == 2
    assert transport.calls[1]["page_token"] == "page-2"

    repeated = PagingTransport(
        [
            FakeResponse(payload={"dataPoints": [], "nextPageToken": "same"}),
            FakeResponse(payload={"dataPoints": [], "nextPageToken": "same"}),
        ]
    )
    with pytest.raises(GoogleHealthInvalidResponseError):
        list(
            GoogleHealthClient(repeated, FakeCredentials()).iter_data_points_pages(
                "weight", page_size=1, max_pages=3
            )
        )


def test_client_iterates_with_a_bounded_page_budget() -> None:
    class EndlessTransport:
        def get_data_points(self, **kwargs: object) -> FakeResponse:
            return FakeResponse(payload={"dataPoints": [], "nextPageToken": "next"})

    with pytest.raises(GoogleHealthInvalidResponseError):
        list(
            GoogleHealthClient(EndlessTransport(), FakeCredentials()).iter_data_points_pages(
                "weight", page_size=1, max_pages=2
            )
        )


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
    transport = GoogleHealthHTTPTransport(
        http_client=http_client, connect_timeout=2.0, read_timeout=7.0
    )
    start = date(2026, 1, 2)
    end = date(2026, 1, 3)

    transport.get_nutrition_log(
        access_token="access-token",
        page_size=10,
        page_token="a token/&",
        start_time=None,
        end_time=None,
        civil_start_time=start,
        civil_end_time=end,
    )

    call = http_client.calls[0]
    assert call["timeout"].connect == 2.0
    assert call["timeout"].read == 7.0
    assert call["params"] == {
        "pageSize": "10",
        "pageToken": "a token/&",
        "filter": (
            'nutrition_log.interval.civil_start_time >= "2026-01-02" AND '
            'nutrition_log.interval.civil_start_time < "2026-01-03"'
        ),
    }
    assert call["headers"] == {"Authorization": "Bearer access-token"}


def test_transport_caps_chunked_response_before_json_buffering() -> None:
    class OversizedResponse:
        status_code = 200
        headers: ClassVar[dict[str, str]] = {"content-length": "1"}

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
        client.get_nutrition_log_page(
            page_size=10,
            civil_start_time=datetime(2026, 1, 1, tzinfo=UTC),
        )
    with pytest.raises(ValueError):
        client.get_nutrition_log_page(
            page_size=10,
            civil_start_time=date(2026, 1, 2),
            civil_end_time=date(2026, 1, 1),
        )


def test_client_rejects_unsupported_physical_time_bounds() -> None:
    client = GoogleHealthClient(FakeTransport(), FakeCredentials())

    with pytest.raises(ValueError, match="civil"):
        client.get_nutrition_log_page(
            page_size=10,
            start_time=datetime(2026, 1, 2, tzinfo=UTC),
            end_time=datetime(2026, 1, 3, tzinfo=UTC),
        )


def test_transport_rejects_physical_time_bounds_before_request() -> None:
    class UnusedHTTPClient:
        def stream(self, *_args: object, **_kwargs: object) -> StreamContext:
            raise AssertionError("physical bounds must be rejected before request")

        def close(self) -> None:
            pass

    transport = GoogleHealthHTTPTransport(http_client=UnusedHTTPClient())
    with pytest.raises(ValueError, match="civil"):
        transport.get_nutrition_log(
            access_token="access-token",
            page_size=10,
            page_token=None,
            start_time=datetime(2026, 1, 2, tzinfo=UTC),
            end_time=datetime(2026, 1, 3, tzinfo=UTC),
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
def test_client_maps_provider_status_without_response_leakage(
    status: int, error_type: type[Exception]
) -> None:
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
        GoogleHealthClient(FailingTransport(), FakeCredentials()).get_nutrition_log_page(
            page_size=10
        )
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

@pytest.mark.parametrize(
    "value",
    [Decimal("0.1234567890123"), Decimal("1E+100")],
)
def test_parser_rejects_values_outside_numeric_scale_or_range(value):
    with pytest.raises(ValueError):
        _parse_nonnegative_number(value)
