from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.activity import ACTIVE_ENERGY_METRIC
from app.models import HealthSample, WithingsConnection
from app.withings.parsers import WithingsActivity, parse_activity_response
from app.withings.sync import _activity_sample, ingest_withings_activity


def test_daily_activity_maps_calories_to_stable_daily_sample(db, user) -> None:
    connection = WithingsConnection(user_id=user.id, state="active")
    db.add(connection)
    db.flush()

    result = ingest_withings_activity(
        db,
        user_id=user.id,
        source_instance_id=connection.id,
        activities=(WithingsActivity(date(2026, 9, 20), Decimal("321.5")),),
    )

    sample = db.scalar(select(HealthSample).where(HealthSample.user_id == user.id))
    assert result.inserted == 1
    assert sample is not None
    assert sample.metric_type == ACTIVE_ENERGY_METRIC
    assert sample.value == Decimal("321.500000")
    assert sample.original_value == Decimal("321.500000000000")
    assert sample.original_value.as_tuple().exponent == -12
    assert sample.unit == "kcal"
    assert sample.source_type == "withings_activity_v2"
    assert sample.source_identifier == str(connection.id)
    assert sample.external_sample_id == "2026-09-20"
    assert sample.local_date == date(2026, 9, 20)
    assert sample.start_at == sample.end_at


def test_activity_ingestion_accepts_any_activity_iterable(db, user) -> None:
    connection = WithingsConnection(user_id=user.id, state="active")
    db.add(connection)
    db.flush()

    result = ingest_withings_activity(
        db,
        user_id=user.id,
        source_instance_id=connection.id,
        activities=iter([WithingsActivity(date(2026, 9, 20), Decimal("0"))]),
    )

    sample = db.scalar(select(HealthSample).where(HealthSample.user_id == user.id))
    assert result.inserted == 1
    assert sample is not None
    assert sample.value == Decimal("0.000000")


def test_activity_ingestion_projects_each_scale_directly_from_raw_decimal(db, user) -> None:
    connection = WithingsConnection(user_id=user.id, state="active")
    db.add(connection)
    db.flush()
    raw_calories = Decimal("1.2345674999996")

    result = ingest_withings_activity(
        db,
        user_id=user.id,
        source_instance_id=connection.id,
        activities=(WithingsActivity(date(2026, 9, 20), raw_calories),),
    )

    sample = db.scalar(select(HealthSample).where(HealthSample.user_id == user.id))
    assert result.inserted == 1
    assert sample is not None
    assert sample.metric_type == ACTIVE_ENERGY_METRIC
    assert sample.unit == "kcal"
    assert sample.original_value == Decimal("1.234567500000")
    assert sample.original_value.as_tuple().exponent == -12
    assert sample.value == Decimal("1.234567")
    assert sample.value.as_tuple().exponent == -6


def test_activity_projection_rejects_negative_calories() -> None:
    with pytest.raises(ValueError):
        _activity_sample(
            WithingsActivity(date(2026, 9, 20), Decimal("-0.1")),
            timezone="UTC",
            source_identifier="synthetic-connection",
        )


def test_activity_projection_rejects_source_value_outside_database_range() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _activity_sample(
            WithingsActivity(date(2026, 9, 20), Decimal("1000000000000")),
            timezone="UTC",
            source_identifier="synthetic-connection",
        )

    errors = exc_info.value.errors(
        include_input=False, include_context=False, include_url=False
    )
    assert errors[0]["loc"] == ("original_value",)
    assert errors[0]["type"] == "original_value_out_of_range"


def test_activity_projection_rejects_value_outside_canonical_database_range() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _activity_sample(
            WithingsActivity(date(2026, 9, 20), Decimal("100000000000000")),
            timezone="UTC",
            source_identifier="synthetic-connection",
        )

    errors = exc_info.value.errors(
        include_input=False, include_context=False, include_url=False
    )
    assert errors[0]["loc"] == ("value",)
    assert errors[0]["type"] == "canonical_value_out_of_range"


def test_seven_provider_calendar_dates_ingest_as_seven_active_energy_days(db, user) -> None:
    connection = WithingsConnection(user_id=user.id, state="active")
    db.add(connection)
    db.flush()
    response = {
        "status": 0,
        "body": {
            "activities": [
                {
                    "date": "2020-06-24",
                    "timezone": "Pacific/Kiritimati",
                    "steps": 1000,
                    "calories": 10,
                    "totalcalories": 110,
                },
                {
                    "date": "2020-06-25",
                    "timezone": "Pacific/Kiritimati",
                    "steps": 1001,
                    "calories": 11,
                    "totalcalories": 111,
                },
                {
                    "date": "2020-06-26",
                    "timezone": "Pacific/Kiritimati",
                    "steps": 1002,
                    "calories": 12,
                    "totalcalories": 112,
                },
                {
                    "date": "2020-06-27",
                    "timezone": "Pacific/Kiritimati",
                    "steps": 1003,
                    "calories": 13,
                    "totalcalories": 113,
                },
                {
                    "date": "2020-06-28",
                    "timezone": "Pacific/Kiritimati",
                    "steps": 1004,
                    "calories": 14,
                    "totalcalories": 114,
                },
                {
                    "date": "2020-06-29",
                    "timezone": "Pacific/Kiritimati",
                    "steps": 1005,
                    "calories": 15,
                    "totalcalories": 115,
                },
                {
                    "date": "2020-06-30",
                    "timezone": "Pacific/Kiritimati",
                    "steps": 1006,
                    "calories": 16,
                    "totalcalories": 116,
                },
            ],
            "more": 0,
            "offset": 0,
        },
    }
    parsed = parse_activity_response(response)

    result = ingest_withings_activity(
        db,
        user_id=user.id,
        source_instance_id=connection.id,
        activities=parsed.activities,
    )
    rows = list(
        db.scalars(
            select(HealthSample)
            .where(HealthSample.user_id == user.id)
            .order_by(HealthSample.local_date)
        )
    )

    expected_dates = (
        date(2020, 6, 24),
        date(2020, 6, 25),
        date(2020, 6, 26),
        date(2020, 6, 27),
        date(2020, 6, 28),
        date(2020, 6, 29),
        date(2020, 6, 30),
    )
    assert result.inserted == 7
    assert len(rows) == 7
    assert tuple(row.local_date for row in rows) == expected_dates
    assert tuple(row.external_sample_id for row in rows) == tuple(
        item.isoformat() for item in expected_dates
    )
    assert tuple(row.metric_type for row in rows) == (ACTIVE_ENERGY_METRIC,) * 7
    assert tuple(row.value for row in rows) == tuple(
        Decimal(value) for value in ("10", "11", "12", "13", "14", "15", "16")
    )
    assert {row.timezone for row in rows} == {user.timezone}



def test_zero_daily_activity_is_evidence_and_retry_is_idempotent(db, user) -> None:
    connection = WithingsConnection(user_id=user.id, state="active")
    db.add(connection)
    db.flush()
    activity = WithingsActivity(date(2026, 9, 20), Decimal("0"))

    first = ingest_withings_activity(
        db,
        user_id=user.id,
        source_instance_id=connection.id,
        activities=(activity,),
    )
    second = ingest_withings_activity(
        db,
        user_id=user.id,
        source_instance_id=connection.id,
        activities=(activity,),
    )

    assert (first.inserted, first.updated, first.skipped) == (1, 0, 0)
    assert (second.inserted, second.updated, second.skipped) == (0, 0, 1)
    samples = list(db.scalars(select(HealthSample).where(HealthSample.user_id == user.id)))
    assert len(samples) == 1
    assert samples[0].import_batch_id == first.batch_id
    assert samples[0].value == Decimal("0.000000")

def test_same_daily_identity_updates_changed_canonical_calories(db, user) -> None:
    connection = WithingsConnection(user_id=user.id, state="active")
    db.add(connection)
    db.flush()
    activity_date = date(2026, 9, 24)

    first = ingest_withings_activity(
        db,
        user_id=user.id,
        source_instance_id=connection.id,
        activities=(WithingsActivity(activity_date, Decimal("26.49")),),
    )
    original = db.scalar(
        select(HealthSample).where(
            HealthSample.user_id == user.id,
            HealthSample.source_type == "withings_activity_v2",
        )
    )
    assert (first.inserted, first.updated, first.skipped) == (1, 0, 0)
    assert original is not None
    original_id = original.id

    updated = ingest_withings_activity(
        db,
        user_id=user.id,
        source_instance_id=connection.id,
        activities=(WithingsActivity(activity_date, Decimal("52.24")),),
    )

    rows = list(
        db.scalars(
            select(HealthSample).where(
                HealthSample.user_id == user.id,
                HealthSample.source_type == "withings_activity_v2",
            )
        )
    )
    assert (updated.inserted, updated.updated, updated.skipped) == (0, 1, 0)
    assert len(rows) == 1
    sample = rows[0]
    assert sample.id == original_id
    assert sample.external_sample_id == "2026-09-24"
    assert sample.source_identifier == str(connection.id)
    assert sample.metric_type == ACTIVE_ENERGY_METRIC
    assert sample.value == Decimal("52.240000")
    assert sample.value.as_tuple().exponent == -6
    assert sample.unit == "kcal"
    assert sample.original_value == Decimal("52.240000000000")
    assert sample.original_value.as_tuple().exponent == -12
    assert sample.original_unit == "kcal"
    assert sample.local_date == activity_date
    assert sample.start_at == sample.end_at
    assert sample.timezone == user.timezone
    assert sample.source_name == "Withings"
    assert sample.import_batch_id == updated.batch_id
