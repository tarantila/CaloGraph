from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError

from app.schemas import VerificationActivityResponse, VerificationNutritionResponse


def test_activity_contract_accepts_canonical_day_records_and_serializes_numeric_values() -> None:
    response = VerificationActivityResponse(
        start_date=date(2026, 9, 14),
        end_date=date(2026, 9, 14),
        view="canonical",
        days=[
            {
                "date": date(2026, 9, 14),
                "canonical": {
                    "provider_key": "google_health",
                    "status": "available",
                    "active_energy_kcal": Decimal("321.5"),
                    "record_count": 1,
                    "source_types": ["google_health_activity_v4"],
                },
                "providers": [],
            }
        ],
    )

    assert jsonable_encoder(response) == {
        "start_date": "2026-09-14",
        "end_date": "2026-09-14",
        "view": "canonical",
        "days": [
            {
                "date": "2026-09-14",
                "canonical": {
                    "provider_key": "google_health",
                    "status": "available",
                    "active_energy_kcal": 321.5,
                    "record_count": 1,
                    "source_types": ["google_health_activity_v4"],
                },
                "providers": [],
            }
        ],
    }


def test_activity_contract_accepts_all_source_provider_records() -> None:
    response = VerificationActivityResponse(
        start_date=date(2026, 9, 14),
        end_date=date(2026, 9, 14),
        view="all",
        days=[
            {
                "date": date(2026, 9, 14),
                "canonical": None,
                "providers": [
                    {
                        "provider_key": "google_health",
                        "status": "available",
                        "active_energy_kcal": Decimal("321.5"),
                        "record_count": 1,
                        "source_types": ["google_health_activity_v4"],
                    },
                    {
                        "provider_key": "apple_health",
                        "status": "no_data",
                        "active_energy_kcal": None,
                        "record_count": 0,
                        "source_types": [],
                    },
                ],
            }
        ],
    )

    assert response.days[0].canonical is None
    assert [record.provider_key for record in response.days[0].providers] == [
        "google_health",
        "apple_health",
    ]


def test_nutrition_contract_accepts_canonical_summary_and_events() -> None:
    response = VerificationNutritionResponse(
        date=date(2026, 9, 14),
        view="canonical",
        canonical={
            "provider_key": "yazio",
            "status": "available",
            "record_count": 1,
            "summary": {
                "calories_kcal": Decimal("640.25"),
                "protein_g": Decimal("31.5"),
                "carbohydrates_g": Decimal("72"),
                "fat_g": Decimal("19"),
            },
            "events": [
                {
                    "provider_key": "yazio",
                    "occurred_at": datetime(2026, 9, 14, 12, tzinfo=UTC),
                    "meal_type": "lunch",
                    "food_name": "Lentil bowl",
                    "calories_kcal": Decimal("640.25"),
                    "protein_g": Decimal("31.5"),
                    "carbohydrates_g": Decimal("72"),
                    "fat_g": Decimal("19"),
                    "serving_amount": Decimal("1.5"),
                    "serving_unit": "portion",
                }
            ],
        },
        providers=[],
    )

    assert jsonable_encoder(response)["canonical"] == {
        "provider_key": "yazio",
        "status": "available",
        "record_count": 1,
        "summary": {
            "calories_kcal": 640.25,
            "protein_g": 31.5,
            "carbohydrates_g": 72.0,
            "fat_g": 19.0,
        },
        "events": [
            {
                "provider_key": "yazio",
                "occurred_at": "2026-09-14T12:00:00Z",
                "meal_type": "lunch",
                "food_name": "Lentil bowl",
                "calories_kcal": 640.25,
                "protein_g": 31.5,
                "carbohydrates_g": 72.0,
                "fat_g": 19.0,
                "serving_amount": 1.5,
                "serving_unit": "portion",
            }
        ],
    }


def test_nutrition_contract_accepts_all_source_provider_groups() -> None:
    response = VerificationNutritionResponse(
        date=date(2026, 9, 14),
        view="all",
        canonical=None,
        providers=[
            {
                "provider_key": "google_health",
                "status": "available",
                "record_count": 1,
                "summary": {
                    "calories_kcal": Decimal("500"),
                    "protein_g": Decimal("20"),
                    "carbohydrates_g": Decimal("60"),
                    "fat_g": Decimal("10"),
                },
                "events": [],
            },
            {
                "provider_key": "apple_health",
                "status": "no_data",
                "record_count": 0,
                "summary": {
                    "calories_kcal": None,
                    "protein_g": None,
                    "carbohydrates_g": None,
                    "fat_g": None,
                },
                "events": [],
            },
        ],
    )

    assert response.canonical is None
    assert [group.provider_key for group in response.providers] == [
        "google_health",
        "apple_health",
    ]


@pytest.mark.parametrize(
    ("response_type", "payload"),
    [
        (
            VerificationActivityResponse,
            {
                "start_date": date(2026, 9, 14),
                "end_date": date(2026, 9, 14),
                "view": "canonical",
                "days": [
                    {
                        "date": date(2026, 9, 14),
                        "canonical": {
                            "provider_key": "unknown",
                            "status": "no_data",
                            "active_energy_kcal": None,
                            "record_count": 0,
                            "source_types": [],
                        },
                        "providers": [],
                    }
                ],
            },
        ),
        (
            VerificationNutritionResponse,
            {
                "date": date(2026, 9, 14),
                "view": "canonical",
                "canonical": {
                    "provider_key": "unknown",
                    "status": "no_data",
                    "record_count": 0,
                    "summary": {
                        "calories_kcal": None,
                        "protein_g": None,
                        "carbohydrates_g": None,
                        "fat_g": None,
                    },
                    "events": [],
                },
                "providers": [],
            },
        ),
    ],
)
def test_verification_contracts_reject_unknown_provider_keys(
    response_type: type[VerificationActivityResponse] | type[VerificationNutritionResponse],
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="provider_key"):
        response_type.model_validate(payload)


@pytest.mark.parametrize(
    ("response_type", "payload"),
    [
        (
            VerificationActivityResponse,
            {
                "start_date": date(2026, 9, 14),
                "end_date": date(2026, 9, 14),
                "view": "providers",
                "days": [],
            },
        ),
        (
            VerificationNutritionResponse,
            {
                "date": date(2026, 9, 14),
                "view": "providers",
                "canonical": None,
                "providers": [],
            },
        ),
    ],
)
def test_verification_contracts_reject_invalid_view_values(
    response_type: type[VerificationActivityResponse] | type[VerificationNutritionResponse],
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="view"):
        response_type.model_validate(payload)
