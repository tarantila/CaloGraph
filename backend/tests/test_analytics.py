from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.analytics.service import daily_points, percentile
from app.api.analytics import micronutrients
from app.importers.common import CanonicalSample
from app.importers.json_adapter import AdapterResult
from app.models import NutritionTarget, User
from app.services.import_service import persist_import


def metric(day: int, name: str, value: str) -> CanonicalSample:
    at = datetime(2024, 1, day, 8 + day, tzinfo=UTC)
    unit = "kcal" if "energy" in name else "g"
    return CanonicalSample(
        name,
        Decimal(value),
        unit,
        Decimal(value),
        unit,
        at,
        at,
        "Europe/Berlin",
        "test",
        "Test",
        "test",
        f"{day}-{name}",
    )


def test_daily_totals_missing_days_and_target_change(db: Session, user: User) -> None:
    old = user.targets[0]
    old.valid_to = date(2024, 1, 3)
    db.add(
        NutritionTarget(
            user_id=user.id,
            valid_from=date(2024, 1, 3),
            calories_kcal=Decimal("1800"),
            protein_g=Decimal("130"),
        )
    )
    samples = [
        metric(2, "dietary_energy_kcal", "1900"),
        metric(2, "protein_g", "120"),
        metric(2, "carbohydrates_g", "200"),
        metric(2, "fat_g", "60"),
    ]
    persist_import(db, user, AdapterResult("test", samples, received=4), None, "x-test", "test")
    points = daily_points(db, user, date(2024, 1, 1), date(2024, 1, 3))
    assert points[0].tracking_status == "no_data"
    assert points[1].calories_kcal == Decimal("1900")
    assert points[1].target_kcal == Decimal("2000")
    assert points[2].target_kcal == Decimal("1800")


def test_all_zero_primary_nutrition_is_treated_as_missing(
    db: Session, user: User
) -> None:
    samples = [
        metric(2, "dietary_energy_kcal", "0"),
        metric(2, "protein_g", "0"),
        metric(2, "carbohydrates_g", "0"),
        metric(2, "fat_g", "0"),
    ]
    persist_import(db, user, AdapterResult("test", samples, received=4), None, "x-test", "test")

    point = daily_points(db, user, date(2024, 1, 2), date(2024, 1, 2))[0]

    assert point.calories_kcal is None
    assert point.protein_g is None
    assert point.carbs_g is None
    assert point.fat_g is None
    assert point.tracking_status == "no_data"


def test_low_calorie_day_is_accepted_as_recorded_data(
    db: Session, user: User
) -> None:
    samples = [metric(2, "dietary_energy_kcal", "250")]
    persist_import(db, user, AdapterResult("test", samples, received=1), None, "x-test", "test")

    point = daily_points(db, user, date(2024, 1, 2), date(2024, 1, 2))[0]

    assert point.calories_kcal == Decimal("250")
    assert point.tracking_status == "complete"
    assert point.tracking_score == 1
    assert point.tracking_reasons == ["Kalorienwert vorhanden"]


def test_percentiles() -> None:
    values = [Decimal(value) for value in (1, 2, 3, 4, 5)]
    assert percentile(values, Decimal("0.25")) == Decimal("2")
    assert percentile(values, Decimal("0.75")) == Decimal("4")


def test_micronutrient_analysis_uses_nutrition_days_and_reports_coverage(
    db: Session, user: User
) -> None:
    samples = [
        metric(1, "dietary_energy_kcal", "1800"),
        metric(2, "dietary_energy_kcal", "1900"),
        metric(1, "iron_mg", "7"),
        metric(2, "iron_mg", "14"),
        metric(1, "vitamin_d_ug", "10"),
    ]
    persist_import(
        db,
        user,
        AdapterResult("test", samples, received=len(samples)),
        None,
        "x-test",
        "test",
    )

    result = micronutrients(
        start=date(2024, 1, 1),
        end=date(2024, 1, 2),
        source="test",
        user=user,
        db=db,
    )
    by_metric = {item["metric_type"]: item for item in result["nutrients"]}

    assert result["recorded_days"] == 2
    assert by_metric["iron_mg"]["average_daily"] == 10.5
    assert by_metric["iron_mg"]["percent_of_nrv"] == 75.0
    assert by_metric["iron_mg"]["status"] == "below_orientation"
    assert by_metric["vitamin_d_ug"]["coverage_ratio"] == 0.5
    assert by_metric["vitamin_d_ug"]["status"] == "insufficient_data"
