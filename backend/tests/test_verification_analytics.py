from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.activity import ACTIVE_ENERGY_METRIC, ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS
from app.analytics import verification
from app.analytics.scalar_selection import ScalarProviderSelection
from app.auth.security import hash_password
from app.models import HealthSample, ImportBatch, User
from app.provider_preferences import ACTIVITY_ENERGY_DATA_AREA
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


def _activity_sample(
    db: Session,
    user: User,
    *,
    local_date: date,
    source_type: str,
    value: Decimal,
    start_at: datetime | None = None,
) -> HealthSample:
    batch = ImportBatch(user_id=user.id, source_type=source_type, status="completed")
    db.add(batch)
    db.flush()
    sample_id = uuid4()
    sample = HealthSample(
        user_id=user.id,
        import_batch_id=batch.id,
        external_sample_id=str(sample_id),
        fingerprint=sample_id.hex,
        source_type=source_type,
        source_identifier=f"synthetic-{source_type}",
        metric_type=ACTIVE_ENERGY_METRIC,
        value=value,
        unit="kcal",
        original_value=value,
        original_unit="kcal",
        start_at=start_at
        or datetime(local_date.year, local_date.month, local_date.day, 12, tzinfo=UTC),
        end_at=datetime(local_date.year, local_date.month, local_date.day, 23, tzinfo=UTC),
        local_date=local_date,
        timezone="UTC",
    )
    db.add(sample)
    db.flush()
    return sample


def _activity_selection(provider_key: str, source_type: str) -> ScalarProviderSelection:
    return ScalarProviderSelection(
        data_area=ACTIVITY_ENERGY_DATA_AREA,
        provider_key=provider_key,
        source_type=source_type,
    )


def test_activity_canonical_uses_selected_provider_without_cross_provider_sum(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    day = date(2026, 9, 14)
    _activity_sample(
        db,
        user,
        local_date=day,
        source_type=ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS["google_health"][0],
        value=Decimal("300"),
    )
    _activity_sample(
        db,
        user,
        local_date=day,
        source_type=ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS["yazio"][0],
        value=Decimal("40"),
    )
    _activity_sample(
        db,
        user,
        local_date=day,
        source_type=ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS["apple_health"][0],
        value=Decimal("120"),
    )
    monkeypatch.setattr(
        verification,
        "resolve_scalar_provider",
        lambda _db, **_kwargs: _activity_selection(
            "google_health", ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS["google_health"][0]
        ),
    )

    response = verification.read_activity_verification(
        db, user_id=user.id, start=day, end=day, view="canonical"
    )

    assert response.days[0].providers == []
    assert response.days[0].canonical is not None
    assert response.days[0].canonical.model_dump() == {
        "provider_key": "google_health",
        "status": "available",
        "active_energy_kcal": 300.0,
        "record_count": 1,
        "source_types": ["google_health_activity_v4"],
    }


def test_activity_all_sources_keeps_provider_totals_separate(
    db: Session, user: User
) -> None:
    day = date(2026, 9, 14)
    _activity_sample(
        db,
        user,
        local_date=day,
        source_type=ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS["google_health"][0],
        value=Decimal("300"),
    )
    for value in (Decimal("40"), Decimal("25")):
        _activity_sample(
            db,
            user,
            local_date=day,
            source_type=ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS["yazio"][0],
            value=value,
        )
    for source_type, value in zip(
        ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS["apple_health"],
        (Decimal("120"), Decimal("80")),
        strict=True,
    ):
        _activity_sample(
            db,
            user,
            local_date=day,
            source_type=source_type,
            value=value,
        )

    response = verification.read_activity_verification(
        db, user_id=user.id, start=day, end=day, view="all"
    )

    assert response.days[0].canonical is None
    assert [record.model_dump() for record in response.days[0].providers] == [
        {
            "provider_key": "google_health",
            "status": "available",
            "active_energy_kcal": 300.0,
            "record_count": 1,
            "source_types": ["google_health_activity_v4"],
        },
        {
            "provider_key": "apple_health",
            "status": "unavailable",
            "active_energy_kcal": None,
            "record_count": 2,
            "source_types": ["apple_health_xml", "health_auto_export_v2"],
        },
        {
            "provider_key": "yazio",
            "status": "available",
            "active_energy_kcal": 65.0,
            "record_count": 2,
            "source_types": ["yazio_export_v1"],
        },
    ]


def test_google_daily_rollup_rows_are_not_summed(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    day = date(2026, 9, 14)
    source_type = ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS["google_health"][0]
    _activity_sample(
        db,
        user,
        local_date=day,
        source_type=source_type,
        value=Decimal("200"),
        start_at=datetime(2026, 9, 14, 7, tzinfo=UTC),
    )
    _activity_sample(
        db,
        user,
        local_date=day,
        source_type=source_type,
        value=Decimal("500"),
        start_at=datetime(2026, 9, 14, 8, tzinfo=UTC),
    )
    monkeypatch.setattr(
        verification,
        "resolve_scalar_provider",
        lambda _db, **_kwargs: _activity_selection("google_health", source_type),
    )

    response = verification.read_activity_verification(
        db, user_id=user.id, start=day, end=day, view="canonical"
    )

    assert response.days[0].canonical is not None
    assert response.days[0].canonical.active_energy_kcal == 200.0
    assert response.days[0].canonical.record_count == 2


def test_activity_empty_range_returns_no_data_days(db: Session, user: User) -> None:
    start = date(2026, 9, 14)
    end = date(2026, 9, 15)

    response = verification.read_activity_verification(
        db, user_id=user.id, start=start, end=end, view="all"
    )

    assert [item.date for item in response.days] == [start, end]
    for item in response.days:
        assert item.canonical is None
        assert [record.model_dump() for record in item.providers] == [
            {
                "provider_key": provider_key,
                "status": "no_data",
                "active_energy_kcal": None,
                "record_count": 0,
                "source_types": [],
            }
            for provider_key in ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS
        ]


def test_activity_read_is_user_scoped(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    day = date(2026, 9, 14)
    source_type = ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS["google_health"][0]
    _activity_sample(
        db,
        user,
        local_date=day,
        source_type=source_type,
        value=Decimal("300"),
    )
    other_user = User(
        username="other-user",
        password_hash=hash_password("correct-horse-battery-staple"),
        timezone="UTC",
    )
    db.add(other_user)
    db.flush()
    _activity_sample(
        db,
        other_user,
        local_date=day,
        source_type=source_type,
        value=Decimal("900"),
    )
    monkeypatch.setattr(
        verification,
        "resolve_scalar_provider",
        lambda _db, **_kwargs: _activity_selection("google_health", source_type),
    )

    response = verification.read_activity_verification(
        db, user_id=user.id, start=day, end=day, view="canonical"
    )

    assert response.days[0].canonical is not None
    assert response.days[0].canonical.active_energy_kcal == 300.0
    assert response.days[0].canonical.record_count == 1


def test_activity_verification_route_requires_authentication(client: TestClient) -> None:
    response = client.get("/api/v1/analytics/verification/activity")

    assert response.status_code == 401


def test_activity_canonical_marks_ambiguous_apple_transports_unavailable(
    db: Session, user: User
) -> None:
    day = date(2026, 9, 14)
    for source_type, value in zip(
        ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS["apple_health"],
        (Decimal("120"), Decimal("80")),
        strict=True,
    ):
        _activity_sample(
            db,
            user,
            local_date=day,
            source_type=source_type,
            value=value,
        )

    response = verification.read_activity_verification(
        db, user_id=user.id, start=day, end=day, view="canonical"
    )

    assert response.days[0].providers == []
    assert response.days[0].canonical is not None
    assert response.days[0].canonical.model_dump() == {
        "provider_key": "apple_health",
        "status": "unavailable",
        "active_energy_kcal": None,
        "record_count": 2,
        "source_types": ["apple_health_xml", "health_auto_export_v2"],
    }
