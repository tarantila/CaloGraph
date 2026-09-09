from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest

from app.services import yazio_sdk_provider
from app.services.yazio_provider import (
    YazioConsumedProduct,
    YazioConsumedSimpleProduct,
    YazioDailyNutrientSummary,
    YazioNutrientValues,
    YazioProductProfile,
    YazioProviderInvalidResponseError,
    YazioServing,
)


@dataclass
class _Response:
    status_code: int = 200
    parsed: Any = None
    headers: dict[str, str] | None = None
    content: bytes = b"{}"

    def __post_init__(self) -> None:
        if self.headers is None:
            self.headers = {}


class _Client:
    def get_httpx_client(self) -> _Client:
        return self

    def close(self) -> None:
        pass


def _patch_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(yazio_sdk_provider, "_new_client", lambda: _Client())
    monkeypatch.setattr(yazio_sdk_provider, "_new_authenticated_client", lambda _: _Client())
    monkeypatch.setattr(
        yazio_sdk_provider.create_token,
        "sync_detailed",
        lambda **_: _Response(parsed={"access_token": "token"}),
    )


def test_food_contracts_are_immutable_and_preserve_zero() -> None:
    nutrients = YazioNutrientValues(energy=Decimal("0"), protein=None)
    serving = YazioServing(label="portion", amount=Decimal("1"), unit="portion")
    assert nutrients.energy == Decimal("0")
    assert nutrients.protein is None
    with pytest.raises(AttributeError):
        nutrients.energy = Decimal("1")  # type: ignore[misc]
    with pytest.raises(AttributeError):
        serving.label = "other"  # type: ignore[misc]


def test_sdk_maps_typed_product_simple_product_profiles_and_daily_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_auth(monkeypatch)
    calls: list[tuple[str, dict[str, Any]]] = []

    def consumed(**kwargs: Any) -> _Response:
        calls.append(("consumed", kwargs))
        return _Response(
            parsed=yazio_sdk_provider._generated_consumed_items(
                {
                    "products": [
                        {
                            "id": "event-1",
                            "product_id": "p-1",
                            "amount": 2.5,
                            "date": "2026-08-01T12:34:56+02:00",
                            "daytime": "lunch",
                            "serving": "bowl",
                            "serving_quantity": 2,
                            "unknown": "safe",
                        }
                    ],
                    "simple_products": [
                        {
                            "id": "simple-1",
                            "name": "Tea",
                            "amount": 1,
                            "date": "2026-08-01T08:10:00",
                            "energy": 0,
                            "protein": 0,
                            "mystery_nutrient": 4,
                            "mystery": "kept",
                            "token": "must-not-survive",
                        }
                    ],
                }
            )
        )
    def daily(**kwargs: Any) -> _Response:
        calls.append(("daily", kwargs))
        return _Response(
            parsed=[
                yazio_sdk_provider._generated_daily_nutrients(
                    {"date": "2026-08-01", "energy": 0, "protein": 3.2, "energy_goal": 2000}
                )
            ]
        )

    def product(product_id: str, **kwargs: Any) -> _Response:
        calls.append(("product", {"id": product_id, **kwargs}))
        return _Response(
            parsed=yazio_sdk_provider._generated_product(
                {
                    "name": "Food",
                    "producer": None,
                    "base_unit": "g",
                    "is_verified": True,
                    "is_private": False,
                    "is_deleted": False,
                    "eans": ["111", "222"],
                    "servings": [
                        {"serving": "portion", "amount": 30},
                        {"serving": "bag", "amount": 10},
                    ],
                    "nutrients": {"energy": 2, "protein": 0},
                    "unknown_profile": "safe",
                }
            )
        )

    monkeypatch.setattr(yazio_sdk_provider.list_consumed_items, "sync_detailed", consumed)
    monkeypatch.setattr(yazio_sdk_provider.get_daily_nutrients, "sync_detailed", daily)
    monkeypatch.setattr(yazio_sdk_provider.get_product, "sync_detailed", product)

    result = yazio_sdk_provider.YazioSdkProvider().fetch_food_diary(
        "owner@example.com", "password", date(2026, 8, 1), date(2026, 8, 1)
    )
    assert result.consumed_products[0].amount == Decimal("2.5")
    assert result.consumed_products[0].provider_civil_datetime == datetime(2026, 8, 1, 12, 34, 56)
    assert result.consumed_products[0].provider_timezone == "UTC+02:00"
    assert result.consumed_products[0].local_date == date(2026, 8, 1)
    assert result.consumed_simple_products[0].name == "Tea"
    assert result.consumed_simple_products[0].nutrients.energy == Decimal("0")
    assert result.consumed_simple_products[0].nutrients.additional["mystery_nutrient"] == Decimal("4")
    assert "amount" not in result.consumed_simple_products[0].nutrients.additional
    assert "serving_quantity" not in result.consumed_simple_products[0].nutrients.additional
    assert "token" not in result.consumed_simple_products[0].metadata
    assert result.consumed_simple_products[0].metadata["mystery"] == "kept"
    assert result.product_profiles[0].product_id == "p-1"
    assert result.product_profiles[0].base_unit == "g"
    assert result.product_profiles[0].is_verified is True
    assert result.product_profiles[0].is_private is False
    assert result.product_profiles[0].is_deleted is False
    assert result.product_profiles[0].eans == ("111", "222")
    assert result.product_profiles[0].servings == (
        YazioServing(label="portion", amount=Decimal("30"), unit="g"),
        YazioServing(label="bag", amount=Decimal("10"), unit="g"),
    )
    assert result.product_profiles[0].nutrients.energy == Decimal("2")
    assert result.daily_summaries[0].nutrients.energy == Decimal("0")
    assert "energy_goal" not in result.daily_summaries[0].nutrients.additional
    assert [kind for kind, _ in calls].count("consumed") == 1
    assert [kind for kind, _ in calls].count("daily") == 1
    assert [kind for kind, _ in calls].count("product") == 1
    assert calls[0][1]["date"] == "2026-08-01"
    assert {key: value for key, value in calls[1][1].items() if key != "client"} == {
        "start": "2026-08-01",
        "end": "2026-08-01",
    }


def test_food_reads_one_day_each_and_cache_distinct_product_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)
    product_calls: list[str] = []
    def consumed(**kwargs: Any) -> _Response:
        if kwargs["date"] == "2026-08-01":
            items = [{"id": "e1", "product_id": "same", "amount": 1, "date": "2026-08-01"}]
        else:
            items = [
                {"id": "e2", "product_id": "same", "amount": 2, "date": "2026-08-02"},
                {"id": "e3", "product_id": "other", "amount": 1, "date": "2026-08-02"},
            ]
        return _Response(parsed={"products": items})
    monkeypatch.setattr(yazio_sdk_provider.list_consumed_items, "sync_detailed", consumed)
    monkeypatch.setattr(
        yazio_sdk_provider.get_daily_nutrients,
        "sync_detailed",
        lambda **_: _Response(parsed=[]),
    )

    def product(product_id: str, **_: Any) -> _Response:
        product_calls.append(product_id)
        return _Response(
            parsed=yazio_sdk_provider._generated_product(
                {"base_unit": "g", "nutrients": {}, "is_verified": False, "is_private": True}
            )
        )

    monkeypatch.setattr(yazio_sdk_provider.get_product, "sync_detailed", product)
    result = yazio_sdk_provider.YazioSdkProvider().fetch_food_diary(
        "e", "p", date(2026, 8, 1), date(2026, 8, 2)
    )
    assert len(result.consumed_products) == 3
    assert product_calls == ["same", "other"]


@pytest.mark.parametrize(
    ("rows", "start_day", "end_day"),
    [
        ([{"date": "2026-07-31"}], date(2026, 8, 1), date(2026, 8, 1)),
        ([{"date": "2026-08-01"}, {"date": "2026-08-01"}], date(2026, 8, 1), date(2026, 8, 1)),
    ],
)
def test_food_rejects_daily_summary_outside_range_and_duplicates(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, Any]],
    start_day: date,
    end_day: date,
) -> None:
    _patch_auth(monkeypatch)
    monkeypatch.setattr(
        yazio_sdk_provider.list_consumed_items,
        "sync_detailed",
        lambda **_: _Response(parsed=yazio_sdk_provider._generated_consumed_items({})),
    )
    monkeypatch.setattr(
        yazio_sdk_provider.get_daily_nutrients,
        "sync_detailed",
        lambda **_: _Response(
            parsed=[yazio_sdk_provider._generated_daily_nutrients(row) for row in rows]
        ),
    )
    with pytest.raises(YazioProviderInvalidResponseError):
        yazio_sdk_provider.YazioSdkProvider().fetch_food_diary("e", "p", start_day, end_day)


def test_food_mapping_rejects_malformed_numbers_and_simple_product_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_auth(monkeypatch)
    monkeypatch.setattr(
        yazio_sdk_provider.list_consumed_items,
        "sync_detailed",
        lambda **_: _Response(parsed={"simple_products": [{"id": "x", "energy": "not-a-number"}]}),
    )
    monkeypatch.setattr(
        yazio_sdk_provider.get_daily_nutrients,
        "sync_detailed",
        lambda **_: _Response(parsed=[]),
    )
    with pytest.raises(YazioProviderInvalidResponseError):
        yazio_sdk_provider.YazioSdkProvider().fetch_food_diary("e", "p", date(2026, 8, 1), date(2026, 8, 1))

    monkeypatch.setattr(
        yazio_sdk_provider.list_consumed_items,
        "sync_detailed",
        lambda **_: _Response(parsed={"simple_products": ["not-a-mapping"]}),
    )
    with pytest.raises(YazioProviderInvalidResponseError):
        yazio_sdk_provider.YazioSdkProvider().fetch_food_diary("e", "p", date(2026, 8, 1), date(2026, 8, 1))


def test_food_optional_servings_flags_and_civil_time() -> None:
    profile = YazioProductProfile(
        product_id="p",
        name=None,
        producer=None,
        category=None,
        base_unit="ml",
        nutrients=YazioNutrientValues(),
        servings=(),
        eans=(),
        language=None,
        countries=(),
        updated_at=None,
        is_verified=False,
        is_private=True,
        is_deleted=True,
        metadata={"extra": "value"},
    )
    assert profile.servings == ()
    assert profile.is_deleted is True
    event = YazioConsumedProduct(
        consumed_item_id="e",
        product_id="p",
        amount=None,
        provider_civil_datetime=None,
        local_date=date(2026, 8, 1),
        daytime=None,
        serving=None,
        serving_quantity=None,
        metadata={},
    )
    assert event.amount is None
    assert event.local_date == date(2026, 8, 1)


def test_contract_types_have_decimal_values() -> None:
    assert YazioConsumedSimpleProduct.__dataclass_params__.frozen
    assert YazioDailyNutrientSummary.__dataclass_params__.frozen
    assert YazioNutrientValues(energy=Decimal("1")).energy == Decimal("1")
