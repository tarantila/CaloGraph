from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.analytics import provider_daily
from app.analytics.provider_selection import (
    NutritionProviderSelection,
    NutritionProviderUnavailable,
)
from app.api import analytics as analytics_api
from app.nutrition.enums import CoverageState, PresenceState, ResolutionState
from app.nutrition.resolution import DAILY_PROJECTION_METRICS
from app.schemas import DailyPoint

USER_ID = UUID("11111111-1111-1111-1111-111111111111")
SOURCE_INSTANCE_ID = UUID("22222222-2222-2222-2222-222222222222")
DAY = date(2026, 9, 1)


def _point() -> DailyPoint:
    return DailyPoint(
        date=DAY,
        calories_kcal=Decimal("1800"),
        target_kcal=None,
        maintenance_kcal=None,
        deviation_kcal=None,
        activity_mode=None,
        activity_source_type=None,
        active_energy_kcal=None,
        activity_credit_kcal=Decimal("0"),
        activity_data_status="disabled",
        effective_budget_kcal=None,
        effective_maintenance_kcal=None,
        effective_deviation_kcal=None,
        protein_g=Decimal("40"),
        carbs_g=Decimal("200"),
        fat_g=Decimal("60"),
        tracking_status="complete",
        tracking_score=1,
        tracking_reasons=["Kalorienwert vorhanden"],
    )


def test_daily_preference_serves_canonical_provider_without_legacy_value_read(monkeypatch):
    user = SimpleNamespace(id=USER_ID, timezone="UTC")
    selection = NutritionProviderSelection("google_health", SOURCE_INSTANCE_ID)

    monkeypatch.setattr(analytics_api, "resolve_nutrition_provider", lambda *args, **kwargs: selection)
    monkeypatch.setattr(
        analytics_api,
        "read_provider_daily_points",
        lambda *args, **kwargs: [_point()],
        raising=False,
    )
    monkeypatch.setattr(
        analytics_api,
        "daily_points",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("canonical preference must not read Legacy HealthSample nutrition values")
        ),
    )
    monkeypatch.setattr(analytics_api, "_unlock_big_picture_if_requested", lambda *args: None)

    result = analytics_api.daily(
        start=DAY,
        end=DAY,
        source=None,
        tracking=None,
        weekday=None,
        period=None,
        user=user,
        db=object(),
    )

    assert result == [_point()]


def test_daily_explicit_source_keeps_legacy_path(monkeypatch):
    user = SimpleNamespace(id=USER_ID, timezone="UTC")

    monkeypatch.setattr(
        analytics_api,
        "resolve_nutrition_provider",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("explicit source must not resolve the saved preference")
        ),
    )
    monkeypatch.setattr(analytics_api, "_unlock_big_picture_if_requested", lambda *args: None)
    monkeypatch.setattr(analytics_api, "daily_points", lambda *args, **kwargs: [_point()])

    result = analytics_api.daily(
        start=DAY,
        end=DAY,
        source="yazio_export_v1",
        tracking=None,
        weekday=None,
        period=None,
        user=user,
        db=object(),
    )

    assert result == [_point()]


def test_daily_without_preference_keeps_legacy_path(monkeypatch):
    user = SimpleNamespace(id=USER_ID, timezone="UTC")

    monkeypatch.setattr(analytics_api, "resolve_nutrition_provider", lambda *args, **kwargs: None)
    monkeypatch.setattr(analytics_api, "_unlock_big_picture_if_requested", lambda *args: None)
    monkeypatch.setattr(analytics_api, "daily_points", lambda *args, **kwargs: [_point()])
    monkeypatch.setattr(
        analytics_api,
        "read_provider_daily_points",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("no preference must not read the provider path")
        ),
    )

    result = analytics_api.daily(
        start=DAY,
        end=DAY,
        source=None,
        tracking=None,
        weekday=None,
        period=None,
        user=user,
        db=object(),
    )

    assert result == [_point()]


def test_provider_daily_reads_all_projection_metrics_in_one_bounded_read(monkeypatch):
    calls = []

    def candidate(local_date: date, metric_key: str):
        return SimpleNamespace(
            local_date=local_date,
            metric_key=metric_key,
            value=Decimal("1"),
            value_contributing=True,
            presence_state=PresenceState.SUPPLIED,
            coverage_state=CoverageState.COMPLETE,
            resolution_state=ResolutionState.RESOLVED,
        )

    def fake_period(db, **kwargs):
        del db
        calls.append(kwargs)
        return {
            local_date: {
                metric_key: candidate(local_date, metric_key)
                for metric_key in kwargs["metric_keys"]
            }
            for offset in range((kwargs["end"] - kwargs["start"]).days + 1)
            for local_date in [kwargs["start"] + provider_daily.timedelta(days=offset)]
        }

    monkeypatch.setattr(provider_daily, "resolve_provider_period", fake_period)
    monkeypatch.setattr(
        provider_daily,
        "_build_daily_point",
        lambda **kwargs: kwargs,
    )

    class EmptyDb:
        def scalars(self, statement):
            del statement
            return []

        def execute(self, statement):
            del statement
            return SimpleNamespace(all=lambda: [])

    start = date(2026, 9, 1)
    end = date(2026, 10, 2)
    result = provider_daily.read_provider_daily_points(
        EmptyDb(),
        user_id=USER_ID,
        provider_key="apple_health",
        source_instance_id=SOURCE_INSTANCE_ID,
        start=start,
        end=end,
    )

    assert len(result) == 32
    assert [(call["start"], call["end"]) for call in calls] == [(start, end)]
    assert calls[0]["max_days"] == 32
    assert all(set(call["metric_keys"]) == set(DAILY_PROJECTION_METRICS) for call in calls)
    assert all(call["provider_key"] == "apple_health" for call in calls)
    assert all(call["user_id"] == USER_ID for call in calls)
    assert all(call["source_instance_id"] == SOURCE_INSTANCE_ID for call in calls)


def test_daily_point_consumers_use_one_provider_path_without_legacy_points(monkeypatch):
    user = SimpleNamespace(id=USER_ID, timezone="UTC", week_starts_on=0)
    selection = NutritionProviderSelection("apple_health", SOURCE_INSTANCE_ID)

    monkeypatch.setattr(analytics_api, "resolve_nutrition_provider", lambda *args, **kwargs: selection)
    monkeypatch.setattr(analytics_api, "read_provider_daily_points", lambda *args, **kwargs: [_point()])
    monkeypatch.setattr(
        analytics_api,
        "daily_points",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("provider preference must not mix Legacy daily points")
        ),
    )
    monkeypatch.setattr(
        analytics_api,
        "_unlock_big_picture_if_requested",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("provider preference must not use the Legacy achievement hook")
        ),
    )

    calendar = analytics_api.calendar(start=DAY, end=DAY, user=user, db=object())
    weekly = analytics_api.weekly(start=DAY, end=DAY, user=user, db=object())
    weekdays = analytics_api.weekdays(
        start=DAY,
        end=DAY,
        period=None,
        user=user,
        db=object(),
    )
    trends = analytics_api.trends(
        start=DAY,
        end=DAY,
        include_incomplete=False,
        period=None,
        user=user,
        db=object(),
    )

    assert len(calendar["days"]) == 1
    assert len(weekly["weeks"]) == 1
    assert len(weekdays["weekdays"]) == 7
    assert len(trends["points"]) == 1


def test_daily_provider_unavailable_fails_closed(monkeypatch):
    user = SimpleNamespace(id=USER_ID, timezone="UTC")

    monkeypatch.setattr(
        analytics_api,
        "resolve_nutrition_provider",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            NutritionProviderUnavailable("provider offline")
        ),
    )

    with pytest.raises(analytics_api.ProblemHTTPException) as caught:
        analytics_api.daily(
            start=DAY,
            end=DAY,
            source=None,
            tracking=None,
            weekday=None,
            period=None,
            user=user,
            db=object(),
        )

    assert caught.value.status_code == 503


def test_provider_daily_empty_provider_data_returns_no_data_points(monkeypatch):
    def fake_period(db, **kwargs):
        del db
        return {
            kwargs["start"]: {
                metric_key: SimpleNamespace(
                    value=None,
                    value_contributing=False,
                    presence_state=PresenceState.MISSING,
                    coverage_state=CoverageState.UNKNOWN,
                    resolution_state=ResolutionState.UNRESOLVED,
                )
                for metric_key in kwargs["metric_keys"]
            }
        }

    class EmptyDb:
        def scalars(self, statement):
            del statement
            return []

        def execute(self, statement):
            del statement
            return SimpleNamespace(all=lambda: [])

    monkeypatch.setattr(provider_daily, "resolve_provider_period", fake_period)

    result = provider_daily.read_provider_daily_points(
        EmptyDb(),
        user_id=USER_ID,
        provider_key="yazio",
        source_instance_id=SOURCE_INSTANCE_ID,
        start=DAY,
        end=DAY,
    )

    assert len(result) == 1
    assert result[0].calories_kcal is None
    assert result[0].tracking_status == "no_data"
