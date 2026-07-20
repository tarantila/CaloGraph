from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.analytics.service import daily_points, percentile
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


def test_percentiles() -> None:
    values = [Decimal(value) for value in (1, 2, 3, 4, 5)]
    assert percentile(values, Decimal("0.25")) == Decimal("2")
    assert percentile(values, Decimal("0.75")) == Decimal("4")
