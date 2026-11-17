from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from test_google_health_nutrition_ingestion import _connection as _google_connection
from test_google_health_nutrition_ingestion import _point
from test_yazio_nutrition_ingestion import _connection as _yazio_connection

from app.analytics.micronutrient_shadow import read_canonical_micronutrient_period
from app.analytics.nutrition_analysis import AnalysisNutritionReadError, read_analysis_nutrition
from app.analytics.nutrition_micronutrients import (
    AnalysisMicronutrientReadError,
    read_analysis_micronutrients,
)
from app.analytics.provider_daily import ProviderDailyReadError, read_provider_daily_points
from app.analytics.provider_selection import NutritionProviderNotReady, NutritionProviderSelection
from app.google_health.client import NutritionNutrient, NutritionQuantity
from app.nutrition.enums import ResolutionState
from app.nutrition.models import (
    NutritionConsumptionEvent,
    NutritionFieldObservation,
    NutritionIngestionRun,
    NutritionProvenance,
    NutritionSourceObservation,
)
from app.nutrition.resolution import period_reader
from app.services.google_health_nutrition_ingestion import ingest_google_health_nutrition_logs
from app.services.yazio_nutrition_ingestion import ingest_yazio_food_diary
from app.services.yazio_provider import (
    YazioDailyNutrientSummary,
    YazioFoodDiary,
    YazioNutrientValues,
)

DAY = date(2026, 9, 1)
GOOGLE = "google_health"
YAZIO = "yazio"


@pytest.fixture
def provider_sources(db, user):
    google = _google_connection(db, user)
    yazio = _yazio_connection(db, user)
    return google, yazio


def _google_nutrients(*, protein: bool = True, fiber: Decimal | None = None, vitamin_a: bool = True):
    nutrients = []
    if protein:
        nutrients.append(NutritionNutrient("PROTEIN", NutritionQuantity(30, "g")))
    if fiber is not None:
        nutrients.append(NutritionNutrient("DIETARY_FIBER", NutritionQuantity(fiber, "g")))
    if vitamin_a:
        nutrients.append(NutritionNutrient("VITAMIN_A", NutritionQuantity(Decimal("100"), "ug")))
    return tuple(nutrients)


def _ingest_google(db, user, connection, *, protein=True, fiber=None, vitamin_a=True):
    point = _point(
        name=f"google-{DAY.isoformat()}",
        food=None,
        nutrients=_google_nutrients(protein=protein, fiber=fiber, vitamin_a=vitamin_a),
        energy=NutritionQuantity(500, "kcal"),
        total_carbohydrate=NutritionQuantity(40, "g"),
        total_fat=NutritionQuantity(12, "g"),
        food_display_name="Google meal",
        meal_type="lunch",
    )
    ingest_google_health_nutrition_logs(
        db,
        user_id=user.id,
        source_instance_id=connection.id,
        requested_start=DAY,
        requested_end=DAY,
        data_points=(point,),
    )


def _ingest_yazio(db, user, connection, *, core_valid=True):
    nutrients = YazioNutrientValues(
        energy=Decimal("800") if core_valid else None,
        protein=Decimal("20") if core_valid else None,
        carb=Decimal("90") if core_valid else None,
        fat=Decimal("30") if core_valid else None,
        fiber=Decimal("5"),
        sugar=Decimal("10"),
        saturated_fat=Decimal("3"),
        additional={"vitamin.a": Decimal("0.00054")},
    )
    diary = YazioFoodDiary(
        requested_start_day=DAY,
        requested_end_day=DAY,
        consumed_products=(),
        consumed_simple_products=(),
        product_profiles=(),
        daily_summaries=(YazioDailyNutrientSummary(DAY, nutrients, energy_goal=None),),
    )
    ingest_yazio_food_diary(
        db,
        user_id=user.id,
        source_instance_id=connection.id,
        requested_start=DAY,
        requested_end=DAY,
        diary=diary,
    )


def _selection(google, yazio):
    return NutritionProviderSelection(
        provider_key=GOOGLE,
        source_instance_id=google.id,
        provider_sources=((GOOGLE, google.id), (YAZIO, yazio.id)),
    )


def _assert_shared_daily_readers(monkeypatch, db, user, selection, expected):
    from app.analytics import verification
    from app.api import analytics

    monkeypatch.setattr(analytics, "_preferred_nutrition_provider", lambda *args, **kwargs: selection)
    monkeypatch.setattr(verification, "resolve_nutrition_provider", lambda *args, **kwargs: selection)
    point = read_provider_daily_points(
        db,
        user_id=user.id,
        provider_key=selection.provider_key,
        source_instance_id=selection.source_instance_id,
        provider_sources=selection.provider_sources,
        start=DAY,
        end=DAY,
    )[0]
    assert (point.calories_kcal, point.protein_g, point.carbs_g, point.fat_g) == expected

    daily = analytics.daily(start=DAY, end=DAY, weekday=None, period=None, user=user, db=db)[0]
    calendar = analytics.calendar(start=DAY, end=DAY, user=user, db=db)["days"][0]
    weekly = analytics.weekly(start=DAY, end=DAY, user=user, db=db)["weeks"][0]["days"][0]
    weekdays = analytics.weekdays(start=DAY, end=DAY, user=user, db=db)["weekdays"]
    trends = analytics.trends(
        start=DAY, end=DAY, include_incomplete=False, period=None, user=user, db=db,
    )["points"][0]

    def point_values(reader_point):
        fields = ("calories_kcal", "protein_g", "carbs_g", "fat_g")
        values = (
            (reader_point[field] for field in fields)
            if isinstance(reader_point, dict)
            else (getattr(reader_point, field) for field in fields)
        )
        return tuple(Decimal(str(value)) if value is not None else None for value in values)

    for reader_point in (daily, calendar, weekly, trends):
        assert point_values(reader_point) == expected
    day_of_week = DAY.weekday()
    mean = weekdays[day_of_week]["mean_kcal"]
    assert (Decimal(str(mean)) if mean is not None else None) == expected[0]

    canonical = verification.read_nutrition_verification(
        db, user_id=user.id, local_date=DAY, view="canonical",
    ).canonical
    if expected[0] is None:
        assert canonical is None
    else:
        assert canonical is not None
        assert canonical.provider_key == (GOOGLE if expected[0] == Decimal("500") else YAZIO)
        assert tuple(
            Decimal(str(getattr(canonical.summary, key)))
            for key in ("calories_kcal", "protein_g", "carbohydrates_g", "fat_g")
        ) == expected
        assert all(event.provider_key == canonical.provider_key for event in canonical.events)


def _evidence_state(db):
    return [
        tuple(db.execute(select(model.__table__).order_by(model.id)))
        for model in (
            NutritionIngestionRun, NutritionSourceObservation, NutritionConsumptionEvent,
            NutritionFieldObservation, NutritionProvenance,
        )
    ]


@pytest.mark.parametrize(
    ("google_core_valid", "google_fiber", "google_vitamin_a", "expected_provider", "expected", "expected_fiber"),
    [
        (True, None, True, GOOGLE, (Decimal("500"), Decimal("30"), Decimal("40"), Decimal("12")), None),
        (True, Decimal("0"), True, GOOGLE, (Decimal("500"), Decimal("30"), Decimal("40"), Decimal("12")), Decimal("0")),
        (False, Decimal("6"), True, YAZIO, (Decimal("800"), Decimal("20"), Decimal("90"), Decimal("30")), Decimal("5")),
        (True, None, False, GOOGLE, (Decimal("500"), Decimal("30"), Decimal("40"), Decimal("12")), None),
    ],
    ids=("missing-fiber", "explicit-zero-fiber", "invalid-core", "missing-micronutrient"),
)
def test_all_nutrition_readers_select_one_provider_for_the_whole_day(
    db, user, provider_sources, monkeypatch,
    google_core_valid, google_fiber, google_vitamin_a, expected_provider, expected, expected_fiber,
):
    google, yazio = provider_sources
    _ingest_google(db, user, google, protein=google_core_valid, fiber=google_fiber, vitamin_a=google_vitamin_a)
    _ingest_yazio(db, user, yazio)
    selection = _selection(google, yazio)
    before = _evidence_state(db)

    nutrition = read_analysis_nutrition(db, user_id=user.id, selection=selection, start=DAY, end=DAY)
    nutrition_day = nutrition.days[0]
    assert nutrition_day.provider_key == expected_provider
    assert (
        nutrition_day.dietary_energy_kcal,
        nutrition_day.protein_g,
        nutrition_day.carbohydrates_g,
        nutrition_day.fat_g,
    ) == expected
    assert nutrition_day.fiber_g == expected_fiber
    assert bool(nutrition_day.meals) is (expected_provider == GOOGLE)

    micronutrients = read_analysis_micronutrients(
        db, user_id=user.id, selection=selection, start=DAY, end=DAY, metric_key="vitamin_a_ug",
    )
    vitamin_a = micronutrients.metrics[0]
    expected_vitamin_a = (
        Decimal("100") if expected_provider == GOOGLE and google_vitamin_a
        else Decimal("540") if expected_provider == YAZIO
        else None
    )
    assert micronutrients.series[0].provider_key == expected_provider
    assert micronutrients.series[0].value == expected_vitamin_a
    assert vitamin_a.total == expected_vitamin_a

    canonical_micro = read_canonical_micronutrient_period(
        db,
        user_id=user.id,
        provider_key=GOOGLE,
        source_instance_id=google.id,
        provider_sources=selection.provider_sources,
        start=DAY,
        end=DAY,
    )
    vitamin_a_total = next(item.total for item in canonical_micro.nutrients if item.metric_type == "vitamin_a_ug")
    assert vitamin_a_total == expected_vitamin_a

    _assert_shared_daily_readers(monkeypatch, db, user, selection, expected)
    assert _evidence_state(db) == before


@pytest.mark.parametrize("resolution", [ResolutionState.CONFLICT, ResolutionState.DUPLICATE_CANDIDATE])
def test_unsafe_secondary_evidence_fails_closed_instead_of_selecting_another_provider(
    db, user, provider_sources, monkeypatch, resolution,
):
    from app.analytics import verification

    google, yazio = provider_sources
    _ingest_google(db, user, google, fiber=Decimal("1"))
    _ingest_yazio(db, user, yazio)
    selection = _selection(google, yazio)
    original_resolve = period_reader._resolve_provider_period

    def unsafe_secondary(*args, **kwargs):
        matrix = original_resolve(*args, **kwargs)
        if kwargs["provider_key"] != GOOGLE:
            return matrix
        return {
            current: {
                **candidates,
                "fiber_g": replace(candidates["fiber_g"], resolution_state=resolution),
            }
            for current, candidates in matrix.items()
        }

    monkeypatch.setattr(period_reader, "_resolve_provider_period", unsafe_secondary)

    with pytest.raises(AnalysisNutritionReadError):
        read_analysis_nutrition(db, user_id=user.id, selection=selection, start=DAY, end=DAY)
    with pytest.raises(AnalysisMicronutrientReadError):
        read_analysis_micronutrients(
            db, user_id=user.id, selection=selection, start=DAY, end=DAY, metric_key="vitamin_a_ug",
        )
    with pytest.raises(ProviderDailyReadError):
        read_provider_daily_points(
            db, user_id=user.id, provider_sources=selection.provider_sources, start=DAY, end=DAY,
        )
    with pytest.raises(ValueError):
        read_canonical_micronutrient_period(
            db, user_id=user.id, provider_key=GOOGLE, source_instance_id=google.id,
            provider_sources=selection.provider_sources, start=DAY, end=DAY,
        )
    monkeypatch.setattr(verification, "resolve_nutrition_provider", lambda *args, **kwargs: selection)
    with pytest.raises(NutritionProviderNotReady):
        verification.read_nutrition_verification(db, user_id=user.id, local_date=DAY, view="canonical")


def test_no_reader_selects_an_invalid_day_or_uses_secondary_only_evidence(
    db, user, provider_sources, monkeypatch,
):
    google, yazio = provider_sources
    _ingest_google(db, user, google, protein=False, fiber=Decimal("0"))
    _ingest_yazio(db, user, yazio, core_valid=False)
    selection = _selection(google, yazio)
    before = _evidence_state(db)

    day = read_analysis_nutrition(
        db, user_id=user.id, selection=selection, start=DAY, end=DAY,
    ).days[0]
    assert day.provider_key is None
    assert day.dietary_energy_kcal is None
    assert day.fiber_g is None
    assert day.meals == []
    micronutrients = read_analysis_micronutrients(
        db, user_id=user.id, selection=selection, start=DAY, end=DAY, metric_key="vitamin_a_ug",
    )
    assert micronutrients.series[0].provider_key is None
    assert micronutrients.series[0].value is None
    assert micronutrients.metrics[0].total is None
    assert micronutrients.metrics[0].contributors == []
    canonical = read_canonical_micronutrient_period(
        db, user_id=user.id, provider_key=GOOGLE, source_instance_id=google.id,
        provider_sources=selection.provider_sources, start=DAY, end=DAY,
    )
    assert canonical.recorded_days == 0
    assert all(nutrient.total is None for nutrient in canonical.nutrients)
    _assert_shared_daily_readers(monkeypatch, db, user, selection, (None, None, None, None))
    assert _evidence_state(db) == before
