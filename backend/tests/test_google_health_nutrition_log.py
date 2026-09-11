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


def test_unknown_meal_type_is_retained_as_a_typed_string() -> None:
    value = point("users/u/dataTypes/nutrition-log/dataPoints/future-meal-1", 1)
    value["nutritionLog"]["mealType"] = "FUTURE_MEAL_TYPE"
    nutrition_client, _ = client({"dataPoints": [value]})

    page = nutrition_client.get_nutrition_log_page(page_size=1)

    assert page.data_points[0].nutrition_log.meal_type == "FUTURE_MEAL_TYPE"
    assert len(page.data_points[0].nutrition_log.meal_type) <= 512


def test_unknown_nutrient_enum_is_retained_with_a_typed_quantity() -> None:
    value = point("users/u/dataTypes/nutrition-log/dataPoints/future-nutrient-1", 1)
    value["nutritionLog"]["nutrients"] = [
        {
            "nutrient": "FUTURE_NUTRIENT",
            "quantity": {"grams": 1, "userProvidedUnit": "GRAM"},
        }
    ]
    nutrition_client, _ = client({"dataPoints": [value]})

    page = nutrition_client.get_nutrition_log_page(page_size=1)

    nutrient = page.data_points[0].nutrition_log.nutrients[0]
    assert nutrient.nutrient == "FUTURE_NUTRIENT"
    assert len(nutrient.nutrient) <= 512
    assert nutrient.quantity.value == 1
    assert nutrient.quantity.unit == "GRAM"
    assert len(nutrient.quantity.unit) <= 512


def test_unknown_quantity_unit_is_retained_with_a_typed_quantity() -> None:
    value = point("users/u/dataTypes/nutrition-log/dataPoints/future-unit-1", 1)
    value["nutritionLog"]["nutrients"] = [
        {
            "nutrient": "PROTEIN",
            "quantity": {"grams": 1, "userProvidedUnit": "FUTURE_UNIT"},
        }
    ]
    nutrition_client, _ = client({"dataPoints": [value]})

    page = nutrition_client.get_nutrition_log_page(page_size=1)
    quantity = page.data_points[0].nutrition_log.nutrients[0].quantity
    assert quantity.value == 1
    assert quantity.unit == "FUTURE_UNIT"
    assert len(quantity.unit) <= 512


@pytest.mark.parametrize(
    ("field", "scalar_key", "unit"),
    [
        ("energy", "kcal", "FUTURE_ENERGY_UNIT"),
        ("energy_from_fat", "kcal", "FUTURE_ENERGY_FROM_FAT_UNIT"),
        ("total_carbohydrate", "grams", "FUTURE_CARBOHYDRATE_UNIT"),
        ("total_fat", "grams", "FUTURE_FAT_UNIT"),
    ],
)
def test_unknown_top_level_quantity_units_are_retained(
    field: str,
    scalar_key: str,
    unit: str,
) -> None:
    value = point("users/u/dataTypes/nutrition-log/dataPoints/future-top-level-unit-1", 1)
    payload_field = {
        "energy": "energy",
        "energy_from_fat": "energyFromFat",
        "total_carbohydrate": "totalCarbohydrate",
        "total_fat": "totalFat",
    }[field]
    value["nutritionLog"][payload_field] = {
        scalar_key: 1,
        "userProvidedUnit": unit,
    }
    nutrition_client, _ = client({"dataPoints": [value]})

    page = nutrition_client.get_nutrition_log_page(page_size=1)

    quantity = getattr(page.data_points[0].nutrition_log, field)
    assert quantity is not None
    assert quantity.value == 1
    assert quantity.unit == unit
    assert len(quantity.unit) <= 512


def test_data_source_exposes_all_allowlisted_fields_as_typed_objects() -> None:
    value = point("users/u/dataTypes/nutrition-log/dataPoints/source-1", 1)
    value["dataSource"] = {
        "recordingMethod": "AUTOMATIC",
        "platform": "ANDROID",
        "device": {
            "formFactor": "PHONE",
            "manufacturer": "Example Manufacturer",
            "displayName": "Example Device",
        },
        "application": {
            "packageName": "com.example.app",
            "webClientId": "web-client-id",
            "googleWebClientId": "google-web-client-id",
        },
    }
    nutrition_client, _ = client({"dataPoints": [value]})

    page = nutrition_client.get_nutrition_log_page(page_size=1)

    data_source = page.data_points[0].data_source
    assert data_source is not None
    assert not hasattr(data_source, "payload")
    assert not hasattr(page.data_points[0], "payload")
    assert data_source.recording_method == "AUTOMATIC"
    assert data_source.platform == "ANDROID"
    assert data_source.device is not None
    assert data_source.device.form_factor == "PHONE"
    assert data_source.device.manufacturer == "Example Manufacturer"
    assert data_source.device.display_name == "Example Device"
    assert data_source.application is not None
    assert data_source.application.package_name == "com.example.app"
    assert data_source.application.web_client_id == "web-client-id"
    assert data_source.application.google_web_client_id == "google-web-client-id"


def test_unknown_data_source_fields_are_not_retained() -> None:
    value = point("users/u/dataTypes/nutrition-log/dataPoints/source-future-1", 1)
    value["dataSource"] = {
        "platform": "ANDROID",
        "device": {"futureDeviceField": {"enabled": True}},
        "application": {"futureApplicationField": {"enabled": True}},
        "futureSourceField": {"enabled": True},
    }
    nutrition_client, _ = client({"dataPoints": [value]})

    page = nutrition_client.get_nutrition_log_page(page_size=1)

    data_source = page.data_points[0].data_source
    assert data_source is not None
    assert data_source.platform == "ANDROID"
    assert data_source.device is not None
    assert not hasattr(data_source.device, "future_device_field")
    assert data_source.application is not None
    assert not hasattr(data_source.application, "future_application_field")
    assert not hasattr(data_source, "future_source_field")
    assert not hasattr(data_source, "payload")
    assert not hasattr(page.data_points[0], "payload")


@pytest.mark.parametrize(
    "data_source",
    [
        {"recordingMethod": 1},
        {"platform": 1},
        {"device": {"formFactor": 1}},
        {"device": {"manufacturer": 1}},
        {"device": {"displayName": 1}},
        {"application": {"packageName": 1}},
        {"application": {"webClientId": 1}},
        {"application": {"googleWebClientId": 1}},
    ],
)
def test_malformed_data_source_scalar_types_are_rejected(
    data_source: dict[str, object],
) -> None:
    value = point("users/u/dataTypes/nutrition-log/dataPoints/source-bad-1", 1)
    value["dataSource"] = data_source
    nutrition_client, _ = client({"dataPoints": [value]})

    with pytest.raises(GoogleHealthInvalidResponseError):
        nutrition_client.get_nutrition_log_page(page_size=1)


@pytest.mark.parametrize(
    "nutrition_log_update",
    [
        {"mealType": 1},
        {
            "nutrients": [
                {
                    "nutrient": 1,
                    "quantity": {"grams": 1, "userProvidedUnit": "GRAM"},
                }
            ]
        },
        {
            "nutrients": [
                {
                    "nutrient": "PROTEIN",
                    "quantity": {"grams": 1, "userProvidedUnit": 1},
                }
            ]
        },
    ],
)
def test_malformed_nutrition_scalar_types_are_rejected(
    nutrition_log_update: dict[str, object],
) -> None:
    value = point("users/u/dataTypes/nutrition-log/dataPoints/source-bad-2", 1)
    value["nutritionLog"].update(nutrition_log_update)
    nutrition_client, _ = client({"dataPoints": [value]})

    with pytest.raises(GoogleHealthInvalidResponseError):
        nutrition_client.get_nutrition_log_page(page_size=1)


@pytest.mark.parametrize(
    ("field", "payload_field", "scalar_key"),
    [
        ("energy", "energy", "kcal"),
        ("nutrient", "nutrients", "grams"),
    ],
)
def test_negative_energy_or_nutrient_quantity_is_rejected(
    field: str, payload_field: str, scalar_key: str
) -> None:
    value = point("users/u/dataTypes/nutrition-log/dataPoints/negative-quantity-1", 1)
    if field == "energy":
        value["nutritionLog"][payload_field] = {
            scalar_key: -1.0,
            "userProvidedUnit": "KILOCALORIE",
        }
    else:
        value["nutritionLog"][payload_field] = [
            {
                "nutrient": "PROTEIN",
                "quantity": {"grams": -1.0, "userProvidedUnit": "GRAM"},
            }
        ]
    nutrition_client, _ = client({"dataPoints": [value]})

    with pytest.raises(GoogleHealthInvalidResponseError):
        nutrition_client.get_nutrition_log_page(page_size=1)


@pytest.mark.parametrize(
    "mutator",
    [
        pytest.param(
            lambda value: value["nutritionLog"].update({"foodDisplayName": "A" * 513}),
            id="food-display-name-ascii",
        ),
        pytest.param(
            lambda value: value["nutritionLog"].update({"foodDisplayName": "é" * 257}),
            id="food-display-name-utf8",
        ),
        pytest.param(
            lambda value: value["nutritionLog"].update(
                {
                    "serving": {
                        "amount": 1,
                        "foodMeasurementUnit": "A" * 513,
                        "foodMeasurementUnitDisplayName": "GRAM",
                    }
                }
            ),
            id="serving-food-measurement-unit-ascii",
        ),
        pytest.param(
            lambda value: value["nutritionLog"].update(
                {
                    "serving": {
                        "amount": 1,
                        "foodMeasurementUnit": "é" * 257,
                        "foodMeasurementUnitDisplayName": "GRAM",
                    }
                }
            ),
            id="serving-food-measurement-unit-utf8",
        ),
        pytest.param(
            lambda value: value["nutritionLog"].update(
                {
                    "serving": {
                        "amount": 1,
                        "foodMeasurementUnit": "GRAM",
                        "foodMeasurementUnitDisplayName": "A" * 513,
                    }
                }
            ),
            id="serving-food-measurement-unit-display-name-ascii",
        ),
        pytest.param(
            lambda value: value["nutritionLog"].update(
                {
                    "serving": {
                        "amount": 1,
                        "foodMeasurementUnit": "GRAM",
                        "foodMeasurementUnitDisplayName": "é" * 257,
                    }
                }
            ),
            id="serving-food-measurement-unit-display-name-utf8",
        ),
    ],
)
def test_oversized_food_and_serving_strings_are_rejected(mutator) -> None:
    value = point("users/u/dataTypes/nutrition-log/dataPoints/food-huge-1", 1)
    mutator(value)
    nutrition_client, _ = client({"dataPoints": [value]})

    with pytest.raises(GoogleHealthInvalidResponseError):
        nutrition_client.get_nutrition_log_page(page_size=1)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value["nutritionLog"].update({"mealType": "F" * 513}),
        lambda value: value["nutritionLog"].update(
            {
                "nutrients": [
                    {
                        "nutrient": "F" * 513,
                        "quantity": {"grams": 1, "userProvidedUnit": "GRAM"},
                    }
                ]
            }
        ),
        lambda value: value["nutritionLog"].update(
            {
                "nutrients": [
                    {
                        "nutrient": "PROTEIN",
                        "quantity": {"grams": 1, "userProvidedUnit": "F" * 513},
                    }
                ]
            }
        ),
    ],
)
def test_oversized_unknown_optional_strings_are_rejected(mutator) -> None:
    value = point("users/u/dataTypes/nutrition-log/dataPoints/source-huge-1", 1)
    mutator(value)
    nutrition_client, _ = client({"dataPoints": [value]})

    with pytest.raises(GoogleHealthInvalidResponseError):
        nutrition_client.get_nutrition_log_page(page_size=1)


@pytest.mark.parametrize(
    "data_source",
    [
        {"recordingMethod": "R" * 513},
        {"platform": "P" * 513},
        {"device": {"formFactor": "F" * 513}},
        {"device": {"manufacturer": "M" * 513}},
        {"device": {"displayName": "D" * 513}},
        {"application": {"packageName": "P" * 513}},
        {"application": {"webClientId": "W" * 513}},
        {"application": {"googleWebClientId": "G" * 513}},
    ],
)
def test_oversized_data_source_scalar_strings_are_rejected(
    data_source: dict[str, object],
) -> None:
    value = point("users/u/dataTypes/nutrition-log/dataPoints/source-huge-2", 1)
    value["dataSource"] = data_source
    nutrition_client, _ = client({"dataPoints": [value]})

    with pytest.raises(GoogleHealthInvalidResponseError):
        nutrition_client.get_nutrition_log_page(page_size=1)


@pytest.mark.parametrize(
    "mutator",
    [
        pytest.param(
            lambda value: value.update({"dataSource": {"recordingMethod": "é" * 257}}),
            id="data-source-recording-method",
        ),
        pytest.param(
            lambda value: value.update({"dataSource": {"platform": "é" * 257}}),
            id="data-source-platform",
        ),
        pytest.param(
            lambda value: value.update({"dataSource": {"device": {"formFactor": "é" * 257}}}),
            id="data-source-device-form-factor",
        ),
        pytest.param(
            lambda value: value.update({"dataSource": {"device": {"manufacturer": "é" * 257}}}),
            id="data-source-device-manufacturer",
        ),
        pytest.param(
            lambda value: value.update({"dataSource": {"device": {"displayName": "é" * 257}}}),
            id="data-source-device-display-name",
        ),
        pytest.param(
            lambda value: value.update({"dataSource": {"application": {"packageName": "é" * 257}}}),
            id="data-source-application-package-name",
        ),
        pytest.param(
            lambda value: value.update({"dataSource": {"application": {"webClientId": "é" * 257}}}),
            id="data-source-application-web-client-id",
        ),
        pytest.param(
            lambda value: value.update(
                {"dataSource": {"application": {"googleWebClientId": "é" * 257}}}
            ),
            id="data-source-application-google-web-client-id",
        ),
    ],
)
def test_utf8_oversized_optional_strings_are_rejected(mutator) -> None:
    value = point("users/u/dataTypes/nutrition-log/dataPoints/source-utf8-1", 1)
    mutator(value)
    nutrition_client, _ = client({"dataPoints": [value]})

    with pytest.raises(GoogleHealthInvalidResponseError):
        nutrition_client.get_nutrition_log_page(page_size=1)


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


def test_future_data_source_values_are_retained_but_unknown_fields_are_ignored() -> None:
    value = point("users/u/dataTypes/nutrition-log/dataPoints/future-1", 1)
    value["dataSource"] = {
        "recordingMethod": "FUTURE_RECORDING_METHOD",
        "platform": "FUTURE_PLATFORM",
        "device": {"formFactor": "FUTURE_FORM_FACTOR"},
        "futureSourceField": {"enabled": True},
    }
    nutrition_client, _ = client({"dataPoints": [value]})

    page = nutrition_client.get_nutrition_log_page(page_size=1)

    data_source = page.data_points[0].data_source
    assert data_source is not None
    assert data_source.recording_method == "FUTURE_RECORDING_METHOD"
    assert len(data_source.recording_method) <= 512
    assert data_source.platform == "FUTURE_PLATFORM"
    assert len(data_source.platform) <= 512
    assert data_source.device is not None
    assert data_source.device.form_factor == "FUTURE_FORM_FACTOR"
    assert len(data_source.device.form_factor) <= 512
    assert not hasattr(data_source, "future_source_field")


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


def test_negative_serving_amount_is_rejected() -> None:
    value = point("users/u/dataTypes/nutrition-log/dataPoints/serving-negative-1", 1)
    value["nutritionLog"]["serving"] = {
        "foodMeasurementUnit": "g",
        "foodMeasurementUnitDisplayName": "grams",
        "amount": -1,
    }
    nutrition_client, _ = client({"dataPoints": [value]})

    with pytest.raises(GoogleHealthInvalidResponseError):
        nutrition_client.get_nutrition_log_page(page_size=1)


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
