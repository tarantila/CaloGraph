import io
from contextlib import suppress
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from defusedxml.common import DefusedXmlException
from hypothesis import given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st

from app.config import settings
from app.importers import json_adapter
from app.importers import yazio as yazio_importer
from app.importers.apple_xml import parse_apple_health_xml
from app.importers.common import (
    CanonicalSample,
    decimal_value,
    local_date_for,
    normalize_value,
)
from app.importers.errors import ImportFormatError, ImportLimitError
from app.importers.json_adapter import parse_json_payload
from app.importers.yazio import parse_yazio_export

json_scalars = (
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=True, allow_infinity=True)
    | st.text(max_size=140)
)
json_values = st.recursive(
    json_scalars,
    lambda children: st.lists(children, max_size=4)
    | st.dictionaries(st.text(max_size=140), children, max_size=4),
    max_leaves=12,
)


def test_unit_conversions() -> None:
    assert normalize_value(Decimal("418.4"), "kJ", "kcal") == Decimal("100")
    assert normalize_value(Decimal("2"), "L", "ml") == Decimal("2000")
    assert normalize_value(Decimal("1.5"), "mg", "ug") == Decimal("1500.0")
    assert normalize_value(Decimal("250"), "mcg", "mg") == Decimal("0.250")


def test_decimal_values_fit_the_database_contract() -> None:
    assert decimal_value("999999999999.123456789012") == Decimal(
        "999999999999.123456789012"
    )
    assert normalize_value(Decimal("1"), "kJ", "kcal").as_tuple().exponent == -6
    with pytest.raises(ValueError, match=r"Numeric\(24,12\)"):
        decimal_value("1000000000000")
    with pytest.raises(ValueError, match=r"Numeric\(24,12\)"):
        decimal_value("0.1234567890123")
    with pytest.raises(ValueError, match="zu groß"):
        normalize_value(Decimal("999999999999"), "g", "ug")


def test_berlin_dst_and_midnight_local_date() -> None:
    before_switch = datetime(2024, 3, 30, 23, 30, tzinfo=UTC)
    after_switch = datetime(2024, 3, 31, 22, 30, tzinfo=UTC)
    assert str(local_date_for(before_switch, "Europe/Berlin")) == "2024-03-31"
    assert str(local_date_for(after_switch, "Europe/Berlin")) == "2024-04-01"


def test_fingerprint_is_deterministic() -> None:
    user_id = uuid4()
    sample = CanonicalSample(
        metric_type="protein_g",
        value=Decimal("42.0"),
        unit="g",
        original_value=Decimal("42000"),
        original_unit="mg",
        start_at=datetime(2024, 1, 1, tzinfo=UTC),
        end_at=datetime(2024, 1, 1, tzinfo=UTC),
        timezone="Europe/Berlin",
        source_type="test",
        source_name="Test",
        source_identifier="source",
        external_sample_id=None,
    )
    assert sample.fingerprint(user_id) == sample.fingerprint(user_id)


def test_health_auto_export_and_unknown_type() -> None:
    result = parse_json_payload(
        {
            "data": {
                "metrics": [
                    {
                        "name": "dietary_protein",
                        "units": "g",
                        "data": [{"qty": 30, "date": "2024-02-06 14:30:00 +0100"}],
                    },
                    {
                        "name": "unknown_private_metric",
                        "units": "x",
                        "data": [{"qty": 1, "date": "2024-02-06 14:30:00 +0100"}],
                    },
                    {
                        "name": "step_count",
                        "units": "count",
                        "data": [{"qty": 5000, "date": "2024-02-06 14:30:00 +0100"}],
                    },
                    {
                        "name": "HKQuantityTypeIdentifierBodyMass",
                        "units": "kg",
                        "data": [{"qty": 80, "date": "2024-02-06 14:30:00 +0100"}],
                    },
                ]
            }
        },
        "Europe/Berlin",
    )
    assert result.received == 4
    assert len(result.samples) == 1
    assert result.unknown_types == {"unknown_private_metric"}
    assert result.unknown_count == 3


def test_adapter_caps_materialized_errors_and_unknown_types(monkeypatch) -> None:
    monkeypatch.setattr(settings, "max_import_errors", 2)
    monkeypatch.setattr(settings, "max_import_unknown_types", 2)
    result = parse_json_payload(
        {
            "samples": [
                {
                    "type": "dietary_energy",
                    "value": "invalid",
                    "start_at": "2026-07-20T12:00:00+00:00",
                }
                for _ in range(5)
            ]
            + [
                {
                    "type": f"unknown-{index}",
                    "value": 1,
                    "start_at": "2026-07-20T12:00:00+00:00",
                }
                for index in range(5)
            ]
        },
        "Europe/Berlin",
    )

    assert result.failed_count == 5
    assert len(result.errors) == 2
    assert result.unknown_count == 5
    assert len(result.unknown_types) == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"samples": [{}, {}, {}]},
        {"metrics": [{"name": "dietary_energy", "data": [{}, {}, {}]}]},
        {
            "data": {
                "metrics": [{"name": "dietary_energy", "data": [{}, {}, {}]}]
            }
        },
    ],
)
def test_record_limits_are_checked_before_pydantic_materialization(
    payload: dict[str, object],
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "max_import_records", 2)

    def unexpected_validation(_cls, _payload):
        raise AssertionError("Pydantic validation must not run above the record limit")

    monkeypatch.setattr(
        json_adapter.CalographPayloadInput,
        "model_validate",
        unexpected_validation,
    )
    monkeypatch.setattr(
        json_adapter.HealthAutoPayloadInput,
        "model_validate",
        unexpected_validation,
    )
    monkeypatch.setattr(
        json_adapter.HealthAutoEnvelopeInput,
        "model_validate",
        unexpected_validation,
    )

    with pytest.raises(ImportLimitError):
        parse_json_payload(payload, "Europe/Berlin")


@pytest.mark.parametrize("invalid_item", ["scalar", 1, None, []])
def test_json_adapters_reject_non_object_list_items(invalid_item: object) -> None:
    with pytest.raises(ImportFormatError):
        parse_json_payload({"samples": [invalid_item]}, "Europe/Berlin")
    with pytest.raises(ImportFormatError):
        parse_json_payload({"metrics": [invalid_item]}, "Europe/Berlin")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_name", "x" * 191),
        ("source_identifier", "x" * 256),
        ("id", "x" * 256),
        ("unit", "x" * 65),
        ("timezone", "x" * 65),
    ],
)
def test_json_adapter_rejects_long_database_fields(
    field: str,
    value: str,
) -> None:
    with pytest.raises(ImportFormatError):
        parse_json_payload(
            {
                "samples": [
                    {
                        "type": "dietary_energy",
                        "value": 1,
                        "start_at": "2026-07-20T12:00:00+00:00",
                        field: value,
                    }
                ]
            },
            "Europe/Berlin",
        )


def test_invalid_sample_timezone_is_safe_partial_error() -> None:
    secret_timezone = "Mars/secret-timezone"
    result = parse_json_payload(
        {
            "samples": [
                {
                    "type": "dietary_energy",
                    "value": 1,
                    "start_at": "2026-07-20T12:00:00+00:00",
                    "timezone": secret_timezone,
                }
            ]
        },
        "Europe/Berlin",
    )

    assert result.failed_count == 1
    assert result.samples == []
    assert secret_timezone not in result.errors[0][3]


@hypothesis_settings(max_examples=75, deadline=None)
@given(st.lists(json_values, max_size=6))
def test_calograph_adapter_never_leaks_unexpected_exceptions(samples: list[object]) -> None:
    with suppress(ImportFormatError):
        parse_json_payload({"samples": samples}, "Europe/Berlin")


@hypothesis_settings(max_examples=75, deadline=None)
@given(st.lists(json_values, max_size=6))
def test_health_auto_adapter_never_leaks_unexpected_exceptions(
    metrics: list[object],
) -> None:
    with suppress(ImportFormatError):
        parse_json_payload({"metrics": metrics}, "Europe/Berlin")


@hypothesis_settings(max_examples=75, deadline=None)
@given(st.dictionaries(st.text(max_size=140), json_values, max_size=6))
def test_yazio_adapter_never_leaks_unexpected_exceptions(
    payload: dict[str, object],
) -> None:
    with suppress(ImportFormatError):
        parse_yazio_export(payload, "Europe/Berlin")


def test_xml_entities_are_rejected() -> None:
    xml = b'<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY x "boom">]><HealthData><Record type="HKQuantityTypeIdentifierDietaryProtein" value="&x;" unit="g" startDate="2024-01-01 10:00:00 +0100" /></HealthData>'
    with pytest.raises(DefusedXmlException):
        parse_apple_health_xml(io.BytesIO(xml), "Europe/Berlin")


def test_yazio_days_export_is_aggregated_without_meal_details() -> None:
    result = parse_yazio_export(
        {
            "2026-07-20": {
                "daily_summary": {
                    "activity_energy": 321,
                    "steps": 8421,
                    "water_intake": 2100,
                    "units": {"unit_energy": "kcal"},
                    "meals": {
                        "breakfast": {
                            "nutrients": {
                                "energy.energy": 450,
                                "nutrient.protein": 25,
                                "nutrient.carb": 60,
                                "nutrient.fat": 15,
                            }
                        },
                        "dinner": {
                            "nutrients": {
                                "energy.energy": 800,
                                "nutrient.protein": 55,
                                "nutrient.carb": 90,
                                "nutrient.fat": 30,
                            }
                        },
                    },
                }
            }
        },
        "Europe/Berlin",
        "test-account",
    )

    by_metric = {sample.metric_type: sample for sample in result.samples}
    assert result.received == 4
    assert result.errors == []
    assert by_metric["dietary_energy_kcal"].value == Decimal("1250")
    assert by_metric["protein_g"].value == Decimal("80")
    assert by_metric["carbohydrates_g"].value == Decimal("150")
    assert by_metric["fat_g"].value == Decimal("45")
    assert "active_energy_kcal" not in by_metric
    assert "steps" not in by_metric
    assert "water_ml" not in by_metric
    assert by_metric["protein_g"].external_sample_id == "2026-07-20:protein_g"
    assert by_metric["protein_g"].source_identifier == "test-account"


def test_yazio_binary_float_artifacts_are_rounded_to_database_precision() -> None:
    result = parse_yazio_export(
        {
            "2026-07-29": {
                "daily_summary": {
                    "meals": {
                        "lunch": {
                            "nutrients": {
                                "energy.energy": 222.75000000000003,
                                "nutrient.protein": 13.750000000000002,
                                "nutrient.carb": 21.450000000000003,
                                "nutrient.fat": 7.700000000000001,
                            }
                        }
                    }
                }
            }
        },
        "Europe/Berlin",
    )

    by_metric = {sample.metric_type: sample for sample in result.samples}
    assert result.received == 4
    assert result.errors == []
    assert by_metric["dietary_energy_kcal"].original_value == Decimal(
        "222.750000000000"
    )
    assert by_metric["protein_g"].value == Decimal("13.750000")
    assert by_metric["carbohydrates_g"].value == Decimal("21.450000")
    assert by_metric["fat_g"].value == Decimal("7.700000")


@pytest.mark.parametrize(
    "unsafe_value",
    [-0.1, float("nan"), float("inf"), 1_000_000_000_000.0, "0.1234567890123"],
)
def test_yazio_unsafe_values_remain_rejected(unsafe_value: object) -> None:
    result = parse_yazio_export(
        {
            "2026-07-29": {
                "daily_summary": {
                    "meals": {
                        "lunch": {
                            "nutrients": {
                                "energy.energy": unsafe_value,
                            }
                        }
                    }
                }
            }
        },
        "Europe/Berlin",
    )

    assert result.samples == []
    assert result.failed_count == 1
    assert result.errors[0][2] == "invalid_day"


def test_yazio_flat_day_format_and_kilojoules_are_supported() -> None:
    result = parse_yazio_export(
        {
            "days": {
                "2026-07-21": {
                    "energy": 4184,
                    "protein": 100,
                    "carb": 200,
                    "fat": 70,
                    "units": {"unit_energy": "kJ"},
                }
            }
        },
        "Europe/Berlin",
    )
    by_metric = {sample.metric_type: sample.value for sample in result.samples}
    assert by_metric["dietary_energy_kcal"] == Decimal("1000")
    assert by_metric["protein_g"] == Decimal("100")


def test_yazio_empty_nutrition_day_omits_zero_placeholders() -> None:
    result = parse_yazio_export(
        {
            "2026-07-19": {
                "daily_summary": {
                    "activity_energy": 120,
                    "steps": 3400,
                    "water_intake": 0,
                    "meals": {
                        "breakfast": {
                            "nutrients": {
                                "energy.energy": 0,
                                "nutrient.protein": 0,
                                "nutrient.carb": 0,
                                "nutrient.fat": 0,
                            }
                        }
                    },
                }
            }
        },
        "Europe/Berlin",
    )

    assert result.samples == []
    assert result.received == 0
    assert result.errors == []


def test_yazio_micronutrients_are_imported_with_canonical_units() -> None:
    result = parse_yazio_export(
        {
            "vitamin.d": {"2026-07-20": 0.0000125},
            "mineral.iron": {"2026-07-20": 0.0084},
            "mineral.calcium": {"2026-07-20": 0.1525},
            "unknown.nutrient": {"2026-07-20": 99},
        },
        "Europe/Berlin",
    )

    by_metric = {sample.metric_type: sample for sample in result.samples}
    assert by_metric["vitamin_d_ug"].value == Decimal("12.5")
    assert by_metric["vitamin_d_ug"].unit == "ug"
    assert by_metric["vitamin_d_ug"].original_value == Decimal("0.0000125")
    assert by_metric["vitamin_d_ug"].original_unit == "g"
    assert by_metric["iron_mg"].value == Decimal("8.4")
    assert by_metric["iron_mg"].unit == "mg"
    assert by_metric["calcium_mg"].value == Decimal("152.5")
    assert "unknown.nutrient" not in by_metric


def test_yazio_export_rejects_payload_without_dated_entries() -> None:
    with pytest.raises(ValueError, match="keine Tagesdaten"):
        parse_yazio_export({"profile": {"name": "not imported"}}, "Europe/Berlin")


@pytest.mark.parametrize(
    "payload",
    [
        {
            "unsupported-a": {},
            "unsupported-b": {},
            "unsupported-c": {},
        },
        {
            "nutrients": {
                "unsupported": {
                    "entry-a": 1,
                    "entry-b": 2,
                    "entry-c": 3,
                }
            }
        },
    ],
)
def test_yazio_limits_all_raw_entries_before_pydantic_materialization(
    payload: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "max_import_records", 2)

    def unexpected_validation(_cls, _payload):
        raise AssertionError("Pydantic validation must not run above the record limit")

    monkeypatch.setattr(
        yazio_importer.YazioExportRootInput,
        "model_validate",
        unexpected_validation,
    )

    with pytest.raises(ImportLimitError):
        parse_yazio_export(payload, "Europe/Berlin")
