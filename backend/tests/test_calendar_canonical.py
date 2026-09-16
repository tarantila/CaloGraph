from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.analytics import calendar_canonical
from app.analytics.daily_canonical import DailyCanonicalOutcome, DailyCanonicalState
from app.api import analytics as analytics_api
from app.config import Settings
from app.nutrition.models import (
    NutritionDailyProjection,
    NutritionDailyProjectionFact,
    NutritionDailyProjectionLineage,
    NutritionProjectionHead,
)
from app.schemas import DailyPoint
from app.source_priority.models import SourcePriorityPolicy, SourcePriorityRule

USER = SimpleNamespace(
    id=UUID("01234567-89ab-cdef-0123-456789abcdef"),
    timezone="UTC",
)


def _point(local_date: date, calories: str | None = "1900") -> DailyPoint:
    value = Decimal(calories) if calories is not None else None
    return DailyPoint(
        date=local_date,
        calories_kcal=value,
        target_kcal=Decimal("2000"),
        maintenance_kcal=Decimal("2200"),
        deviation_kcal=value - Decimal("2000") if value is not None else None,
        activity_mode="off",
        activity_source_type=None,
        active_energy_kcal=None,
        activity_credit_kcal=Decimal("0"),
        activity_data_status="disabled",
        effective_budget_kcal=Decimal("2000"),
        effective_maintenance_kcal=Decimal("2200"),
        effective_deviation_kcal=value - Decimal("2000") if value is not None else None,
        protein_g=Decimal("100") if value is not None else None,
        carbs_g=None,
        fat_g=None,
        tracking_status="complete" if value is not None else "no_data",
        tracking_score=1 if value is not None else 0,
        tracking_reasons=["Kalorienwert vorhanden"] if value is not None else ["Keine Ernährungsdaten vorhanden"],
    )


def test_calendar_canonical_read_flag_defaults_off() -> None:
    settings = Settings(environment="test")

    assert settings.analytics_calendar_canonical_read_enabled is False


def test_calendar_flag_off_keeps_legacy_and_does_not_invoke_canonical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_points = [_point(date(2026, 9, 10))]
    called = False

    monkeypatch.setattr(analytics_api.settings, "analytics_calendar_canonical_read_enabled", False)
    monkeypatch.setattr(analytics_api, "daily_points", lambda *args: legacy_points)

    def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("canonical reader must not run when disabled")

    monkeypatch.setattr(analytics_api, "run_calendar_canonical_read", fail_if_called, raising=False)

    result = analytics_api.calendar(
        start=date(2026, 9, 10),
        end=date(2026, 9, 10),
        user=USER,
        db=object(),
    )

    assert result == {
        "days": [{**legacy_points[0].model_dump(mode="json"), "classification": "under_budget"}]
    }
    assert called is False


def test_calendar_canonical_success_preserves_calendar_transformation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_points = [_point(date(2026, 9, 10)), _point(date(2026, 9, 11), None)]
    canonical_points = tuple(legacy_points)
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    sequence: list[str] = []

    def legacy(*args):
        sequence.append("legacy")
        return legacy_points

    monkeypatch.setattr(analytics_api.settings, "analytics_calendar_canonical_read_enabled", True)
    monkeypatch.setattr(analytics_api, "daily_points", legacy)

    def run(*args, **kwargs):
        sequence.append("canonical")
        calls.append((args, kwargs))
        return DailyCanonicalOutcome(DailyCanonicalState.MATCH, points=canonical_points)

    monkeypatch.setattr(analytics_api, "run_calendar_canonical_read", run, raising=False)

    result = analytics_api.calendar(
        start=date(2026, 9, 10),
        end=date(2026, 9, 11),
        user=USER,
        db=object(),
    )

    assert result == {
        "days": [
            {**legacy_points[0].model_dump(mode="json"), "classification": "under_budget"},
            {**legacy_points[1].model_dump(mode="json"), "classification": "no_data"},
        ]
    }
    assert len(calls) == 1
    assert sequence == ["legacy", "canonical"]
    assert calls[0][1] == {"legacy_points": legacy_points}
    assert calls[0][0] == (USER.id, date(2026, 9, 10), date(2026, 9, 11))


def test_calendar_canonical_fallback_keeps_complete_legacy_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_points = [_point(date(2026, 9, 10))]

    monkeypatch.setattr(analytics_api.settings, "analytics_calendar_canonical_read_enabled", True)
    monkeypatch.setattr(analytics_api, "daily_points", lambda *args: legacy_points)
    monkeypatch.setattr(
        analytics_api,
        "run_calendar_canonical_read",
        lambda *args, **kwargs: DailyCanonicalOutcome(DailyCanonicalState.FALLBACK),
        raising=False,
    )

    result = analytics_api.calendar(
        start=date(2026, 9, 10),
        end=date(2026, 9, 10),
        user=USER,
        db=object(),
    )

    assert result == {
        "days": [{**legacy_points[0].model_dump(mode="json"), "classification": "under_budget"}]
    }


def test_calendar_canonical_exception_fails_open_to_legacy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_points = [_point(date(2026, 9, 10))]

    monkeypatch.setattr(analytics_api.settings, "analytics_calendar_canonical_read_enabled", True)
    monkeypatch.setattr(analytics_api, "daily_points", lambda *args: legacy_points)
    monkeypatch.setattr(
        analytics_api,
        "run_calendar_canonical_read",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("hidden")),
        raising=False,
    )

    result = analytics_api.calendar(
        start=date(2026, 9, 10),
        end=date(2026, 9, 10),
        user=USER,
        db=object(),
    )

    assert result == {
        "days": [{**legacy_points[0].model_dump(mode="json"), "classification": "under_budget"}]
    }


def test_calendar_canonical_snapshot_race_keeps_initial_legacy_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_points = [_point(date(2026, 9, 10), "1900")]
    newer_canonical_points = (_point(date(2026, 9, 10), "1800"),)

    monkeypatch.setattr(analytics_api.settings, "analytics_calendar_canonical_read_enabled", True)
    monkeypatch.setattr(analytics_api, "daily_points", lambda *args: legacy_points)
    monkeypatch.setattr(
        analytics_api,
        "run_calendar_canonical_read",
        lambda *args, **kwargs: DailyCanonicalOutcome(
            DailyCanonicalState.MATCH,
            points=newer_canonical_points,
        ),
    )

    result = analytics_api.calendar(
        start=date(2026, 9, 10),
        end=date(2026, 9, 10),
        user=USER,
        db=object(),
    )

    assert result == {
        "days": [{**legacy_points[0].model_dump(mode="json"), "classification": "under_budget"}]
    }


def test_calendar_canonical_reader_reuses_d4b_with_bounded_telemetry(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    point = _point(date(2026, 9, 10))

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        return DailyCanonicalOutcome(DailyCanonicalState.MATCH, points=(point,))

    monkeypatch.setattr(calendar_canonical, "run_daily_canonical_read", run)

    with caplog.at_level("INFO", logger=calendar_canonical.LOGGER.name):
        result = calendar_canonical.run_calendar_canonical_read(
            USER.id,
            date(2026, 9, 10),
            date(2026, 9, 10),
        )

    assert result.points == (point,)
    assert calls == [
        (
            (USER.id, date(2026, 9, 10), date(2026, 9, 10), None, None, None),
            {"enabled": True, "max_days": 31, "emit_telemetry": False},
        )
    ]
    payload = caplog.records[-1].msg
    assert '"event":"analytics.calendar.canonical"' in payload
    assert '"version":"d4c.v1"' in payload
    assert '"outcome":"canonical_served"' in payload
    assert str(USER.id) not in payload
    assert "2026-09-10" not in payload


def test_calendar_canonical_reader_falls_back_on_legacy_snapshot_change(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    legacy_point = _point(date(2026, 9, 10), "1900")
    canonical_point = _point(date(2026, 9, 10), "1800")
    monkeypatch.setattr(
        calendar_canonical,
        "run_daily_canonical_read",
        lambda *args, **kwargs: DailyCanonicalOutcome(
            DailyCanonicalState.MATCH,
            points=(canonical_point,),
        ),
    )

    with caplog.at_level("INFO", logger=calendar_canonical.LOGGER.name):
        result = calendar_canonical.run_calendar_canonical_read(
            USER.id,
            legacy_point.date,
            legacy_point.date,
            legacy_points=(legacy_point,),
        )

    assert result.state is DailyCanonicalState.FALLBACK
    assert result.reason == "legacy_snapshot_changed"
    assert result.points is None
    assert '"outcome":"fallback_parity"' in caplog.records[-1].msg


def test_calendar_canonical_reader_maps_seal_fallback_without_leaking_details(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(
        calendar_canonical,
        "run_daily_canonical_read",
        lambda *args, **kwargs: DailyCanonicalOutcome(
            DailyCanonicalState.FALLBACK,
            reason="source_priority_policy_invalid",
        ),
    )

    with caplog.at_level("INFO", logger=calendar_canonical.LOGGER.name):
        result = calendar_canonical.run_calendar_canonical_read(
            USER.id,
            date(2026, 9, 10),
            date(2026, 9, 10),
        )

    assert result.points is None
    payload = caplog.records[-1].msg
    assert '"outcome":"fallback_seal"' in payload
    assert "source_priority_policy_invalid" not in payload


def test_calendar_canonical_serving_preserves_thirty_one_day_range_and_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = date(2026, 8, 31)
    legacy_points = [
        _point(start + timedelta(days=index), None if index in {1, 15} else "1900")
        for index in range(31)
    ]
    monkeypatch.setattr(analytics_api.settings, "analytics_calendar_canonical_read_enabled", True)
    monkeypatch.setattr(analytics_api, "daily_points", lambda *args: legacy_points)
    monkeypatch.setattr(
        analytics_api,
        "run_calendar_canonical_read",
        lambda *args, **kwargs: DailyCanonicalOutcome(
            DailyCanonicalState.MATCH,
            points=tuple(legacy_points),
        ),
    )

    result = analytics_api.calendar(
        start=start,
        end=date(2026, 9, 30),
        user=USER,
        db=object(),
    )

    assert [item["date"] for item in result["days"]] == [
        point.date.isoformat() for point in legacy_points
    ]
    assert len(result["days"]) == 31
    assert result["days"][1]["classification"] == "no_data"
    assert result["days"][15]["classification"] == "no_data"
    assert all("source" not in item for item in result["days"])


def test_calendar_over_thirty_one_days_falls_back_to_legacy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = date(2026, 9, 1)
    legacy_points = [_point(start + timedelta(days=index)) for index in range(32)]
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    monkeypatch.setattr(analytics_api.settings, "analytics_calendar_canonical_read_enabled", True)
    monkeypatch.setattr(analytics_api, "daily_points", lambda *args: legacy_points)

    def fallback(*args, **kwargs):
        calls.append((args, kwargs))
        return DailyCanonicalOutcome(DailyCanonicalState.SKIPPED, reason="range_too_large")

    monkeypatch.setattr(analytics_api, "run_calendar_canonical_read", fallback)

    result = analytics_api.calendar(
        start=start,
        end=start + timedelta(days=31),
        user=USER,
        db=object(),
    )

    assert result == {
        "days": [
            {**point.model_dump(mode="json"), "classification": "under_budget"}
            for point in legacy_points
        ]
    }
    assert calls == [
        (
            (USER.id, start, start + timedelta(days=31)),
            {"legacy_points": legacy_points},
        )
    ]


def test_calendar_get_does_not_write_projection_or_source_priority_rows(
    db,
    user,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    models = (
        NutritionDailyProjection,
        NutritionDailyProjectionFact,
        NutritionDailyProjectionLineage,
        NutritionProjectionHead,
        SourcePriorityPolicy,
        SourcePriorityRule,
    )
    before = tuple(db.query(model).count() for model in models)
    monkeypatch.setattr(analytics_api.settings, "analytics_calendar_canonical_read_enabled", True)
    monkeypatch.setattr(
        analytics_api,
        "run_calendar_canonical_read",
        lambda *args, **kwargs: DailyCanonicalOutcome(DailyCanonicalState.FALLBACK),
    )

    analytics_api.calendar(
        start=date(2026, 9, 10),
        end=date(2026, 9, 10),
        user=user,
        db=db,
    )

    after = tuple(db.query(model).count() for model in models)
    assert after == before
