from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.importers.apple_xml import AppleFoodCorrelation, AppleFoodNutrient
from app.models import User
from app.nutrition.models import NutritionFieldObservation
from app.services.apple_health_nutrition_ingestion import (
    create_apple_health_nutrition_run,
    persist_apple_food_correlation,
)

POSTGRES_TESTS_ENABLED = (
    os.environ.get("CALOGRAPH_ALLOW_DESTRUCTIVE_POSTGRES_TESTS") == "1"
    and bool(os.environ.get("CALOGRAPH_POSTGRES_TEST_URL"))
)


def _correlation(value: Decimal, *, raw_type: str, unit: str) -> AppleFoodCorrelation:
    moment = datetime(2026, 8, 17, 10, 0, tzinfo=UTC)
    nutrient = AppleFoodNutrient(
        raw_type=raw_type,
        raw_value=str(value),
        value=value,
        unit=unit,
        start_at=moment,
        end_at=moment,
        source_name="Apple",
        source_version="1",
    )
    return AppleFoodCorrelation(
        source_name="Apple",
        source_version="1",
        device=None,
        start_at=moment,
        end_at=moment,
        creation_at=moment,
        food_name="PostgreSQL Boundary Test",
        external_uuid=f"postgres-{uuid4()}",
        metadata=(),
        nutrients=(nutrient,),
    )


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL tests are not explicitly enabled",
)
def test_apple_food_unsafe_decimal_never_reaches_numeric_column() -> None:
    database_url = os.environ["CALOGRAPH_POSTGRES_TEST_URL"]
    engine = sa.create_engine(database_url)
    try:
        with Session(engine) as db:
            user = User(
                username=f"apple-postgres-{uuid4().hex}",
                password_hash="hash",
                timezone="UTC",
            )
            db.add(user)
            db.flush()
            run = create_apple_health_nutrition_run(db, user_id=user.id)

            persist_apple_food_correlation(
                db,
                user_id=user.id,
                run=run,
                correlation=_correlation(
                    Decimal("1E+100"),
                    raw_type="HKQuantityTypeIdentifierDietaryProtein",
                    unit="g",
                ),
                timezone=user.timezone,
            )
            db.commit()

            field = db.scalar(select(NutritionFieldObservation))
            assert field is not None
            assert field.provider_raw_value_decimal is None
            assert field.canonical_value is None
    finally:
        engine.dispose()


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL tests are not explicitly enabled",
)
def test_apple_food_unit_conversion_overflow_never_reaches_numeric_column() -> None:
    database_url = os.environ["CALOGRAPH_POSTGRES_TEST_URL"]
    engine = sa.create_engine(database_url)
    try:
        with Session(engine) as db:
            user = User(
                username=f"apple-postgres-{uuid4().hex}",
                password_hash="hash",
                timezone="UTC",
            )
            db.add(user)
            db.flush()
            run = create_apple_health_nutrition_run(db, user_id=user.id)

            persist_apple_food_correlation(
                db,
                user_id=user.id,
                run=run,
                correlation=_correlation(
                    Decimal("2000000000"),
                    raw_type="HKQuantityTypeIdentifierDietaryVitaminC",
                    unit="g",
                ),
                timezone=user.timezone,
            )
            db.commit()

            field = db.scalar(select(NutritionFieldObservation))
            assert field is not None
            assert field.provider_raw_value_decimal == Decimal("2000000000")
            assert field.canonical_value is None
    finally:
        engine.dispose()
