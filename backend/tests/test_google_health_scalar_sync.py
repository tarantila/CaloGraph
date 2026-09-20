from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.activity import ACTIVE_ENERGY_METRIC, ACTIVITY_PROVIDER_SOURCE_TYPES
from app.google_health.client import ActiveEnergyBurnedDataPoint, WeightDataPoint
from app.models import GoogleHealthConnection, HealthSample, User
from app.services.google_health_scalar_sync import (
    sync_google_health_activity,
    sync_google_health_weight,
)
from app.weight import WEIGHT_METRIC, WEIGHT_PROVIDER_SOURCE_TYPES


START = datetime(2026, 9, 15, 22, 30, tzinfo=UTC)
END = datetime(2026, 9, 15, 23, 0, tzinfo=UTC)


def _connection(db: Session, user: User) -> GoogleHealthConnection:
    item = GoogleHealthConnection(
        user_id=user.id,
        encrypted_refresh_token=b"encrypted",
        granted_scopes=[],
        state="active",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _activity() -> ActiveEnergyBurnedDataPoint:
    return ActiveEnergyBurnedDataPoint(
        name="users/me/dataTypes/active-energy-burned/dataPoints/energy-1",
        start_time=START,
        end_time=END,
        value=Decimal("125.5"),
        unit="kcal",
        start_utc_offset="0s",
        end_utc_offset="0s",
    )


def _weight(name: str = "users/me/dataTypes/weight/dataPoints/weight-1") -> WeightDataPoint:
    return WeightDataPoint(
        name=name,
        start_time=datetime(2026, 9, 15, 23, 30, tzinfo=UTC),
        end_time=datetime(2026, 9, 15, 23, 30, tzinfo=UTC),
        value=Decimal("72.5"),
        unit="kilograms",
        start_utc_offset="0s",
        end_utc_offset="0s",
    )


def test_activity_persists_canonical_interval_and_is_idempotent(db: Session, user: User) -> None:
    connection = _connection(db, user)

    first = sync_google_health_activity(
        db,
        user_id=user.id,
        source_instance_id=connection.id,
        requested_start=date(2026, 9, 16),
        requested_end=date(2026, 9, 16),
        data_points=(_activity(),),
    )
    second = sync_google_health_activity(
        db,
        user_id=user.id,
        source_instance_id=connection.id,
        requested_start=date(2026, 9, 16),
        requested_end=date(2026, 9, 16),
        data_points=(_activity(),),
    )

    sample = db.scalar(select(HealthSample).where(HealthSample.user_id == user.id))
    assert first.inserted == 1
    assert second.inserted == 0
    assert second.skipped == 1
    assert sample is not None
    assert sample.metric_type == ACTIVE_ENERGY_METRIC
    assert sample.value == Decimal("125.500000")
    assert sample.unit == "kcal"
    assert sample.source_type == "google_health_activity_v4"
    assert sample.source_identifier == str(connection.id)
    assert sample.external_sample_id == _activity().name
    assert sample.start_at == START.replace(tzinfo=None)
    assert sample.end_at == END.replace(tzinfo=None)
    assert sample.local_date == date(2026, 9, 16)


def test_weight_normalizes_and_orders_latest_of_local_day(db: Session, user: User) -> None:
    connection = _connection(db, user)
    earlier = _weight("users/me/dataTypes/weight/dataPoints/weight-early")
    later = WeightDataPoint(
        name="users/me/dataTypes/weight/dataPoints/weight-late",
        start_time=datetime(2026, 9, 16, 0, 15, tzinfo=UTC),
        end_time=datetime(2026, 9, 16, 0, 15, tzinfo=UTC),
        value=Decimal("71.75"),
        unit="kilograms",
        start_utc_offset="0s",
        end_utc_offset="0s",
    )

    result = sync_google_health_weight(
        db,
        user_id=user.id,
        source_instance_id=connection.id,
        requested_start=date(2026, 9, 15),
        requested_end=date(2026, 9, 9 + 7),
        data_points=(later, earlier),
    )

    samples = list(
        db.scalars(
            select(HealthSample)
            .where(HealthSample.user_id == user.id, HealthSample.metric_type == WEIGHT_METRIC)
            .order_by(HealthSample.start_at)
        )
    )
    assert result.inserted == 2
    assert [sample.value for sample in samples] == [Decimal("72.500000"), Decimal("71.750000")]
    assert samples[-1].source_type == "google_health_weight_v4"
    assert samples[-1].unit == "kg"
    assert samples[-1].original_value == Decimal("71.75")
    assert samples[-1].original_unit == "kilograms"
    assert samples[-1].start_at == later.start_time.replace(tzinfo=None)
    assert samples[-1].end_at == later.end_time.replace(tzinfo=None)
    assert samples[-1].local_date == date(2026, 9, 16)


@pytest.mark.parametrize(
    "point",
    [
        ActiveEnergyBurnedDataPoint(
            name="users/me/dataTypes/active-energy-burned/dataPoints/bad-unit",
            start_time=START,
            end_time=END,
            value=Decimal("1"),
            unit="kj",
        ),
        ActiveEnergyBurnedDataPoint(
            name="users/me/dataTypes/active-energy-burned/dataPoints/bad-value",
            start_time=START,
            end_time=END,
            value=Decimal("-1"),
            unit="kcal",
        ),
        ActiveEnergyBurnedDataPoint(
            name="",
            start_time=START,
            end_time=END,
            value=Decimal("1"),
            unit="kcal",
        ),
    ],
)
def test_invalid_scalar_input_persists_nothing(db: Session, user: User, point) -> None:
    connection = _connection(db, user)
    with pytest.raises(ValueError):
        sync_google_health_activity(
            db,
            user_id=user.id,
            source_instance_id=connection.id,
            requested_start=date(2026, 9, 15),
            requested_end=date(2026, 9, 15),
            data_points=(point,),
        )
    assert db.scalar(select(func.count(HealthSample.id))) == 0


def test_cross_user_connection_is_rejected_without_persistence(db: Session, user: User) -> None:
    other = User(username=f"other-{uuid4()}", password_hash="hash", timezone="Europe/Berlin")
    db.add(other)
    db.commit()
    connection = _connection(db, other)

    with pytest.raises(ValueError):
        sync_google_health_weight(
            db,
            user_id=user.id,
            source_instance_id=connection.id,
            requested_start=date(2026, 9, 15),
            requested_end=date(2026, 9, 15),
            data_points=(_weight(),),
        )
    assert db.scalar(select(func.count(HealthSample.id))) == 0


def test_source_type_constants_are_isolated() -> None:
    assert ACTIVITY_PROVIDER_SOURCE_TYPES["google_health"] == "google_health_activity_v4"
    assert WEIGHT_PROVIDER_SOURCE_TYPES["google_health"] == "google_health_weight_v4"
