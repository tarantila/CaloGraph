from __future__ import annotations

from datetime import date, datetime

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
    headers = {"content-type": "application/json"}

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
    nutrition_client, _ = client({"dataPoints": [point("users/u/dataTypes/nutrition-log/dataPoints/point-1", 1)], "nextPageToken": "next"})

    page = nutrition_client.get_nutrition_log_page(page_size=10)

    assert isinstance(page, NutritionLogPage)
    assert isinstance(page.data_points[0], NutritionLogDataPoint)
    assert page.data_points[0].name == "users/u/dataTypes/nutrition-log/dataPoints/point-1"
    assert page.data_points[0].nutrition_log.food_display_name == "Breakfast"
    assert page.data_points[0].nutrition_log.interval.civil_start_time == datetime(2026, 1, 1, 8)
    assert page.next_page_token == "next"
    assert page.page_size == 10
    assert not hasattr(page, "payload")


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
        {"dataPoints": [{"name": "users/u/dataTypes/nutrition-log/dataPoints/bad-1", "hydrationLog": {"interval": interval(1)}}]},
        {"dataPoints": [{"name": "users/u/dataTypes/nutrition-log/dataPoints/bad-1", "nutritionLog": {}}]},
        {"dataPoints": [{"name": "users/u/dataTypes/nutrition-log/dataPoints/bad-1", "nutritionLog": {"interval": {**interval(1), "startTime": "nope"}}}]},
        {"dataPoints": [{"name": "users/u/dataTypes/nutrition-log/dataPoints/bad-1", "nutritionLog": {"interval": {**interval(1), "endTime": "2026-01-01T07:00:00Z"}}}]},
        {"dataPoints": [{"name": "users/u/dataTypes/nutrition-log/dataPoints/bad-1", "nutritionLog": {"interval": interval(1), "unexpected": True}}]},
        {"dataPoints": [{"name": "users/u/dataTypes/nutrition-log/dataPoints/bad-1", "nutritionLog": {"interval": interval(1), "mealType": "BRUNCH"}}]},
        {"dataPoints": "not-a-list"},
    ],
)
def test_malformed_provider_shapes_are_rejected(payload: object) -> None:
    nutrition_client, _ = client(payload)

    with pytest.raises(GoogleHealthInvalidResponseError):
        nutrition_client.get_nutrition_log_page(page_size=10)


def test_civil_bounds_require_ordered_supported_values() -> None:
    nutrition_client, _ = client({"dataPoints": []})

    with pytest.raises(ValueError):
        nutrition_client.get_nutrition_log_page(
            page_size=1,
            civil_start_time=date(2026, 1, 3),
            civil_end_time=date(2026, 1, 2),
        )


def test_civil_datetime_filter_preserves_subsecond_precision() -> None:
    nutrition_client, transport = client({"dataPoints": []})
    bound = datetime(2026, 1, 2, 3, 4, 5, 123456)

    nutrition_client.get_nutrition_log_page(page_size=1, civil_start_time=bound)

    assert transport.calls[0]["civil_start_time"] == bound


@pytest.mark.parametrize(
    "mutator",
    [
        lambda item: item.update({"name": "unsafe"}),
        lambda item: item.update(
            {
                "dataSource": {
                    "device": {"unknown": "value"},
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
