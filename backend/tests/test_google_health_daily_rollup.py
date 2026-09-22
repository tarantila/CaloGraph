from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from test_google_health_client import FakeCredentials, FakeResponse

from app.google_health.client import (
    GoogleHealthClient,
    GoogleHealthHTTPTransport,
    GoogleHealthInvalidResponseError,
)
from app.services.google_health_sync import GoogleHealthSyncService


def _point(*, day: tuple[int, int, int] = (2026, 1, 2), value: object = Decimal("123.456789012")) -> dict[str, object]:
    return {
        "civilStartTime": {
            "date": {"year": day[0], "month": day[1], "day": day[2]},
            "time": {"hours": 0, "minutes": 0, "seconds": 0, "nanos": 0},
        },
        "activeEnergyBurned": {"kcalSum": value},
    }


class RollupTransport:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    def post_daily_roll_up(self, **kwargs: object) -> FakeResponse:
        self.calls.append(kwargs)
        return self.responses.pop(0)

    def get_nutrition_log(self, **kwargs: object) -> FakeResponse:
        self.calls.append(kwargs)
        return self.responses.pop(0)


def test_daily_rollup_posts_civil_range_and_preserves_exact_decimal() -> None:
    transport = RollupTransport([FakeResponse(payload={"rollupDataPoints": [_point()]})])
    page = GoogleHealthClient(transport, FakeCredentials()).get_daily_rollup_page(
        "active-energy-burned", start_date=date(2026, 1, 2), end_date=date(2026, 1, 2)
    )

    assert page.data_points[0].civil_date == date(2026, 1, 2)
    assert page.data_points[0].value == Decimal("123.456789012")
    assert transport.calls[0]["data_type"] == "active-energy-burned"
    assert transport.calls[0]["start_date"] == date(2026, 1, 2)
    assert transport.calls[0]["end_date"] == date(2026, 1, 2)
    assert transport.calls[0]["window_size_days"] == 1


def test_daily_rollup_empty_is_valid_but_missing_envelope_is_invalid() -> None:
    empty = GoogleHealthClient(
        RollupTransport([FakeResponse(payload={"rollupDataPoints": []})]), FakeCredentials()
    ).get_daily_rollup_page(
        "active-energy-burned", start_date=date(2026, 1, 2), end_date=date(2026, 1, 2)
    )
    assert empty.data_points == ()

    with pytest.raises(GoogleHealthInvalidResponseError) as raised:
        GoogleHealthClient(
            RollupTransport([FakeResponse(payload={})]), FakeCredentials()
        ).get_daily_rollup_page(
            "active-energy-burned", start_date=date(2026, 1, 2), end_date=date(2026, 1, 2)
        )
    assert raised.value.structural_reason_code == "rollup_data_points_missing"

def test_daily_rollup_paginates_and_rejects_repeated_token() -> None:
    client = GoogleHealthClient(
        RollupTransport(
            [
                FakeResponse(payload={"rollupDataPoints": [_point()], "nextPageToken": "next"}),
                FakeResponse(payload={"rollupDataPoints": [_point(day=(2026, 1, 3))]}),
            ]
        ),
        FakeCredentials(),
    )
    pages = list(
        client.iter_daily_rollup_pages(
            start_date=date(2026, 1, 2), end_date=date(2026, 1, 3), max_pages=2
        )
    )
    assert [len(page.data_points) for page in pages] == [1, 1]
    assert [page.page_index for page in pages] == [0, 1]

    with pytest.raises(GoogleHealthInvalidResponseError) as raised:
        list(
            GoogleHealthClient(
                RollupTransport(
                    [
                        FakeResponse(payload={"rollupDataPoints": [], "nextPageToken": "same"}),
                        FakeResponse(payload={"rollupDataPoints": [], "nextPageToken": "same"}),
                    ]
                ),
                FakeCredentials(),
            ).iter_daily_rollup_pages(
                start_date=date(2026, 1, 2), end_date=date(2026, 1, 2)
            )
        )
    assert raised.value.structural_reason_code == "repeated_page_token"


def test_daily_rollup_chunks_at_ninety_days() -> None:
    transport = RollupTransport(
        [FakeResponse(payload={"rollupDataPoints": []}), FakeResponse(payload={"rollupDataPoints": []})]
    )
    list(
        GoogleHealthClient(transport, FakeCredentials()).iter_daily_rollup_pages(
            start_date=date(2026, 1, 1), end_date=date(2026, 4, 1)
        )
    )
    assert [(call["start_date"], call["end_date"]) for call in transport.calls] == [
        (date(2026, 1, 1), date(2026, 3, 31)),
        (date(2026, 4, 1), date(2026, 4, 1)),
    ]


def test_daily_rollup_uses_fourteen_day_fallback_only_for_first_ninety_day_400() -> None:
    transport = RollupTransport(
        [FakeResponse(status_code=400)]
        + [FakeResponse(payload={"rollupDataPoints": []}) for _ in range(5)]
    )
    list(
        GoogleHealthClient(transport, FakeCredentials()).iter_daily_rollup_pages(
            start_date=date(2026, 1, 1), end_date=date(2026, 2, 28)
        )
    )
    assert transport.calls[0]["start_date"] == date(2026, 1, 1)
    assert transport.calls[0]["end_date"] == date(2026, 2, 28)
    assert transport.calls[1]["start_date"] == date(2026, 1, 1)
    assert transport.calls[1]["end_date"] == date(2026, 1, 14)


def test_daily_rollup_reports_civil_and_numeric_field_diagnostics() -> None:
    missing_day = _point()
    del missing_day["civilStartTime"]
    with pytest.raises(GoogleHealthInvalidResponseError) as raised:
        GoogleHealthClient(
            RollupTransport([FakeResponse(payload={"rollupDataPoints": [missing_day]})]),
            FakeCredentials(),
        ).get_daily_rollup_page(
            "active-energy-burned", start_date=date(2026, 1, 2), end_date=date(2026, 1, 2)
        )
    assert raised.value.diagnostic is not None
    assert raised.value.diagnostic.field_path == "civilStartTime.date"
    assert raised.value.diagnostic.point_index == 0

    with pytest.raises(GoogleHealthInvalidResponseError) as raised:
        GoogleHealthClient(
            RollupTransport(
                [FakeResponse(payload={"rollupDataPoints": [_point(value=Decimal("1.1234567890123"))]})]
            ),
            FakeCredentials(),
        ).get_daily_rollup_page(
            "active-energy-burned", start_date=date(2026, 1, 2), end_date=date(2026, 1, 2)
        )
    assert raised.value.diagnostic is not None
    assert raised.value.diagnostic.numeric_reason_code == "exceeds_numeric_scale_with_precision"


def test_nutrition_invalid_point_reports_safe_page_point_and_field() -> None:
    response = FakeResponse(
        payload={
            "dataPoints": [
                {
                    "nutritionLog": {
                        "interval": {
                            "startTime": "2026-01-02T10:00:00Z",
                            "endTime": "2026-01-02T11:00:00Z",
                            "startUtcOffset": "0s",
                            "endUtcOffset": "0s",
                        },
                        "nutrients": [],
                    }
                },
                {"nutritionLog": {"interval": "invalid"}},
            ]
        }
    )
    with pytest.raises(GoogleHealthInvalidResponseError) as raised:
        GoogleHealthClient(RollupTransport([response]), FakeCredentials()).get_nutrition_log_page(
            page_size=10
        )
    diagnostic = raised.value.diagnostic
    assert diagnostic is not None
    assert diagnostic.page_index == 0
    assert diagnostic.point_index == 1
    assert diagnostic.field_path == "dataPoints[1].nutritionLog.interval"
    assert "invalid" not in repr(diagnostic)


def test_daily_rollup_accepts_zero_without_treating_it_as_missing() -> None:
    page = GoogleHealthClient(
        RollupTransport([FakeResponse(payload={"rollupDataPoints": [_point(value=0)]})]),
        FakeCredentials(),
    ).get_daily_rollup_page(
        "active-energy-burned", start_date=date(2026, 1, 2), end_date=date(2026, 1, 2)
    )

    assert page.data_points[0].value == Decimal("0")


def test_http_daily_rollup_uses_post_and_civil_midnight_body() -> None:
    class Context:
        def __init__(self, response: FakeResponse) -> None:
            self.response = response

        def __enter__(self) -> FakeResponse:
            return self.response

        def __exit__(self, *_: object) -> None:
            return None

    class RecordingHTTPClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def stream(self, method: str, url: str, **kwargs: object) -> Context:
            self.calls.append({"method": method, "url": url, **kwargs})
            return Context(FakeResponse(payload={"rollupDataPoints": []}))

    http_client = RecordingHTTPClient()
    GoogleHealthHTTPTransport(http_client=http_client).post_daily_roll_up(
        data_type="active-energy-burned",
        access_token="access-token",
        start_date=date(2026, 1, 2),
        end_date=date(2026, 1, 2),
        window_size_days=1,
        page_token=None,
    )
    call = http_client.calls[0]
    assert call["method"] == "POST"
    assert str(call["url"]).endswith(":dailyRollUp")
    assert call["json"] == {
        "range": {
            "start": {
                "date": {"year": 2026, "month": 1, "day": 2},
                "time": {"hours": 0, "minutes": 0, "seconds": 0, "nanos": 0},
            },
            "end": {
                "date": {"year": 2026, "month": 1, "day": 2},
                "time": {"hours": 23, "minutes": 59, "seconds": 59, "nanos": 0},
            },
        },
        "windowSizeDays": 1,
    }


def _nutrition_point() -> dict[str, object]:
    return {
        "name": "users/me/dataTypes/nutrition-log/dataPoints/diagnostic-1",
        "nutritionLog": {
            "interval": {
                "startTime": "2026-01-02T08:00:00Z",
                "endTime": "2026-01-02T08:30:00Z",
                "startUtcOffset": "0s",
                "endUtcOffset": "0s",
            },
            "foodDisplayName": "Breakfast",
            "mealType": "BREAKFAST",
        },
    }


def test_diagnostic_nutrition_reader_is_bounded_and_read_only() -> None:
    transport = RollupTransport(
        [
            FakeResponse(
                payload={"dataPoints": [_nutrition_point()], "nextPageToken": f"page-{index}"}
            )
            for index in range(3)
        ]
    )
    result = GoogleHealthClient(transport, FakeCredentials()).read_nutrition_diagnostic()

    assert result.pages_read == 3
    assert len(result.data_points) == 3
    assert result.truncated is True
    assert [call["page_size"] for call in transport.calls] == [100, 100, 100]


def test_production_nutrition_reader_can_read_beyond_diagnostic_page_three() -> None:
    class Client:
        def __init__(self) -> None:
            self.calls = 0

        def get_nutrition_log_page(self, **_kwargs: object):
            self.calls += 1
            return type(
                "Page",
                (),
                {
                    "data_points": tuple(object() for _ in range(100)),
                    "next_page_token": f"token-{self.calls}" if self.calls < 4 else None,
                },
            )()

    service = GoogleHealthSyncService(session_factory=lambda: None)  # type: ignore[arg-type]
    points, truncated = service._pages(
        Client(),
        data_type="nutrition-log",
        start=date(2026, 1, 2),
        end=date(2026, 1, 2),
        timezone="UTC",
    )

    assert len(points) == 400
    assert truncated is False


def test_diagnostic_nutrition_reader_stops_at_first_parser_error() -> None:
    invalid = FakeResponse(payload={"dataPoints": [{"nutritionLog": {"interval": "invalid"}}]})
    valid = FakeResponse(payload={"dataPoints": [_nutrition_point()]})
    transport = RollupTransport([invalid, valid])

    with pytest.raises(GoogleHealthInvalidResponseError):
        GoogleHealthClient(transport, FakeCredentials()).read_nutrition_diagnostic()

    assert len(transport.calls) == 1
