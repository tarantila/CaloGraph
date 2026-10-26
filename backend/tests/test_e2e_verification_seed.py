from __future__ import annotations

from datetime import date

import pytest
from e2e_verification_seed import _seed_nutrition
from sqlalchemy.orm import Session

from app.models import GoogleHealthConnection, User
from app.nutrition.models import NutritionConsumptionEvent, NutritionSourceObservation


@pytest.mark.parametrize(
    ("day", "expected_provider_timezone"),
    [
        (date(2026, 7, 1), "7200s"),
        (date(2026, 10, 25), "7200s"),
        (date(2026, 10, 26), "3600s"),
    ],
)
def test_google_seed_uses_date_specific_timezone_offset(
    db: Session,
    user: User,
    day: date,
    expected_provider_timezone: str,
) -> None:
    connection = GoogleHealthConnection(user_id=user.id)
    db.add(connection)
    db.flush()

    _seed_nutrition(db, user.id, connection.id, day)

    event = (
        db.query(NutritionConsumptionEvent)
        .filter(
            NutritionConsumptionEvent.user_id == user.id,
            NutritionConsumptionEvent.source_instance_id == connection.id,
            NutritionConsumptionEvent.logical_event_key == "synthetic-event-1",
        )
        .one()
    )
    observation = db.get(NutritionSourceObservation, event.source_observation_id)

    assert observation is not None
    assert observation.provider_timezone == expected_provider_timezone
    assert event.provider_timezone == expected_provider_timezone
