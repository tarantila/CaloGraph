import json
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.analytics import weekly_canonical
from app.analytics.canonical_serving import (
    CanonicalServingDetail,
    CanonicalServingOutcome,
    CanonicalServingResult,
    CanonicalServingSource,
)
from app.analytics.daily_canonical import DailyCanonicalOutcome, DailyCanonicalState
from app.api import analytics as analytics_api
from app.config import Settings
from app.schemas import DailyPoint

USER = SimpleNamespace(
    id=UUID("01234567-89ab-cdef-0123-456789abcdef"),
    timezone="UTC",
    week_starts_on=0,
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
        tracking_reasons=(
            ["Kalorienwert vorhanden"] if value is not None else ["Keine Ernährungsdaten vorhanden"]
        ),
    )


def _points(start: date, days: int) -> list[DailyPoint]:
    return [_point(start + timedelta(days=offset)) for offset in range(days)]


def test_weekly_canonical_flag_defaults_off() -> None:
    configured = Settings(_env_file=None, environment="test")

    assert configured.analytics_weekly_canonical_read_enabled is False


def test_weekly_31_day_request_can_serve_selected_canonical_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = date(2026, 9, 1)
    legacy_points = _points(start, 31)
    canonical_points = [_point(start + timedelta(days=offset), "1800") for offset in range(31)]
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    monkeypatch.setattr(analytics_api.settings, "analytics_weekly_canonical_read_enabled", True)
    monkeypatch.setattr(analytics_api, "daily_points", lambda *args: legacy_points)

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        return DailyCanonicalOutcome(DailyCanonicalState.MATCH, points=tuple(canonical_points))

    monkeypatch.setattr(analytics_api, "run_weekly_canonical_read", run, raising=False)

    result = analytics_api.weekly(start=start, end=start + timedelta(days=30), user=USER, db=object())

    assert len(calls) == 1
    assert result["weeks"]
    assert result["weeks"][0]["days"][0] == canonical_points[0].model_dump(mode="json")


def test_weekly_default_90_day_request_stays_legacy_without_canonical_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = date(2026, 9, 1)
    legacy_points = _points(start, 90)
    called = False

    monkeypatch.setattr(analytics_api.settings, "analytics_weekly_canonical_read_enabled", True)
    monkeypatch.setattr(analytics_api, "daily_points", lambda *args: legacy_points)

    def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("90-day Weekly request must not attempt Canonical")

    monkeypatch.setattr(analytics_api, "run_weekly_canonical_read", fail_if_called, raising=False)

    result = analytics_api.weekly(user=USER, db=object())

    assert [
        day
        for week in result["weeks"]
        for day in week["days"]
    ] == [point.model_dump(mode="json") for point in legacy_points]
    assert called is False




def _serving_result(
    points: tuple[DailyPoint, ...],
    outcome: CanonicalServingOutcome,
    *,
    detail: CanonicalServingDetail | None = None,
    exception_class: str | None = None,
) -> CanonicalServingResult:
    return CanonicalServingResult(
        selected_points=points,
        source=(
            CanonicalServingSource.CANONICAL_SELECTED
            if outcome is CanonicalServingOutcome.CANONICAL_SERVED
            else CanonicalServingSource.LEGACY_SELECTED
        ),
        outcome=outcome,
        detail=detail,
        exception_class=exception_class,
    )


def test_weekly_flag_off_returns_exact_legacy_response_without_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = date(2026, 9, 1)
    legacy_points = _points(start, 3)
    called = False

    monkeypatch.setattr(analytics_api.settings, "analytics_weekly_canonical_read_enabled", False)
    monkeypatch.setattr(analytics_api, "daily_points", lambda *args: legacy_points)

    def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("disabled Weekly Canonical read must not run")

    monkeypatch.setattr(analytics_api, "run_weekly_canonical_read", fail_if_called)

    result = analytics_api.weekly(
        start=start,
        end=start + timedelta(days=2),
        user=USER,
        db=SimpleNamespace(),
    )

    assert called is False
    assert [
        day
        for week in result["weeks"]
        for day in week["days"]
    ] == [point.model_dump(mode="json") for point in legacy_points]


def test_weekly_canonical_exact_match_preserves_public_aggregation_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = date(2026, 9, 5)
    legacy_points = _points(start, 3)
    targetless = _point(start + timedelta(days=1), None).model_copy(
        update={
            "target_kcal": None,
            "maintenance_kcal": None,
            "deviation_kcal": None,
            "activity_mode": None,
            "effective_budget_kcal": None,
            "effective_maintenance_kcal": None,
            "effective_deviation_kcal": None,
        }
    )
    legacy_points[1] = targetless
    legacy_points[2] = legacy_points[2].model_copy(
        update={
            "activity_mode": "full",
            "activity_source_type": "apple_health_xml",
            "active_energy_kcal": Decimal("300"),
            "activity_credit_kcal": Decimal("300"),
            "effective_budget_kcal": Decimal("2300"),
            "effective_maintenance_kcal": Decimal("2500"),
            "effective_deviation_kcal": Decimal("-400"),
        }
    )
    monkeypatch.setattr(analytics_api, "daily_points", lambda *args: legacy_points)

    monkeypatch.setattr(analytics_api.settings, "analytics_weekly_canonical_read_enabled", False)
    legacy_result = analytics_api.weekly(start=start, end=start + timedelta(days=2), user=USER, db=object())

    monkeypatch.setattr(analytics_api.settings, "analytics_weekly_canonical_read_enabled", True)
    monkeypatch.setattr(
        analytics_api,
        "run_weekly_canonical_read",
        lambda *args, **kwargs: DailyCanonicalOutcome(
            DailyCanonicalState.MATCH,
            points=tuple(legacy_points),
        ),
    )
    canonical_result = analytics_api.weekly(
        start=start,
        end=start + timedelta(days=2),
        user=USER,
        db=object(),
    )

    assert canonical_result == legacy_result


def test_weekly_canonical_exact_match_preserves_alternative_week_start_and_partials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = SimpleNamespace(id=USER.id, timezone="UTC", week_starts_on=6)
    start = date(2026, 9, 5)
    legacy_points = _points(start, 3)
    monkeypatch.setattr(analytics_api.settings, "analytics_weekly_canonical_read_enabled", True)
    monkeypatch.setattr(analytics_api, "daily_points", lambda *args: legacy_points)
    monkeypatch.setattr(
        analytics_api,
        "run_weekly_canonical_read",
        lambda *args, **kwargs: DailyCanonicalOutcome(
            DailyCanonicalState.MATCH,
            points=tuple(legacy_points),
        ),
    )

    result = analytics_api.weekly(
        start=start,
        end=start + timedelta(days=2),
        user=user,
        db=object(),
    )

    assert [week["week_start"] for week in result["weeks"]] == ["2026-08-30", "2026-09-06"]
    assert [len(week["days"]) for week in result["weeks"]] == [1, 2]


@pytest.mark.parametrize(
    ("outcome", "detail", "state", "reason"),
    [
        (
            CanonicalServingOutcome.FALLBACK_NOT_READY,
            CanonicalServingDetail.PROJECTION_NOT_READY,
            DailyCanonicalState.NOT_COMPARABLE,
            "projection_not_ready",
        ),
        (
            CanonicalServingOutcome.FALLBACK_PARITY,
            CanonicalServingDetail.LEGACY_SNAPSHOT_CHANGED,
            DailyCanonicalState.FALLBACK,
            "legacy_snapshot_changed",
        ),
        (
            CanonicalServingOutcome.FALLBACK_SEAL,
            CanonicalServingDetail.SOURCE_PRIORITY_POLICY_INVALID,
            DailyCanonicalState.FALLBACK,
            "source_priority_policy_invalid",
        ),
    ],
)
def test_weekly_canonical_fallback_outcomes_keep_complete_legacy_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    outcome: CanonicalServingOutcome,
    detail: CanonicalServingDetail,
    state: DailyCanonicalState,
    reason: str,
) -> None:
    start = date(2026, 9, 1)
    legacy_points = tuple(_points(start, 3))
    monkeypatch.setattr(
        weekly_canonical,
        "serve_canonical",
        lambda request: _serving_result(legacy_points, outcome, detail=detail),
    )

    result = weekly_canonical.run_weekly_canonical_read(
        USER.id,
        start,
        start + timedelta(days=2),
        legacy_points=legacy_points,
    )

    assert result.state is state
    assert result.reason == reason
    assert result.points is None


def test_weekly_canonical_exception_fails_open_and_emits_redacted_single_event(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    start = date(2026, 9, 1)
    legacy_points = tuple(_points(start, 1))
    monkeypatch.setattr(
        weekly_canonical,
        "serve_canonical",
        lambda request: _serving_result(
            legacy_points,
            CanonicalServingOutcome.FALLBACK_ERROR,
            detail=CanonicalServingDetail.ERROR,
            exception_class="RuntimeError",
        ),
    )

    with caplog.at_level("INFO", logger=weekly_canonical.LOGGER.name):
        result = weekly_canonical.run_weekly_canonical_read(
            USER.id,
            start,
            start,
            legacy_points=legacy_points,
        )

    events = [json.loads(record.getMessage()) for record in caplog.records]
    assert result.state is DailyCanonicalState.ERROR
    assert result.points is None
    assert len(events) == 1
    assert events[0] == {
        "duration_bucket": events[0]["duration_bucket"],
        "event": "analytics.weekly.canonical",
        "exception_class": "RuntimeError",
        "outcome": "error",
        "range_bucket": "1",
        "version": "d4e.v1",
    }
    payload = caplog.records[0].getMessage()
    assert str(USER.id) not in payload
    assert "2026-09-01" not in payload
    assert "1900" not in payload


def test_weekly_adapter_passes_user_scope_and_immutable_legacy_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = date(2026, 9, 1)
    legacy_points = tuple(_points(start, 1))
    captured = []

    def serve(request):
        captured.append(request)
        return _serving_result(legacy_points, CanonicalServingOutcome.CANONICAL_SERVED)

    monkeypatch.setattr(weekly_canonical, "serve_canonical", serve)

    result = weekly_canonical.run_weekly_canonical_read(
        USER.id,
        start,
        start,
        legacy_points=legacy_points,
    )

    assert result.points == legacy_points
    assert captured[0].user_id == USER.id
    assert captured[0].endpoint.value == "weekly"
    assert captured[0].legacy_points == legacy_points


def test_weekly_32_day_request_stays_legacy_without_canonical_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = date(2026, 9, 1)
    legacy_points = _points(start, 32)
    called = False

    monkeypatch.setattr(analytics_api.settings, "analytics_weekly_canonical_read_enabled", True)
    monkeypatch.setattr(analytics_api, "daily_points", lambda *args: legacy_points)

    def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("32-day Weekly request must not attempt Canonical")

    monkeypatch.setattr(analytics_api, "run_weekly_canonical_read", fail_if_called, raising=False)

    result = analytics_api.weekly(
        start=start,
        end=start + timedelta(days=31),
        user=USER,
        db=object(),
    )

    assert [
        day
        for week in result["weeks"]
        for day in week["days"]
    ] == [point.model_dump(mode="json") for point in legacy_points]
    assert called is False
def test_weekly_canonical_fallback_never_mixes_individual_days(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = date(2026, 9, 1)
    legacy_points = _points(start, 3)
    monkeypatch.setattr(analytics_api.settings, "analytics_weekly_canonical_read_enabled", True)
    monkeypatch.setattr(analytics_api, "daily_points", lambda *args: legacy_points)
    monkeypatch.setattr(
        analytics_api,
        "run_weekly_canonical_read",
        lambda *args, **kwargs: DailyCanonicalOutcome(
            DailyCanonicalState.FALLBACK,
            reason="legacy_snapshot_changed",
        ),
    )

    result = analytics_api.weekly(
        start=start,
        end=start + timedelta(days=2),
        user=USER,
        db=object(),
    )

    assert [
        day
        for week in result["weeks"]
        for day in week["days"]
    ] == [point.model_dump(mode="json") for point in legacy_points]


def test_weekly_canonical_flag_is_wired_to_templates_and_compose() -> None:
    from pathlib import Path
    repository_roots = (
        Path(__file__).resolve().parents[2],
        Path("/workspace"),
        Path("/app"),
    )
    repository_root = next(
        root for root in repository_roots if (root / ".env.example").exists()
    )
    for path in (
        repository_root / ".env.example",
        repository_root / ".env.production.example",
        repository_root / "docker-compose.yml",
    ):
        assert "ANALYTICS_WEEKLY_CANONICAL_READ_ENABLED" in path.read_text()
