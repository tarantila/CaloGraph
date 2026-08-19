from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.analytics.service import budget_balance, daily_points, percentile
from app.api.analytics import calendar, daily, micronutrients, trends
from app.importers.common import CanonicalSample
from app.importers.json_adapter import AdapterResult
from app.models import NutritionTarget, User
from app.services.import_service import persist_import


def metric(
    day: int,
    name: str,
    value: str,
    source_type: str = "test",
) -> CanonicalSample:
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
        source_type,
        "Test",
        "test",
        f"{day}-{source_type}-{name}",
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


def test_all_zero_primary_nutrition_is_treated_as_missing(db: Session, user: User) -> None:
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


def test_low_calorie_day_is_accepted_as_recorded_data(db: Session, user: User) -> None:
    samples = [metric(2, "dietary_energy_kcal", "250")]
    persist_import(db, user, AdapterResult("test", samples, received=1), None, "x-test", "test")

    point = daily_points(db, user, date(2024, 1, 2), date(2024, 1, 2))[0]

    assert point.calories_kcal == Decimal("250")
    assert point.tracking_status == "complete"
    assert point.tracking_score == 1
    assert point.tracking_reasons == ["Kalorienwert vorhanden"]


def test_targetless_nutrition_has_no_budget_or_classification(db: Session, user: User) -> None:
    db.delete(user.targets[0])
    db.commit()
    persist_import(
        db,
        user,
        AdapterResult(
            "test",
            [metric(2, "dietary_energy_kcal", "1900")],
            received=1,
        ),
        None,
        "x-test",
        "test",
    )

    point = daily_points(db, user, date(2024, 1, 2), date(2024, 1, 2))[0]
    result = calendar(
        start=date(2024, 1, 2),
        end=date(2024, 1, 2),
        user=user,
        db=db,
    )

    assert point.calories_kcal == Decimal("1900")
    assert point.target_kcal is None
    assert point.deviation_kcal is None
    assert result["days"][0]["classification"] == "no_target"

    assert budget_balance([point]) == {
        "tracked_days": 1,
        "within_budget_days": 0,
        "over_budget_days": 0,
        "over_maintenance_days": 0,
        "unclassified_budget_days": 1,
    }

def test_calendar_uses_budget_and_maintenance_thresholds(db: Session, user: User) -> None:
    target = user.targets[0]
    target.maintenance_kcal = Decimal("2200")
    samples = [
        metric(1, "dietary_energy_kcal", "2000"),
        metric(2, "dietary_energy_kcal", "2200"),
        metric(3, "dietary_energy_kcal", "2201"),
    ]
    persist_import(
        db,
        user,
        AdapterResult("test", samples, received=len(samples)),
        None,
        "x-test",
        "test",
    )

    result = calendar(
        start=date(2024, 1, 1),
        end=date(2024, 1, 3),
        user=user,
        db=db,
    )

    assert [day["classification"] for day in result["days"]] == [
        "under_budget",
        "over_budget",
        "above_maintenance",
    ]
    assert Decimal(result["days"][0]["maintenance_kcal"]) == Decimal("2200")

def test_budget_balance_uses_historical_effective_budgets(
    db: Session, user: User
) -> None:
    old_target = user.targets[0]
    old_target.maintenance_kcal = Decimal("2200")
    old_target.valid_to = date(2024, 1, 3)
    db.add_all(
        [
            NutritionTarget(
                user_id=user.id,
                valid_from=date(2024, 1, 3),
                valid_to=date(2024, 1, 5),
                calories_kcal=Decimal("1800"),
                maintenance_kcal=Decimal("2100"),
                activity_mode="full",
                activity_source_type="apple_health_xml",
                protein_g=Decimal("120"),
            ),
            NutritionTarget(
                user_id=user.id,
                valid_from=date(2024, 1, 5),
                calories_kcal=Decimal("1800"),
                maintenance_kcal=None,
                activity_mode="off",
                activity_source_type=None,
                protein_g=Decimal("120"),
            ),
        ]
    )
    db.commit()
    samples = [
        metric(1, "dietary_energy_kcal", "2000"),
        metric(2, "dietary_energy_kcal", "2001"),
        metric(3, "dietary_energy_kcal", "2100"),
        metric(3, "active_energy_kcal", "300", "apple_health_xml"),
        metric(4, "dietary_energy_kcal", "2401"),
        metric(4, "active_energy_kcal", "300", "apple_health_xml"),
        metric(5, "dietary_energy_kcal", "1900"),
    ]
    persist_import(
        db,
        user,
        AdapterResult("test", samples, received=len(samples)),
        None,
        "x-test",
        "test",
    )

    points = daily_points(db, user, date(2024, 1, 1), date(2024, 1, 5))
    calendar_result = calendar(
        start=date(2024, 1, 1),
        end=date(2024, 1, 5),
        user=user,
        db=db,
    )
    expected = {
        "tracked_days": 5,
        "within_budget_days": 2,
        "over_budget_days": 2,
        "over_maintenance_days": 1,
        "unclassified_budget_days": 0,
    }
    assert [day["classification"] for day in calendar_result["days"]] == [
        "under_budget",
        "over_budget",
        "under_budget",
        "above_maintenance",
        "over_budget",
    ]
    assert budget_balance(points) == expected
    assert trends(
        start=date(2024, 1, 1),
        end=date(2024, 1, 5),
        user=user,
        db=db,
    )["budget_balance"] == expected


def test_trends_budget_balance_is_user_scoped(db: Session, user: User) -> None:
    other = User(username="other", password_hash="test-password")
    db.add(other)
    db.flush()
    db.add(
        NutritionTarget(
            user_id=other.id,
            valid_from=date(2024, 1, 1),
            calories_kcal=Decimal("2000"),
            protein_g=Decimal("120"),
        )
    )
    db.commit()
    persist_import(
        db,
        user,
        AdapterResult("test", [metric(1, "dietary_energy_kcal", "1800")], received=1),
        None,
        "x-test-user",
        "test",
    )
    persist_import(
        db,
        other,
        AdapterResult("test", [metric(2, "dietary_energy_kcal", "1800")], received=1),
        None,
        "x-test-other",
        "test",
    )

    result = trends(user=user, db=db)

    assert result["budget_balance"]["tracked_days"] == 1
    assert result["budget_balance"]["within_budget_days"] == 1
    assert result["budget_balance"]["over_budget_days"] == 0


def test_trends_budget_balance_does_not_expand_empty_calendar_history(
    db: Session, user: User
) -> None:
    sample = metric(1, "dietary_energy_kcal", "1800")
    sample.start_at = datetime(1, 1, 1, tzinfo=UTC)
    sample.end_at = sample.start_at
    persist_import(
        db,
        user,
        AdapterResult("test", [sample], received=1),
        None,
        "x-test-early",
        "test",
    )

    result = trends(user=user, db=db)

    assert result["budget_balance"]["tracked_days"] == 1
    assert result["budget_balance"]["within_budget_days"] == 0
    assert result["budget_balance"]["unclassified_budget_days"] == 1


def test_calendar_budget_remains_primary_above_maintenance(
    db: Session, user: User
) -> None:
    target = user.targets[0]
    target.calories_kcal = Decimal("3000")
    target.maintenance_kcal = Decimal("2500")
    samples = [
        metric(1, "dietary_energy_kcal", "2800"),
        metric(2, "dietary_energy_kcal", "3200"),
    ]
    persist_import(
        db,
        user,
        AdapterResult("test", samples, received=len(samples)),
        None,
        "x-test",
        "test",
    )

    result = calendar(
        start=date(2024, 1, 1),
        end=date(2024, 1, 2),
        user=user,
        db=db,
    )

    assert [day["classification"] for day in result["days"]] == [
        "under_budget",
        "above_maintenance",
    ]
    assert [Decimal(day["deviation_kcal"]) for day in result["days"]] == [
        Decimal("-200"),
        Decimal("200"),
    ]



def test_activity_energy_is_credited_only_from_the_selected_source(
    db: Session, user: User
) -> None:
    target = user.targets[0]
    target.activity_mode = "full"
    target.activity_source_type = "apple_health_xml"
    samples = [
        metric(1, "dietary_energy_kcal", "2100"),
        metric(1, "active_energy_kcal", "500", "apple_health_xml"),
        metric(1, "active_energy_kcal", "200", "yazio_export_v1"),
    ]
    persist_import(
        db,
        user,
        AdapterResult("test", samples, received=len(samples)),
        None,
        "x-test",
        "test",
    )

    point = daily_points(db, user, date(2024, 1, 1), date(2024, 1, 1))[0]

    assert point.active_energy_kcal == Decimal("500")
    assert point.activity_credit_kcal == Decimal("500")
    assert point.effective_budget_kcal == Decimal("2500")
    assert point.effective_deviation_kcal == Decimal("-400")
    assert point.activity_data_status == "credited"


def test_activity_credit_remains_zero_when_selected_source_has_no_day(
    db: Session, user: User
) -> None:
    target = user.targets[0]
    target.activity_mode = "full"
    target.activity_source_type = "apple_health_xml"
    persist_import(
        db,
        user,
        AdapterResult(
            "test",
            [metric(1, "dietary_energy_kcal", "2100")],
            received=1,
        ),
        None,
        "x-test",
        "test",
    )

    point = daily_points(db, user, date(2024, 1, 1), date(2024, 1, 1))[0]

    assert point.active_energy_kcal is None
    assert point.activity_credit_kcal == Decimal()
    assert point.effective_budget_kcal == Decimal("2000")
    assert point.activity_data_status == "missing"
def test_daily_nutrition_source_filter_keeps_historical_activity_source(
    db: Session, user: User
) -> None:
    target = user.targets[0]
    target.activity_mode = "full"
    target.activity_source_type = "apple_health_xml"
    samples = [
        metric(1, "dietary_energy_kcal", "1800", "yazio_export_v1"),
        metric(1, "dietary_energy_kcal", "1700", "apple_health_xml"),
        metric(1, "active_energy_kcal", "300", "apple_health_xml"),
        metric(1, "active_energy_kcal", "900", "yazio_export_v1"),
    ]
    persist_import(
        db,
        user,
        AdapterResult("test", samples, received=len(samples)),
        None,
        "x-test-source-filter",
        "test",
    )

    result = daily(
        start=date(2024, 1, 1),
        end=date(2024, 1, 1),
        source="yazio_export_v1",
        tracking=None,
        weekday=None,
        user=user,
        db=db,
    )

    assert result[0].calories_kcal == Decimal("1800")
    assert result[0].activity_credit_kcal == Decimal("300")
    assert result[0].effective_budget_kcal == Decimal("2300")
    assert result[0].active_energy_kcal == Decimal("300")


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
