import io
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from defusedxml.common import DefusedXmlException

from app.importers.apple_xml import parse_apple_health_xml
from app.importers.common import CanonicalSample, local_date_for, normalize_value
from app.importers.json_adapter import parse_json_payload
from app.importers.yazio import parse_yazio_export


def test_unit_conversions() -> None:
    assert normalize_value(Decimal("418.4"), "kJ", "kcal") == Decimal("100")
    assert normalize_value(Decimal("2"), "L", "ml") == Decimal("2000")
    assert normalize_value(Decimal("1.5"), "mg", "ug") == Decimal("1500.0")
    assert normalize_value(Decimal("250"), "mcg", "mg") == Decimal("0.250")


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
    assert result.errors[0][2] == "empty_day"


def test_yazio_micronutrients_are_imported_with_canonical_units() -> None:
    result = parse_yazio_export(
        {
            "vitamin.d": {"2026-07-20": 12.5},
            "mineral.iron": {"2026-07-20": 8.4},
            "unknown.nutrient": {"2026-07-20": 99},
        },
        "Europe/Berlin",
    )

    by_metric = {sample.metric_type: sample for sample in result.samples}
    assert by_metric["vitamin_d_ug"].value == Decimal("12.5")
    assert by_metric["vitamin_d_ug"].unit == "ug"
    assert by_metric["iron_mg"].value == Decimal("8.4")
    assert by_metric["iron_mg"].unit == "mg"
    assert "unknown.nutrient" not in by_metric


def test_yazio_export_rejects_payload_without_dated_entries() -> None:
    with pytest.raises(ValueError, match="keine Tagesdaten"):
        parse_yazio_export({"profile": {"name": "not imported"}}, "Europe/Berlin")
