from __future__ import annotations

from datetime import date, datetime
from typing import ClassVar

import pytest

from app.google_health.client import (
    GoogleHealthClient,
    GoogleHealthInvalidResponseError,
    NutritionLogDataPoint,
    NutritionLogPage,
)


class Credentials:
    valid = True
    expired = False
    token = "token"


class Response:
    status_code = 200
    headers: ClassVar[dict[str, str]] = {"content-type": "application/json"}

    def __init__(self, payload: object) -> None:
        self.payload = payload

    def json(self) -> object:
        return self.payload


class Transport:
    def __init__(self, payload: object) -> None:
        self.response = Response(payload)
        self.calls: list[dict[str, object]] = []

    def get_nutrition_log(self, **kwargs: object) -> Response:
        self.calls.append(kwargs)
        return self.response


def interval(day: int, *, civil_day: int | None = None) -> dict[str, object]:
    civil_day = civil_day or day
    return {
        "startTime": f"2026-01-{day:02d}T08:00:00Z",
        "endTime": f"2026-01-{day:02d}T08:30:00Z",
        "startUtcOffset": "0s",
        "endUtcOffset": "0s",
        "civilStartTime": {
            "date": {"year": 2026, "month": 1, "day": civil_day},
            "time": {"hours": 8, "minutes": 0, "seconds": 0, "nanos": 0},
        },
        "civilEndTime": {
            "date": {"year": 2026, "month": 1, "day": civil_day},
            "time": {"hours": 8, "minutes": 30, "seconds": 0, "nanos": 0},
        },
    }


def point(name: str, day: int) -> dict[str, object]:
    return {
        "name": name,
        "nutritionLog": {
            "interval": interval(day),
            "foodDisplayName": "Breakfast",
            "mealType": "BREAKFAST",
        },
    }


def client(payload: object, *, max_page_size: int = 100) -> tuple[GoogleHealthClient, Transport]:
    transport = Transport(payload)
    return GoogleHealthClient(transport, Credentials(), max_page_size=max_page_size), transport


def test_valid_page_is_typed_and_contains_only_validated_dtos() -> None:
    value = point(
        "users/user-id-42/dataTypes/nutrition-log/dataPoints/point-1",
        1,
    )
    value["dataSource"] = {"platform": "GOOGLE_WEB_API"}
    value["nutritionLog"]["nutrients"] = [
        {
            "nutrient": "FOLATE",
            "quantity": {"grams": 1, "userProvidedUnit": "GRAM"},
        }
    ]
    nutrition_client, _ = client({"dataPoints": [value], "nextPageToken": "next"})

    page = nutrition_client.get_nutrition_log_page(page_size=10)

    assert isinstance(page, NutritionLogPage)
    assert isinstance(page.data_points[0], NutritionLogDataPoint)
    assert page.data_points[0].name == "users/user-id-42/dataTypes/nutrition-log/dataPoints/point-1"
    assert page.data_points[0].nutrition_log.food_display_name == "Breakfast"
    assert page.data_points[0].nutrition_log.nutrients[0].nutrient == "FOLATE"
    assert page.data_points[0].nutrition_log.interval.civil_start_time == datetime(2026, 1, 1, 8)
    assert page.next_page_token == "next"
    assert page.page_size == 10
    assert not hasattr(page, "payload")


def test_empty_list_response_without_optional_data_points_is_valid() -> None:
    nutrition_client, _ = client({})

    page = nutrition_client.get_nutrition_log_page(page_size=1)

    assert page.data_points == ()
    assert page.next_page_token is None


def test_unused_page_fields_are_ignored() -> None:
    nutrition_client, _ = client({"futurePageMetadata": {"version": 2}})

    page = nutrition_client.get_nutrition_log_page(page_size=1)

    assert page.data_points == ()
    assert page.next_page_token is None


def test_future_data_source_values_and_fields_are_ignored() -> None:
    value = point("users/u/dataTypes/nutrition-log/dataPoints/future-1", 1)
    value["dataSource"] = {
        "platform": "FUTURE_PLATFORM",
        "futureSourceField": {"enabled": True},
    }
    nutrition_client, _ = client({"dataPoints": [value]})

    page = nutrition_client.get_nutrition_log_page(page_size=1)

    assert len(page.data_points) == 1


def test_future_interval_fields_are_ignored() -> None:
    value = point("users/u/dataTypes/nutrition-log/dataPoints/future-2", 1)
    nutrition_interval = value["nutritionLog"]["interval"]
    assert isinstance(nutrition_interval, dict)
    nutrition_interval["futureIntervalField"] = {"enabled": True}
    civil_start = nutrition_interval["civilStartTime"]
    assert isinstance(civil_start, dict)
    civil_start["futureCivilField"] = "ignored"
    civil_date = civil_start["date"]
    assert isinstance(civil_date, dict)
    civil_date["futureDateField"] = 1
    civil_time = civil_start["time"]
    assert isinstance(civil_time, dict)
    civil_time["futureTimeField"] = 1

    nutrition_client, _ = client({"dataPoints": [value]})

    page = nutrition_client.get_nutrition_log_page(page_size=1)

    assert len(page.data_points) == 1


def test_empty_data_point_name_is_valid() -> None:
    nutrition_client, _ = client({"dataPoints": [point("", 1)]})

    page = nutrition_client.get_nutrition_log_page(page_size=1)

    assert page.data_points[0].name == ""


@pytest.mark.parametrize("page_size", [0, -1, 101])
def test_page_size_is_bounded(page_size: int) -> None:
    nutrition_client, _ = client({"dataPoints": []}, max_page_size=100)

    with pytest.raises(ValueError):
        nutrition_client.get_nutrition_log_page(page_size=page_size)


def test_configured_page_size_is_enforced() -> None:
    nutrition_client, _ = client({"dataPoints": []}, max_page_size=2)

    with pytest.raises(ValueError):
        nutrition_client.get_nutrition_log_page(page_size=3)


def test_page_rejects_more_points_than_requested() -> None:
    nutrition_client, _ = client({"dataPoints": [point("a", 1), point("b", 2)]})

    with pytest.raises(GoogleHealthInvalidResponseError):
        nutrition_client.get_nutrition_log_page(page_size=1)


def test_next_page_token_can_be_absent_or_empty_but_invalid_tokens_are_rejected() -> None:
    for payload in ({"dataPoints": []}, {"dataPoints": [], "nextPageToken": ""}):
        nutrition_client, _ = client(payload)
        assert nutrition_client.get_nutrition_log_page(page_size=1).next_page_token is None

    nutrition_client, _ = client({"dataPoints": [], "nextPageToken": "bad\nvalue"})
    with pytest.raises(GoogleHealthInvalidResponseError):
        nutrition_client.get_nutrition_log_page(page_size=1)


def test_civil_time_filter_is_inclusive_start_and_exclusive_end() -> None:
    payload = {
        "dataPoints": [
            point("users/u/dataTypes/nutrition-log/dataPoints/one-1", 1),
            point("users/u/dataTypes/nutrition-log/dataPoints/two-1", 2),
            point("users/u/dataTypes/nutrition-log/dataPoints/three-1", 3),
        ]
    }
    nutrition_client, transport = client(payload)

    page = nutrition_client.get_nutrition_log_page(
        page_size=10,
        civil_start_time=date(2026, 1, 2),
        civil_end_time=date(2026, 1, 3),
    )

    assert [item.name for item in page.data_points] == [
        "users/u/dataTypes/nutrition-log/dataPoints/two-1"
    ]
    assert transport.calls[0]["civil_start_time"] == date(2026, 1, 2)
    assert transport.calls[0]["civil_end_time"] == date(2026, 1, 3)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "dataPoints": [
                {
                    "name": "users/u/dataTypes/nutrition-log/dataPoints/bad-1",
                    "hydrationLog": {"interval": interval(1)},
                }
            ]
        },
        {
            "dataPoints": [
                {"name": "users/u/dataTypes/nutrition-log/dataPoints/bad-1", "nutritionLog": {}}
            ]
        },
        {
            "dataPoints": [
                {
                    "name": "users/u/dataTypes/nutrition-log/dataPoints/bad-1",
                    "nutritionLog": {"interval": {**interval(1), "startTime": "nope"}},
                }
            ]
        },
        {
            "dataPoints": [
                {
                    "name": "users/u/dataTypes/nutrition-log/dataPoints/bad-1",
                    "nutritionLog": {
                        "interval": {**interval(1), "endTime": "2026-01-01T07:00:00Z"}
                    },
                }
            ]
        },
        {
            "dataPoints": [
                {
                    "name": "users/u/dataTypes/nutrition-log/dataPoints/bad-1",
                    "nutritionLog": {"interval": interval(1), "mealType": "UNKNOWN_MEAL"},
                }
            ]
        },
        {"dataPoints": "not-a-list"},
    ],
)
def test_malformed_provider_shapes_are_rejected(payload: object) -> None:
    nutrition_client, _ = client(payload)

    with pytest.raises(GoogleHealthInvalidResponseError):
        nutrition_client.get_nutrition_log_page(page_size=10)


def test_civil_time_can_reverse_during_timezone_fall_back() -> None:
    value = point("users/u/dataTypes/nutrition-log/dataPoints/fallback-1", 1)
    cast_interval = value["nutritionLog"]["interval"]
    assert isinstance(cast_interval, dict)
    cast_interval["startUtcOffset"] = "7200s"
    cast_interval["endTime"] = "2026-01-01T09:00:00Z"
    cast_interval["civilStartTime"] = {
        "date": {"year": 2026, "month": 1, "day": 1},
        "time": {"hours": 10, "minutes": 0, "seconds": 0, "nanos": 0},
    }
    cast_interval["civilEndTime"] = {
        "date": {"year": 2026, "month": 1, "day": 1},
        "time": {"hours": 9, "minutes": 0, "seconds": 0, "nanos": 0},
    }
    nutrition_client, _ = client({"dataPoints": [value]})

    page = nutrition_client.get_nutrition_log_page(page_size=1)

    assert len(page.data_points) == 1


def test_civil_datetime_filter_preserves_subsecond_precision() -> None:
    nutrition_client, transport = client({"dataPoints": []})
    bound = datetime(2026, 1, 2, 3, 4, 5, 123456)

    nutrition_client.get_nutrition_log_page(page_size=1, civil_start_time=bound)

    assert transport.calls[0]["civil_start_time"] == bound


def test_submicrosecond_timestamps_and_offsets_are_validated_exactly() -> None:
    value = point("users/u/dataTypes/nutrition-log/dataPoints/nanos-1", 1)
    cast_interval = value["nutritionLog"]["interval"]
    assert isinstance(cast_interval, dict)
    cast_interval["startTime"] = "2026-01-01T08:00:00.000000001Z"
    cast_interval["endTime"] = "2026-01-01T08:00:00.000000002Z"
    cast_interval["startUtcOffset"] = "0.000000001s"
    cast_interval["endUtcOffset"] = "0.000000001s"
    cast_interval["civilStartTime"] = {
        "date": {"year": 2026, "month": 1, "day": 1},
        "time": {"hours": 8, "minutes": 0, "seconds": 0, "nanos": 2},
    }
    cast_interval["civilEndTime"] = {
        "date": {"year": 2026, "month": 1, "day": 1},
        "time": {"hours": 8, "minutes": 0, "seconds": 0, "nanos": 3},
    }
    nutrition_client, _ = client({"dataPoints": [value]})

    page = nutrition_client.get_nutrition_log_page(page_size=1)
    assert page.data_points[0].nutrition_log.interval.start_time.microsecond == 0


def test_invalid_food_resource_name_is_rejected() -> None:
    value = point("users/u/dataTypes/nutrition-log/dataPoints/food-1", 1)
    value["nutritionLog"]["food"] = "arbitrary-food"
    nutrition_client, _ = client({"dataPoints": [value]})

    with pytest.raises(GoogleHealthInvalidResponseError):
        nutrition_client.get_nutrition_log_page(page_size=1)


@pytest.mark.parametrize("field", ["energy", "totalFat"])
def test_oversized_quantity_integer_is_rejected(field: str) -> None:
    value = point("users/u/dataTypes/nutrition-log/dataPoints/huge-1", 1)
    scalar = "kcal" if field == "energy" else "grams"
    value["nutritionLog"][field] = {scalar: 10**400}
    nutrition_client, _ = client({"dataPoints": [value]})

    with pytest.raises(GoogleHealthInvalidResponseError):
        nutrition_client.get_nutrition_log_page(page_size=1)


def test_physical_and_civil_bounds_cannot_be_mixed() -> None:
    nutrition_client, _ = client({"dataPoints": []})

    with pytest.raises(ValueError):
        nutrition_client.get_nutrition_log_page(
            page_size=1,
            start_time=datetime(2026, 1, 1, tzinfo=__import__("datetime").UTC),
            civil_start_time=date(2026, 1, 1),
        )


def test_reversed_civil_query_bounds_are_rejected() -> None:
    nutrition_client, _ = client({"dataPoints": []})

    with pytest.raises(ValueError):
        nutrition_client.get_nutrition_log_page(
            page_size=1,
            civil_start_time=date(2026, 1, 2),
            civil_end_time=date(2026, 1, 1),
        )


def test_year_one_negative_offset_underflow_is_rejected() -> None:
    value = point("users/u/dataTypes/nutrition-log/dataPoints/year-one-1", 1)
    cast_interval = value["nutritionLog"]["interval"]
    assert isinstance(cast_interval, dict)
    cast_interval["startTime"] = "0001-01-01T00:00:00+00:01"
    cast_interval["endTime"] = "0001-01-01T00:00:00.000000001+00:01"
    cast_interval["startUtcOffset"] = "-0.000000001s"
    cast_interval["endUtcOffset"] = "0s"
    cast_interval["civilStartTime"] = {
        "date": {"year": 1, "month": 1, "day": 1},
        "time": {"hours": 0, "minutes": 0, "seconds": 0, "nanos": 0},
    }
    nutrition_client, _ = client({"dataPoints": [value]})

    with pytest.raises(GoogleHealthInvalidResponseError):
        nutrition_client.get_nutrition_log_page(page_size=1)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda item: item.update({"name": "unsafe"}),
        lambda item: item.update(
            {
                "dataSource": {
                    "device": {"formFactor": 1},
                }
            }
        ),
        lambda item: item["nutritionLog"].update(
            {
                "energy": {
                    "kcal": 1,
                    "userProvidedUnit": "POUND",
                }
            }
        ),
        lambda item: item["nutritionLog"].update(
            {
                "totalFat": {
                    "grams": 1,
                    "userProvidedUnit": "KILOCALORIE",
                }
            }
        ),
    ],
)
def test_invalid_wrapper_or_cross_type_quantity_is_rejected(mutator) -> None:
    value = point("users/u/dataTypes/nutrition-log/dataPoints/bad-1", 1)
    mutator(value)
    nutrition_client, _ = client({"dataPoints": [value]})

    with pytest.raises(GoogleHealthInvalidResponseError):
        nutrition_client.get_nutrition_log_page(page_size=1)


def test_serving_amount_without_unit_is_a_valid_typed_value() -> None:
    value = point("users/u/dataTypes/nutrition-log/dataPoints/serving-1", 1)
    value["nutritionLog"]["serving"] = {"amount": 1}
    nutrition_client, _ = client({"dataPoints": [value]})

    page = nutrition_client.get_nutrition_log_page(page_size=1)

    assert page.data_points[0].nutrition_log.serving is not None
    assert page.data_points[0].nutrition_log.serving.amount == 1


def test_contradictory_civil_and_physical_interval_is_rejected() -> None:
    value = point("users/u/dataTypes/nutrition-log/dataPoints/bad-2", 1)
    cast_interval = value["nutritionLog"]["interval"]
    assert isinstance(cast_interval, dict)
    cast_interval["civilStartTime"] = {
        "date": {"year": 2026, "month": 1, "day": 1},
        "time": {"hours": 9, "minutes": 0, "seconds": 0, "nanos": 0},
    }
    nutrition_client, _ = client({"dataPoints": [value]})

    with pytest.raises(GoogleHealthInvalidResponseError):
        nutrition_client.get_nutrition_log_page(page_size=1)
