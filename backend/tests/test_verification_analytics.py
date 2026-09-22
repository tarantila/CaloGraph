from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.activity import ACTIVE_ENERGY_METRIC, ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS
from app.analytics import verification
from app.analytics.provider_selection import NutritionProviderSelection
from app.analytics.scalar_selection import ScalarProviderSelection
from app.auth.security import hash_password
from app.models import GoogleHealthConnection, HealthSample, ImportBatch, User, YazioConnection
from app.nutrition.enums import (
    ConsumptionEventKind,
    CoverageState,
    LineageState,
    ObservationKind,
    ObservationRole,
    PresenceState,
    ResolutionState,
)
from app.nutrition.models import (
    NutritionConsumptionEvent,
    NutritionFieldObservation,
    NutritionFoodProfile,
    NutritionFoodSnapshot,
    NutritionIngestionRun,
    NutritionSourceObservation,
)
from app.provider_preferences import ACTIVITY_ENERGY_DATA_AREA
from app.schemas import VerificationActivityResponse, VerificationNutritionResponse
from app.services.apple_health_nutrition_ingestion import apple_health_source_instance_id


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


def test_activity_all_sources_resolves_apple_transport_once_per_request(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    start = date(2026, 9, 14)
    end = date(2026, 9, 15)
    source_type = ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS["apple_health"][0]
    for day in (start, end):
        _activity_sample(
            db,
            user,
            local_date=day,
            source_type=source_type,
            value=Decimal("120"),
        )
    original_resolver = verification.resolve_provider_source_type
    resolution_calls = 0

    def count_resolution_calls(*args, **kwargs) -> str:
        nonlocal resolution_calls
        resolution_calls += 1
        return original_resolver(*args, **kwargs)

    monkeypatch.setattr(verification, "resolve_provider_source_type", count_resolution_calls)

    response = verification.read_activity_verification(
        db, user_id=user.id, start=start, end=end, view="all"
    )

    assert resolution_calls == 1
    assert [item.providers[1].active_energy_kcal for item in response.days] == [120.0, 120.0]




def _nutrition_provider_source(db: Session, user: User, provider_key: str):
    if provider_key == "google_health":
        connection = GoogleHealthConnection(user_id=user.id)
    elif provider_key == "yazio":
        connection = YazioConnection(
            user_id=user.id,
            encrypted_email=b"encrypted-email",
            encrypted_password=b"encrypted-password",
            source_identifier=f"verification-{uuid4().hex}",
        )
    elif provider_key == "apple_health":
        return apple_health_source_instance_id(user.id)
    else:
        raise ValueError(f"unsupported provider fixture: {provider_key}")
    db.add(connection)
    db.flush()
    return connection.id


def _nutrition_event(
    db: Session,
    user: User,
    *,
    provider_key: str,
    source_instance_id,
    local_date: date,
    logical_event_key: str | None,
    metrics: dict[str, Decimal],
    food_name: str | None = None,
    daytime: str | None = "lunch",
    occurred_at: datetime | None = None,
    revision: int = 1,
    supersedes: NutritionConsumptionEvent | None = None,
) -> NutritionConsumptionEvent:
    source_key = f"{provider_key}:{logical_event_key}:{revision}:{uuid4()}"
    run = NutritionIngestionRun(
        user_id=user.id,
        provider_key=provider_key,
        source_instance_id=source_instance_id,
        connector_variant="verification-fixture",
        requested_start_date=local_date,
        requested_end_date=local_date,
        covered_start_date=local_date,
        covered_end_date=local_date,
        status="completed",
        coverage_state=CoverageState.COMPLETE.value,
    )
    db.add(run)
    db.flush()
    source = NutritionSourceObservation(
        user_id=user.id,
        ingestion_run_id=run.id,
        provider_key=provider_key,
        source_instance_id=source_instance_id,
        connector_variant="verification-fixture",
        observation_kind=ObservationKind.CONSUMPTION_EVENT.value,
        source_namespace="verification-fixture",
        source_record_id=source_key,
        source_revision=revision,
        observation_fingerprint=sha256(source_key.encode()).hexdigest(),
        local_date=local_date,
        timezone_source="fixture",
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
        lineage_state=LineageState.CONFIRMED.value,
    )
    db.add(source)
    db.flush()

    snapshot_id = None
    if food_name is not None:
        profile = NutritionFoodProfile(
            user_id=user.id,
            provider_key=provider_key,
            source_instance_id=source_instance_id,
        )
        db.add(profile)
        db.flush()
        snapshot = NutritionFoodSnapshot(
            user_id=user.id,
            food_profile_id=profile.id,
            source_observation_id=source.id,
            content_hash=sha256(f"{source_key}:{food_name}".encode()).hexdigest(),
            name=food_name,
        )
        db.add(snapshot)
        db.flush()
        snapshot_id = snapshot.id

    event = NutritionConsumptionEvent(
        user_id=user.id,
        source_observation_id=source.id,
        provider_key=provider_key,
        source_instance_id=source_instance_id,
        event_kind=ConsumptionEventKind.SIMPLE_PRODUCT.value,
        logical_event_key=logical_event_key,
        supersedes_event_id=supersedes.id if supersedes is not None else None,
        supersedes_revision=supersedes.revision if supersedes is not None else None,
        revision=revision,
        food_snapshot_id=snapshot_id,
        canonical_start_at=occurred_at
        or datetime(local_date.year, local_date.month, local_date.day, 12, tzinfo=UTC),
        local_date=local_date,
        daytime=daytime,
        amount=Decimal("1.5"),
        amount_unit="portion",
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
        lineage_state=LineageState.CONFIRMED.value,
    )
    db.add(event)
    db.flush()
    for metric_key, value in metrics.items():
        db.add(
            NutritionFieldObservation(
                user_id=user.id,
                source_observation_id=source.id,
                provider_field_path=f"fixture.{metric_key}",
                provider_raw_value_decimal=value,
                provider_raw_unit="kcal" if metric_key == "dietary_energy_kcal" else "g",
                metric_key=metric_key,
                canonical_value=value,
                canonical_unit="kcal" if metric_key == "dietary_energy_kcal" else "g",
                observation_role=ObservationRole.CANONICAL.value,
                presence_state=PresenceState.SUPPLIED.value,
                coverage_state=CoverageState.COMPLETE.value,
                resolution_state=ResolutionState.RESOLVED.value,
                lineage_state=LineageState.CONFIRMED.value,
            )
        )
    db.flush()
    return event


def _nutrition_daily_point(
    *,
    calories_kcal: Decimal | None = None,
    protein_g: Decimal | None = None,
    carbohydrates_g: Decimal | None = None,
    fat_g: Decimal | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        calories_kcal=calories_kcal,
        protein_g=protein_g,
        carbs_g=carbohydrates_g,
        fat_g=fat_g,
    )


def _nutrition_selection(provider_key: str, source_instance_id) -> NutritionProviderSelection:
    return NutritionProviderSelection(provider_key, source_instance_id)


def test_nutrition_canonical_returns_selected_provider_summary_and_events(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    day = date(2026, 9, 14)
    google_source = _nutrition_provider_source(db, user, "google_health")
    yazio_source = _nutrition_provider_source(db, user, "yazio")
    _nutrition_event(
        db,
        user,
        provider_key="google_health",
        source_instance_id=google_source,
        local_date=day,
        logical_event_key="google-breakfast",
        food_name="Google breakfast",
        daytime="breakfast",
        metrics={
            "dietary_energy_kcal": Decimal("111"),
            "protein_g": Decimal("5"),
        },
    )
    _nutrition_event(
        db,
        user,
        provider_key="yazio",
        source_instance_id=yazio_source,
        local_date=day,
        logical_event_key="yazio-lunch",
        food_name="Yazio lunch",
        metrics={"dietary_energy_kcal": Decimal("999")},
    )
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        verification,
        "resolve_nutrition_provider",
        lambda _db, **_kwargs: _nutrition_selection("google_health", google_source),
    )
    monkeypatch.setattr(
        verification,
        "read_provider_daily_points",
        lambda _db, *, provider_key, source_instance_id, **_kwargs: (
            calls.append((provider_key, source_instance_id))
            or [
                _nutrition_daily_point(
                    calories_kcal=Decimal("640.25"),
                    protein_g=Decimal("31.5"),
                    carbohydrates_g=Decimal("72"),
                    fat_g=Decimal("19"),
                )
            ]
        ),
    )

    response = verification.read_nutrition_verification(
        db, user_id=user.id, local_date=day, view="canonical"
    )

    assert calls == [("google_health", google_source)]
    assert response.providers == []
    assert response.canonical is not None
    assert response.canonical.model_dump() == {
        "provider_key": "google_health",
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
                "provider_key": "google_health",
                "occurred_at": datetime(2026, 9, 14, 12),
                "meal_type": "breakfast",
                "food_name": "Google breakfast",
                "calories_kcal": 111.0,
                "protein_g": 5.0,
                "carbohydrates_g": None,
                "fat_g": None,
                "serving_amount": 1.5,
                "serving_unit": "portion",
            }
        ],
    }


def test_nutrition_all_sources_groups_events_without_cross_provider_addition(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    day = date(2026, 9, 14)
    google_source = _nutrition_provider_source(db, user, "google_health")
    yazio_source = _nutrition_provider_source(db, user, "yazio")
    _nutrition_event(
        db,
        user,
        provider_key="google_health",
        source_instance_id=google_source,
        local_date=day,
        logical_event_key="google-event",
        food_name="Google meal",
        metrics={"dietary_energy_kcal": Decimal("11")},
    )
    _nutrition_event(
        db,
        user,
        provider_key="yazio",
        source_instance_id=yazio_source,
        local_date=day,
        logical_event_key="yazio-event",
        food_name="Yazio meal",
        metrics={"dietary_energy_kcal": Decimal("22")},
    )
    summaries = {
        "google_health": _nutrition_daily_point(calories_kcal=Decimal("300")),
        "yazio": _nutrition_daily_point(calories_kcal=Decimal("900")),
    }
    monkeypatch.setattr(
        verification,
        "read_provider_daily_points",
        lambda _db, *, provider_key, **_kwargs: [summaries[provider_key]]
        if provider_key in summaries
        else [],
    )

    response = verification.read_nutrition_verification(
        db, user_id=user.id, local_date=day, view="all"
    )

    assert response.canonical is None
    assert [group.provider_key for group in response.providers] == [
        "google_health",
        "yazio",
        "apple_health",
    ]
    assert [
        (group.summary.calories_kcal, group.record_count, [event.food_name for event in group.events])
        for group in response.providers
    ] == [
        (300.0, 1, ["Google meal"]),
        (900.0, 1, ["Yazio meal"]),
        (None, 0, []),
    ]


def test_nutrition_uses_food_snapshot_name_and_safe_fallback(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    day = date(2026, 9, 14)
    source_instance_id = _nutrition_provider_source(db, user, "google_health")
    _nutrition_event(
        db,
        user,
        provider_key="google_health",
        source_instance_id=source_instance_id,
        local_date=day,
        logical_event_key="named",
        food_name="Snapshot name",
        occurred_at=datetime(2026, 9, 14, 11, tzinfo=UTC),
        metrics={},
    )
    _nutrition_event(
        db,
        user,
        provider_key="google_health",
        source_instance_id=source_instance_id,
        local_date=day,
        logical_event_key="unnamed",
        metrics={},
    )
    monkeypatch.setattr(
        verification,
        "resolve_nutrition_provider",
        lambda _db, **_kwargs: _nutrition_selection("google_health", source_instance_id),
    )
    monkeypatch.setattr(verification, "read_provider_daily_points", lambda *_args, **_kwargs: [])

    response = verification.read_nutrition_verification(
        db, user_id=user.id, local_date=day, view="canonical"
    )

    assert response.canonical is not None
    assert [event.food_name for event in response.canonical.events] == [
        "Snapshot name",
        "Unbenannter Eintrag",
    ]


def test_nutrition_missing_meal_type_is_dash_compatible_none(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    day = date(2026, 9, 14)
    source_instance_id = _nutrition_provider_source(db, user, "google_health")
    _nutrition_event(
        db,
        user,
        provider_key="google_health",
        source_instance_id=source_instance_id,
        local_date=day,
        logical_event_key="untimed-meal",
        food_name="No meal type",
        daytime=None,
        metrics={},
    )
    monkeypatch.setattr(
        verification,
        "resolve_nutrition_provider",
        lambda _db, **_kwargs: _nutrition_selection("google_health", source_instance_id),
    )
    monkeypatch.setattr(verification, "read_provider_daily_points", lambda *_args, **_kwargs: [])

    response = verification.read_nutrition_verification(
        db, user_id=user.id, local_date=day, view="canonical"
    )

    assert response.canonical is not None
    assert response.canonical.events[0].meal_type is None


def test_nutrition_ignores_superseded_event_revision(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    day = date(2026, 9, 14)
    source_instance_id = _nutrition_provider_source(db, user, "google_health")
    superseded = _nutrition_event(
        db,
        user,
        provider_key="google_health",
        source_instance_id=source_instance_id,
        local_date=day,
        logical_event_key="revisioned-event",
        food_name="Old meal",
        metrics={"dietary_energy_kcal": Decimal("100")},
    )
    _nutrition_event(
        db,
        user,
        provider_key="google_health",
        source_instance_id=source_instance_id,
        local_date=day,
        logical_event_key="revisioned-event",
        revision=2,
        supersedes=superseded,
        food_name="Current meal",
        metrics={"dietary_energy_kcal": Decimal("200")},
    )
    monkeypatch.setattr(
        verification,
        "resolve_nutrition_provider",
        lambda _db, **_kwargs: _nutrition_selection("google_health", source_instance_id),
    )
    monkeypatch.setattr(verification, "read_provider_daily_points", lambda *_args, **_kwargs: [])

    response = verification.read_nutrition_verification(
        db, user_id=user.id, local_date=day, view="canonical"
    )

    assert response.canonical is not None
    assert response.canonical.record_count == 1
    assert [(event.food_name, event.calories_kcal) for event in response.canonical.events] == [
        ("Current meal", 200.0)
    ]


def test_nutrition_empty_day_is_explicit_no_data(db: Session, user: User) -> None:
    day = date(2026, 9, 14)

    response = verification.read_nutrition_verification(
        db, user_id=user.id, local_date=day, view="all"
    )

    assert response.canonical is None
    assert [group.model_dump() for group in response.providers] == [
        {
            "provider_key": provider_key,
            "status": "no_data",
            "record_count": 0,
            "summary": {
                "calories_kcal": None,
                "protein_g": None,
                "carbohydrates_g": None,
                "fat_g": None,
            },
            "events": [],
        }
        for provider_key in ("google_health", "yazio", "apple_health")
    ]


def test_nutrition_read_is_user_scoped(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    day = date(2026, 9, 14)
    source_instance_id = uuid4()
    _nutrition_event(
        db,
        user,
        provider_key="google_health",
        source_instance_id=source_instance_id,
        local_date=day,
        logical_event_key="owned-event",
        food_name="Owned meal",
        metrics={},
    )
    other_user = User(
        username="nutrition-other-user",
        password_hash=hash_password("correct-horse-battery-staple"),
        timezone="UTC",
    )
    db.add(other_user)
    db.flush()
    _nutrition_event(
        db,
        other_user,
        provider_key="google_health",
        source_instance_id=source_instance_id,
        local_date=day,
        logical_event_key="other-event",
        food_name="Other user's meal",
        metrics={},
    )
    monkeypatch.setattr(
        verification,
        "resolve_nutrition_provider",
        lambda _db, **_kwargs: _nutrition_selection("google_health", source_instance_id),
    )
    monkeypatch.setattr(
        verification,
        "read_provider_daily_points",
        lambda *_args, **_kwargs: [_nutrition_daily_point(calories_kcal=Decimal("321"))],
    )

    response = verification.read_nutrition_verification(
        db, user_id=user.id, local_date=day, view="canonical"
    )

    assert response.canonical is not None
    assert [event.food_name for event in response.canonical.events] == ["Owned meal"]
    assert response.canonical.summary.calories_kcal == 321.0


def test_nutrition_verification_route_requires_authentication(client: TestClient) -> None:
    response = client.get("/api/v1/analytics/verification/nutrition")

    assert response.status_code == 401
