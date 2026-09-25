from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import HealthSample, ImportBatch, User, WithingsConnection
from app.withings.constants import WITHINGS_MEASURE_TYPES, WITHINGS_WEIGHT_SOURCE_TYPE
from app.withings.ingestion import (
    WithingsMeasurementIngestionResult,
    ingest_withings_measurements,
)
from app.withings.parsers import WithingsMeasure, WithingsMeasureGroup, parse_measure_response


def _connection(db: Session, user: User) -> WithingsConnection:
    connection = WithingsConnection(user_id=user.id, state="active")
    db.add(connection)
    db.flush()
    return connection


def _group(
    group_id: int,
    *,
    measured_at: datetime = datetime(2024, 1, 1, 8, tzinfo=UTC),
    timezone: str = "UTC",
    measures: tuple[WithingsMeasure, ...] | None = None,
) -> WithingsMeasureGroup:
    if measures is None:
        measures = (
            WithingsMeasure(
                measure_type=1,
                metric_type="weight_kg",
                unit="kg",
                value=Decimal("82.5"),
                original_value=Decimal("825"),
                exponent=-1,
            ),
        )
    return WithingsMeasureGroup(group_id, measured_at, timezone, measures)


def test_ingestion_accepts_any_group_iterable(db: Session, user: User) -> None:
    connection = _connection(db, user)

    result = ingest_withings_measurements(
        db,
        user_id=user.id,
        source_instance_id=connection.id,
        groups=iter([_group(17)]),
    )

    assert result.inserted == 1
    assert result.received == 1


def test_ingestion_uses_user_timezone_when_measure_group_has_no_provider_timezone(
    db: Session, user: User
) -> None:
    connection = _connection(db, user)
    user.timezone = "Europe/Berlin"
    page = parse_measure_response(
        {
            "status": 0,
            "body": {
                "measuregrps": [
                    {
                        "grpid": 101,
                        "date": 1704065400,
                        "measures": [
                            {"type": measure_type, "value": 1234, "unit": -2}
                            for measure_type in WITHINGS_MEASURE_TYPES
                        ],
                    }
                ]
            },
        }
    )
    group = page.groups[0]

    assert group.timezone is None
    assert group.measured_at == datetime(2023, 12, 31, 23, 30, tzinfo=UTC)
    assert group.local_date is None

    result = ingest_withings_measurements(
        db,
        user_id=user.id,
        source_instance_id=connection.id,
        groups=page.groups,
    )

    rows = db.scalars(
        select(HealthSample).where(HealthSample.user_id == user.id)
    ).all()
    assert result.inserted == len(WITHINGS_MEASURE_TYPES)
    assert {row.local_date for row in rows} == {date(2024, 1, 1)}
    assert {row.timezone for row in rows} == {"Europe/Berlin"}



def test_ingestion_maps_all_supported_measures_and_preserves_decimal_exponent(
    db: Session, user: User
) -> None:
    connection = _connection(db, user)
    measures = tuple(
        WithingsMeasure(
            measure_type=measure_type,
            metric_type=metric_type,
            unit=unit,
            value=Decimal("0.123456789012"),
            original_value=Decimal("123456789012"),
            exponent=-12,
        )
        for measure_type, (metric_type, unit) in WITHINGS_MEASURE_TYPES.items()
    )

    result = ingest_withings_measurements(
        db,
        user_id=user.id,
        source_instance_id=connection.id,
        groups=[_group(100, measures=measures)],
    )

    assert isinstance(result, WithingsMeasurementIngestionResult)
    assert (result.received, result.inserted, result.updated, result.skipped) == (7, 7, 0, 0)
    rows = db.scalars(select(HealthSample).order_by(HealthSample.metric_type)).all()
    assert {row.metric_type: row.value for row in rows} == {
        metric_type: Decimal("0.123457") for metric_type, _ in WITHINGS_MEASURE_TYPES.values()
    }
    assert {row.metric_type: row.original_value for row in rows} == {
        metric_type: Decimal("123456789012.000000000000")
        for metric_type, _ in WITHINGS_MEASURE_TYPES.values()
    }
    assert {row.metric_type: row.original_unit for row in rows} == {
        metric_type: "-12" for metric_type, _ in WITHINGS_MEASURE_TYPES.values()
    }


def test_identity_uses_connection_and_group_measure_type_and_preserves_same_day_groups(
    db: Session, user: User
) -> None:
    connection = _connection(db, user)
    groups = [_group(10), _group(11, measured_at=datetime(2024, 1, 1, 9, tzinfo=UTC))]

    result = ingest_withings_measurements(
        db, user_id=user.id, source_instance_id=connection.id, groups=groups
    )

    assert result.inserted == 2
    rows = db.scalars(select(HealthSample).order_by(HealthSample.start_at)).all()
    assert [row.external_sample_id for row in rows] == ["10:1", "11:1"]
    assert [row.source_type for row in rows] == [WITHINGS_WEIGHT_SOURCE_TYPE] * 2
    assert [row.source_identifier for row in rows] == [str(connection.id)] * 2
    assert [row.local_date for row in rows] == [date(2024, 1, 1)] * 2
    assert all(row.end_at >= row.start_at for row in rows)


def test_retry_is_idempotent_and_reports_skips(db: Session, user: User) -> None:
    connection = _connection(db, user)
    groups = [_group(10)]

    first = ingest_withings_measurements(
        db, user_id=user.id, source_instance_id=connection.id, groups=groups
    )
    second = ingest_withings_measurements(
        db, user_id=user.id, source_instance_id=connection.id, groups=groups
    )

    assert (first.inserted, first.updated) == (1, 0)
    assert second.inserted == 0
    assert second.updated in (0, 1)
    assert second.skipped == 1
    assert db.scalar(select(func.count(HealthSample.id))) == 1
    assert db.scalar(select(func.count(ImportBatch.id))) == 2


def test_cross_user_connection_is_rejected_without_persistence(db: Session, user: User) -> None:
    other = User(username=f"other-{uuid4()}", password_hash="hash", timezone="UTC")
    db.add(other)
    db.flush()
    connection = _connection(db, other)

    with pytest.raises(ValueError, match="user scope"):
        ingest_withings_measurements(
            db, user_id=user.id, source_instance_id=connection.id, groups=[_group(10)]
        )

    assert db.scalar(select(func.count(HealthSample.id))) == 0
    assert db.scalar(select(func.count(ImportBatch.id))) == 0


def test_unknown_measure_types_are_ignored_by_parser() -> None:
    page = parse_measure_response(
        {
            "status": 0,
            "body": {
                "timezone": "UTC",
                "measuregrps": [
                    {
                        "grpid": 10,
                        "date": 1704096000,
                        "measures": [
                            {"type": 999999, "value": 123, "unit": 0},
                            {"type": 1, "value": 825, "unit": -1},
                        ],
                    }
                ],
            },
        }
    )
    assert [measure.measure_type for measure in page.groups[0].measures] == [1]


@pytest.mark.parametrize(
    "bad_measure",
    [
        WithingsMeasure(1, "weight_kg", "kg", Decimal("-1"), Decimal("-10"), -1),
        WithingsMeasure(1, "weight_kg", "kg", Decimal("NaN"), Decimal("1"), 0),
        WithingsMeasure(1, "weight_kg", "kg", Decimal("Infinity"), Decimal("1"), 0),
    ],
)
def test_malformed_negative_or_nonfinite_values_fail_safely(
    db: Session, user: User, bad_measure: WithingsMeasure
) -> None:
    connection = _connection(db, user)
    with pytest.raises(ValueError, match="value"):
        ingest_withings_measurements(
            db, user_id=user.id, source_instance_id=connection.id, groups=[_group(10, measures=(bad_measure,))]
        )
    assert db.scalar(select(func.count(HealthSample.id))) == 0
    assert db.scalar(select(func.count(ImportBatch.id))) == 0
