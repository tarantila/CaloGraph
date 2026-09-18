from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from app.analytics import provider_selection
from app.api import analytics as analytics_api
from app.models import HealthSample, ImportBatch
from app.schemas import DailyPoint

SOURCE_INSTANCE_ID = UUID("22222222-2222-2222-2222-222222222222")
TODAY = date(2026, 7, 23)


def _point(day: date, *, nutrition: bool = True) -> DailyPoint:
    calories = Decimal("1800") if nutrition else None
    protein = Decimal("40") if nutrition else None
    return DailyPoint(
        date=day,
        calories_kcal=calories,
        target_kcal=Decimal("2000"),
        maintenance_kcal=Decimal("2200"),
        deviation_kcal=Decimal("-200") if nutrition else None,
        activity_mode="full",
        activity_source_type="apple_health_xml",
        active_energy_kcal=Decimal("200"),
        activity_credit_kcal=Decimal("200"),
        activity_data_status="credited",
        effective_budget_kcal=Decimal("2200"),
        effective_maintenance_kcal=Decimal("2400"),
        effective_deviation_kcal=Decimal("-400") if nutrition else None,
        protein_g=protein,
        carbs_g=Decimal("200") if nutrition else None,
        fat_g=Decimal("60") if nutrition else None,
        tracking_status="complete" if nutrition else "no_data",
        tracking_score=1 if nutrition else 0,
        tracking_reasons=["Kalorienwert vorhanden"] if nutrition else [],
    )


def _summary_points(*, nutrition: bool = True) -> list[DailyPoint]:
    start = TODAY - timedelta(days=10)
    return [_point(start + timedelta(days=offset), nutrition=nutrition) for offset in range(14)]


def _fixed_datetime() -> type[datetime]:
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 23, 12, 0, tzinfo=tz)

    return FixedDatetime


@pytest.mark.parametrize("provider_key", ["yazio", "google_health", "apple_health"])
def test_dashboard_preference_serves_canonical_values_for_each_provider_without_legacy_read(
    monkeypatch, user, db, provider_key
) -> None:
    points = _summary_points()
    seen: dict[str, object] = {}

    def read_preferred(db_arg, *, user_id, selection, start, end):
        seen.update(
            db=db_arg,
            user_id=user_id,
            provider_key=selection.provider_key,
            source_instance_id=selection.source_instance_id,
            start=start,
            end=end,
        )
        return points

    monkeypatch.setattr(analytics_api, "datetime", _fixed_datetime())
    monkeypatch.setattr(
        analytics_api,
        "_preferred_nutrition_provider",
        lambda db_arg, user_id: provider_selection.NutritionProviderSelection(
            provider_key, SOURCE_INSTANCE_ID
        ),
    )
    monkeypatch.setattr(analytics_api, "_read_preferred_daily_points", read_preferred)
    monkeypatch.setattr(
        analytics_api,
        "daily_points",
        lambda *args, **kwargs: pytest.fail(
            "Dashboard Preference darf keine Legacy-Nutrition-Werte lesen"
        ),
    )

    result = analytics_api.summary(user=user, db=db)

    assert result["today"]["calories_kcal"] == "1800"
    assert result["today"]["protein_g"] == "40"
    assert result["today"]["active_energy_kcal"] == "200"
    assert result["today"]["activity_credit_kcal"] == "200"
    assert result["today"]["effective_budget_kcal"] == "2200"
    assert result["week"]["consumed_kcal"] == 7200.0
    assert result["week"]["activity_credit_kcal"] == 1400.0
    assert result["protein_7d_average_g"] == 40.0
    assert seen == {
        "db": db,
        "user_id": user.id,
        "provider_key": provider_key,
        "source_instance_id": SOURCE_INSTANCE_ID,
        "start": date(2026, 7, 13),
        "end": date(2026, 7, 26),
    }


@pytest.mark.parametrize("provider_key", ["yazio", "google_health", "apple_health"])
def test_dashboard_canonical_provider_is_not_cross_aggregated(
    monkeypatch, user, db, provider_key
) -> None:
    selected = provider_selection.NutritionProviderSelection(provider_key, SOURCE_INSTANCE_ID)
    seen: list[tuple[str, UUID]] = []

    monkeypatch.setattr(analytics_api, "datetime", _fixed_datetime())
    monkeypatch.setattr(analytics_api, "_preferred_nutrition_provider", lambda *args: selected)
    monkeypatch.setattr(
        analytics_api,
        "_read_preferred_daily_points",
        lambda _db, *, selection, **kwargs: (
            seen.append((selection.provider_key, selection.source_instance_id))
            or _summary_points()
        ),
    )
    monkeypatch.setattr(
        analytics_api,
        "daily_points",
        lambda *args, **kwargs: pytest.fail("Cross-provider Legacy-Fallback ist unzulässig"),
    )

    analytics_api.summary(user=user, db=db)

    assert seen == [(provider_key, SOURCE_INSTANCE_ID)]


def test_dashboard_without_preference_keeps_legacy_values(monkeypatch, user, db) -> None:
    legacy_points = _summary_points()

    monkeypatch.setattr(analytics_api, "datetime", _fixed_datetime())
    monkeypatch.setattr(analytics_api, "_preferred_nutrition_provider", lambda *args: None)
    monkeypatch.setattr(analytics_api, "daily_points", lambda *args, **kwargs: legacy_points)
    monkeypatch.setattr(
        analytics_api,
        "_read_preferred_daily_points",
        lambda *args, **kwargs: pytest.fail("Ohne Preference darf kein Canonical-Read erfolgen"),
    )

    result = analytics_api.summary(user=user, db=db)

    assert result["today"]["calories_kcal"] == "1800"


def test_dashboard_empty_canonical_nutrition_returns_no_data_without_legacy_fallback(
    monkeypatch, user, db
) -> None:
    empty_points = _summary_points(nutrition=False)

    monkeypatch.setattr(analytics_api, "datetime", _fixed_datetime())
    monkeypatch.setattr(
        analytics_api,
        "_preferred_nutrition_provider",
        lambda *args: provider_selection.NutritionProviderSelection(
            "apple_health", SOURCE_INSTANCE_ID
        ),
    )
    monkeypatch.setattr(analytics_api, "_read_preferred_daily_points", lambda *args, **kwargs: empty_points)
    monkeypatch.setattr(
        analytics_api,
        "daily_points",
        lambda *args, **kwargs: pytest.fail("Leere Canonical-Daten dürfen nicht Legacy lesen"),
    )

    result = analytics_api.summary(user=user, db=db)

    assert result["today"]["calories_kcal"] is None
    assert result["today"]["tracking_status"] == "no_data"
    assert result["protein_7d_average_g"] is None


@pytest.mark.parametrize(
    "error_type",
    [provider_selection.NutritionProviderUnavailable, provider_selection.NutritionProviderNotReady],
)
def test_dashboard_provider_selection_errors_fail_closed(monkeypatch, user, db, error_type) -> None:
    monkeypatch.setattr(analytics_api, "datetime", _fixed_datetime())
    monkeypatch.setattr(
        analytics_api,
        "resolve_nutrition_provider",
        lambda *args, **kwargs: (_ for _ in ()).throw(error_type("not usable")),
    )

    with pytest.raises(analytics_api.ProblemHTTPException) as caught:
        analytics_api.summary(user=user, db=db)

    assert caught.value.status_code == 503


def test_dashboard_keeps_import_coverage_metadata_separate_from_canonical_values(
    monkeypatch, user, db
) -> None:
    imported_day = date(2026, 7, 1)
    batch = ImportBatch(
        user_id=user.id,
        source_type="apple_health_xml",
        client_identifier="dashboard-test",
        status="completed",
        finished_at=datetime(2026, 7, 2, 12, 0),
    )
    db.add(batch)
    db.flush()
    amount = Decimal("900")
    db.add(
        HealthSample(
            user_id=user.id,
            import_batch_id=batch.id,
            external_sample_id="dashboard-metadata-sample",
            fingerprint="dashboard-metadata-fingerprint",
            source_type="apple_health_xml",
            source_name="Apple Health",
            source_identifier="dashboard-test",
            metric_type="dietary_energy_kcal",
            value=amount,
            unit="kcal",
            original_value=amount,
            original_unit="kcal",
            start_at=datetime(2026, 7, 1, 0, 0),
            end_at=datetime(2026, 7, 1, 0, 1),
            local_date=imported_day,
            timezone="Europe/Berlin",
        )
    )
    db.commit()

    monkeypatch.setattr(analytics_api, "datetime", _fixed_datetime())
    monkeypatch.setattr(
        analytics_api,
        "_preferred_nutrition_provider",
        lambda *args: provider_selection.NutritionProviderSelection(
            "google_health", SOURCE_INSTANCE_ID
        ),
    )
    monkeypatch.setattr(analytics_api, "_read_preferred_daily_points", lambda *args, **kwargs: _summary_points())
    monkeypatch.setattr(
        analytics_api,
        "daily_points",
        lambda *args, **kwargs: pytest.fail("Metadata darf keinen Legacy-Value-Read erzwingen"),
    )

    result = analytics_api.summary(user=user, db=db)

    assert result["today"]["calories_kcal"] == "1800"
    assert result["data_start_date"] == "2026-07-01"
    assert result["data_end_date"] == "2026-07-01"
    assert result["data_day_count"] == 1
    assert result["last_import_at"] == "2026-07-02T12:00:00"
