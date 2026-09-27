from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import HealthSample, ImportBatch, User

WEIGHT_PATH = "/api/v1/analytics/weight"
PASSWORD = "correct-horse-battery-staple"


def _login(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": PASSWORD},
    )
    assert response.status_code == 200


def _add_weight(
    db: Session,
    user: User,
    day: date,
    value: str,
    *,
    suffix: str = "",
) -> None:
    source_type = "health_auto_export_v2"
    timestamp = datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(hours=8)
    external_id = f"{day.isoformat()}-{suffix or value}"
    batch = ImportBatch(user_id=user.id, source_type=source_type, status="completed")
    db.add(batch)
    db.flush()
    db.add(
        HealthSample(
            user_id=user.id,
            import_batch_id=batch.id,
            external_sample_id=external_id,
            fingerprint=sha256(f"{user.id}:{external_id}".encode()).hexdigest(),
            source_type=source_type,
            source_identifier="synthetic-health-device",
            metric_type="weight_kg",
            value=Decimal(value),
            unit="kg",
            original_value=Decimal(value),
            original_unit="kg",
            start_at=timestamp,
            end_at=timestamp,
            local_date=day,
            timezone="UTC",
        )
    )


def test_weight_summary_uses_sparse_selected_points_and_preserves_missing_days(
    client: TestClient,
    user: User,
    db: Session,
) -> None:
    start = date(2026, 9, 1)
    _add_weight(db, user, start, "70.0")
    _add_weight(db, user, start + timedelta(days=2), "69.5")
    db.commit()
    _login(client)

    response = client.get(
        WEIGHT_PATH,
        params={"start": start.isoformat(), "end": (start + timedelta(days=3)).isoformat()},
    )

    assert response.status_code == 200
    payload = response.json()
    assert [point["date"] for point in payload["points"]] == [
        start.isoformat(),
        (start + timedelta(days=2)).isoformat(),
    ]
    assert payload["summary"] == {
        "measurement_count": 2,
        "latest": {"date": (start + timedelta(days=2)).isoformat(), "weight_kg": 69.5},
        "change_kg": -0.5,
        "average_kg": 69.75,
        "minimum_kg": 69.5,
        "maximum_kg": 70.0,
    }


def test_weight_summary_has_no_change_without_two_measurements(
    client: TestClient,
    user: User,
    db: Session,
) -> None:
    day = date(2026, 9, 1)
    _add_weight(db, user, day, "69.5")
    db.commit()
    _login(client)

    response = client.get(WEIGHT_PATH, params={"start": day.isoformat(), "end": day.isoformat()})

    assert response.status_code == 200
    assert response.json()["summary"] == {
        "measurement_count": 1,
        "latest": {"date": day.isoformat(), "weight_kg": 69.5},
        "change_kg": None,
        "average_kg": 69.5,
        "minimum_kg": 69.5,
        "maximum_kg": 69.5,
    }


def test_weight_reader_accepts_maximum_inclusive_range_and_rejects_one_day_more(
    client: TestClient,
    user: User,
) -> None:
    _login(client)
    end = date(2026, 9, 30)

    maximum = client.get(
        WEIGHT_PATH,
        params={"start": (end - timedelta(days=3660)).isoformat(), "end": end.isoformat()},
    )
    too_wide = client.get(
        WEIGHT_PATH,
        params={"start": (end - timedelta(days=3661)).isoformat(), "end": end.isoformat()},
    )

    assert maximum.status_code == 200
    assert too_wide.status_code == 422
