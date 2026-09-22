from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.google_health.client import GoogleHealthDailyRollupDataPoint, GoogleHealthDailyRollupPage
from app.models import GoogleHealthConnection, HealthSample, User
from app.services.credential_crypto import encrypt_credential
from app.services.google_health_scalar_sync import sync_google_health_activity
from app.services.google_health_sync import GoogleHealthSyncService


def _connection(db: Session, user: User) -> GoogleHealthConnection:
    item = GoogleHealthConnection(
        user_id=user.id,
        client_id="client-id",
        encrypted_client_secret=encrypt_credential("client-secret"),
        encrypted_refresh_token=encrypt_credential("refresh-token"),
        granted_scopes=[],
        state="active",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def test_daily_rollup_persists_one_civil_day_and_updates_it_idempotently(
    db: Session, user: User
) -> None:
    connection = _connection(db, user)
    first = GoogleHealthDailyRollupDataPoint(date(2026, 9, 16), Decimal("125.123456789012"))
    second = GoogleHealthDailyRollupDataPoint(date(2026, 9, 16), Decimal("130.123456789012"))

    inserted = sync_google_health_activity(
        db,
        user_id=user.id,
        source_instance_id=connection.id,
        requested_start=date(2026, 9, 16),
        requested_end=date(2026, 9, 16),
        data_points=(first,),
    )
    updated = sync_google_health_activity(
        db,
        user_id=user.id,
        source_instance_id=connection.id,
        requested_start=date(2026, 9, 16),
        requested_end=date(2026, 9, 16),
        data_points=(second,),
    )

    samples = list(db.scalars(select(HealthSample).where(HealthSample.user_id == user.id)))
    assert inserted.inserted == 1
    assert updated.updated == 1
    assert len(samples) == 1
    assert samples[0].value == Decimal("130.123457")
    assert samples[0].original_value == Decimal("130.123456789012")
    assert samples[0].external_sample_id == "daily-rollup:active-energy:2026-09-16"
    assert samples[0].local_date == date(2026, 9, 16)
    assert samples[0].source_type == "google_health_activity_v4"
    assert samples[0].metric_type == "active_energy_kcal"


def test_activity_paging_prefers_daily_rollup_over_legacy_data_points() -> None:
    point = GoogleHealthDailyRollupDataPoint(date(2026, 9, 16), Decimal("1"))

    class Client:
        def iter_daily_rollup_pages(self, **kwargs: object):
            assert kwargs["start_date"] == date(2026, 9, 16)
            assert kwargs["end_date"] == date(2026, 9, 16)
            yield GoogleHealthDailyRollupPage((point,), None, None, 0, 0)

        def get_data_points_page(self, **_kwargs: object):
            raise AssertionError("legacy scalar endpoint must not be used")

    service = GoogleHealthSyncService(session_factory=lambda: None)  # type: ignore[arg-type]
    points, truncated = service._pages(
        Client(),
        data_type="active-energy-burned",
        start=date(2026, 9, 16),
        end=date(2026, 9, 16),
        timezone="UTC",
    )

    assert points == (point,)
    assert truncated is False


def test_activity_daily_rollup_exact_budget_with_next_token_is_truncated() -> None:
    point = GoogleHealthDailyRollupDataPoint(date(2026, 9, 16), Decimal("1"))

    class Client:
        def iter_daily_rollup_pages(self, **_kwargs: object):
            yield GoogleHealthDailyRollupPage((point,), "next", None, 0, 0)

    service = GoogleHealthSyncService(session_factory=lambda: None, max_points=1)  # type: ignore[arg-type]
    points, truncated = service._pages(
        Client(),
        data_type="active-energy-burned",
        start=date(2026, 9, 16),
        end=date(2026, 9, 16),
        timezone="UTC",
    )

    assert points == (point,)
    assert truncated is True


def test_explicit_production_hard_budget_reports_truncation() -> None:
    calls: list[object] = []

    class Client:
        def get_nutrition_log_page(self, **_kwargs: object):
            calls.append(object())
            return type(
                "Page",
                (),
                {
                    "data_points": tuple(object() for _ in range(100)),
                    "next_page_token": f"token-{len(calls)}",
                },
            )()

    service = GoogleHealthSyncService(
        session_factory=lambda: None,  # type: ignore[arg-type]
        max_pages=3,
        max_points=300,
    )
    points, truncated = service._pages(
        Client(),
        data_type="nutrition-log",
        start=date(2026, 9, 16),
        end=date(2026, 9, 16),
        timezone="UTC",
    )

    assert len(calls) == 3
    assert len(points) == 300
    assert truncated is True


def test_activity_paging_rejects_missing_daily_rollup_seam() -> None:
    class Client:
        def get_data_points_page(self, **_kwargs: object):
            raise AssertionError("legacy activity GET must not be used")

    service = GoogleHealthSyncService(session_factory=lambda: None)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="daily rollup seam"):
        service._pages(
            Client(),
            data_type="active-energy-burned",
            start=date(2026, 9, 16),
            end=date(2026, 9, 16),
            timezone="UTC",
        )
