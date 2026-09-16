import logging
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.analytics import weekdays_canonical
from app.analytics.canonical_serving import (
    CanonicalServingDetail,
    CanonicalServingEndpoint,
    CanonicalServingOutcome,
    CanonicalServingResult,
    CanonicalServingSource,
)
from app.analytics.daily_canonical import (
    DailyCanonicalOutcome,
    DailyCanonicalReason,
    DailyCanonicalState,
)
from app.api import analytics as analytics_api
from app.config import Settings
from app.schemas import DailyPoint

USER = SimpleNamespace(
    id=UUID("01234567-89ab-cdef-0123-456789abcdef"),
    timezone="UTC",
    week_starts_on=0,
)


def _point(
    local_date: date,
    calories: str | None,
    *,
    deviation: str | None = "-100",
    effective_deviation: str | None = "-100",
    protein: str | None = "100",
    tracking_status: str = "complete",
) -> DailyPoint:
    value = Decimal(calories) if calories is not None else None
    target = Decimal("2000") if value is not None else None
    return DailyPoint(
        date=local_date,
        calories_kcal=value,
        target_kcal=target,
        maintenance_kcal=Decimal("2200") if target is not None else None,
        deviation_kcal=(
            Decimal(deviation) if deviation is not None and target is not None else None
        ),
        activity_mode="full" if target is not None else None,
        activity_source_type="apple_health_xml" if target is not None else None,
        active_energy_kcal=Decimal("300") if target is not None else None,
        activity_credit_kcal=Decimal("300") if target is not None else Decimal("0"),
        activity_data_status="credited" if target is not None else "disabled",
        effective_budget_kcal=Decimal("2300") if target is not None else None,
        effective_maintenance_kcal=Decimal("2500") if target is not None else None,
        effective_deviation_kcal=(
            Decimal(effective_deviation)
            if effective_deviation is not None and target is not None
            else None
        ),
        protein_g=Decimal(protein) if protein is not None else None,
        carbs_g=None,
        fat_g=None,
        tracking_status=tracking_status,
        tracking_score=1 if value is not None else 0,
        tracking_reasons=["Kalorienwert vorhanden"] if value is not None else ["Keine Ernährungsdaten vorhanden"],
    )


def _flatten(result: dict[str, object]) -> list[dict[str, object]]:
    return result["weekdays"]  # type: ignore[return-value]


def test_weekdays_default_range_is_180_days(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[date, date]] = []
    monkeypatch.setattr(
        analytics_api,
        "daily_points",
        lambda db, user, start, end: captured.append((start, end)) or [],
    )

    result = analytics_api.weekdays(user=USER, db=object())

    assert len(_flatten(result)) == 7
    assert (captured[0][1] - captured[0][0]).days == 179


def test_weekdays_period_all_keeps_existing_achievement_hook_and_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []
    point = _point(date(2026, 9, 7), "1900")
    monkeypatch.setattr(
        analytics_api,
        "_unlock_big_picture_if_requested",
        lambda db, user, period: calls.append(period),
    )
    monkeypatch.setattr(analytics_api, "daily_points", lambda *args: [point])

    result = analytics_api.weekdays(period="all", user=USER, db=object())

    assert calls == ["all"]
    assert _flatten(result)[0] == {
        "weekday": 0,
        "label": "Montag",
        "count": 1,
        "mean_kcal": 1900.0,
        "median_kcal": 1900.0,
        "p25_kcal": 1900.0,
        "p75_kcal": 1900.0,
        "mean_deviation_kcal": -100.0,
        "mean_effective_deviation_kcal": -100.0,
        "mean_protein_g": 100.0,
    }


def test_weekdays_characterization_preserves_denominator_quantiles_and_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = date(2026, 9, 7)
    points = [
        _point(start, "1000"),
        _point(start + timedelta(days=7), "2000"),
        _point(start + timedelta(days=1), None),
        _point(start + timedelta(days=2), "1500", tracking_status="incomplete"),
        _point(start + timedelta(days=3), "1750", deviation=None, effective_deviation=None, protein=None),
    ]
    monkeypatch.setattr(analytics_api, "daily_points", lambda *args: points)

    result = analytics_api.weekdays(
        start=start,
        end=start + timedelta(days=10),
        user=USER,
        db=object(),
    )
    output = _flatten(result)

    assert [item["weekday"] for item in output] == list(range(7))
    assert [item["label"] for item in output] == [
        "Montag",
        "Dienstag",
        "Mittwoch",
        "Donnerstag",
        "Freitag",
        "Samstag",
        "Sonntag",
    ]
    assert output[0]["count"] == 2
    assert output[0]["mean_kcal"] == 1500.0
    assert output[0]["median_kcal"] == 1500.0
    assert output[0]["p25_kcal"] == 1250.0
    assert output[0]["p75_kcal"] == 1750.0
    assert output[1]["count"] == 0
    assert output[1]["mean_kcal"] is None
    assert output[2]["count"] == 1
    assert output[2]["mean_kcal"] == 1500.0
    assert output[2]["mean_deviation_kcal"] == -100.0
    assert output[3]["count"] == 1
    assert output[3]["mean_kcal"] == 1750.0
    assert output[3]["mean_deviation_kcal"] is None
    assert output[3]["mean_effective_deviation_kcal"] is None
    assert output[3]["mean_protein_g"] is None


def test_weekdays_public_wire_contract_and_decimal_none_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        analytics_api,
        "daily_points",
        lambda *args: [_point(date(2026, 9, 7), "1234.567")],
    )

    result = analytics_api.weekdays(
        start=date(2026, 9, 7),
        end=date(2026, 9, 7),
        user=USER,
        db=object(),
    )

    assert set(_flatten(result)[0]) == {
        "weekday",
        "label",
        "count",
        "mean_kcal",
        "median_kcal",
        "p25_kcal",
        "p75_kcal",
        "mean_deviation_kcal",
        "mean_effective_deviation_kcal",
        "mean_protein_g",
    }
    assert _flatten(result)[0]["mean_kcal"] == 1234.567
    assert _flatten(result)[0]["count"] == 1


@pytest.mark.parametrize("tracking_status", ["incomplete", "probably_incomplete"])
def test_weekdays_denominator_includes_calorie_points_regardless_of_tracking_status(
    monkeypatch: pytest.MonkeyPatch,
    tracking_status: str,
) -> None:
    point = _point(date(2026, 9, 7), "1900", tracking_status=tracking_status)
    monkeypatch.setattr(analytics_api, "daily_points", lambda *args: [point])

    result = analytics_api.weekdays(
        start=date(2026, 9, 7),
        end=date(2026, 9, 7),
        user=USER,
        db=object(),
    )

    assert _flatten(result)[0]["count"] == 1
    assert _flatten(result)[0]["p25_kcal"] == 1900.0
    assert _flatten(result)[0]["p75_kcal"] == 1900.0

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


def test_weekdays_canonical_flag_defaults_off() -> None:
    assert Settings(_env_file=None, environment="test").analytics_weekdays_canonical_read_enabled is False


def test_weekdays_flag_off_keeps_legacy_without_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    point = _point(date(2026, 9, 7), "1900")
    monkeypatch.setattr(analytics_api.settings, "analytics_weekdays_canonical_read_enabled", False)
    monkeypatch.setattr(analytics_api, "daily_points", lambda *args: [point])
    called = False

    def fail_if_called(*args: object, **kwargs: object) -> None:
        nonlocal called
        called = True
        raise AssertionError("disabled Weekdays Canonical read must not run")

    monkeypatch.setattr(analytics_api, "run_weekdays_canonical_read", fail_if_called, raising=False)
    result = analytics_api.weekdays(
        start=date(2026, 9, 7),
        end=date(2026, 9, 7),
        user=USER,
        db=object(),
    )

    assert called is False
    assert _flatten(result)[0]["count"] == 1


def test_weekdays_eligible_request_uses_canonical_points(monkeypatch: pytest.MonkeyPatch) -> None:
    start = date(2026, 9, 7)
    legacy = _point(start, "1900")
    canonical = _point(start, "1800")
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(analytics_api.settings, "analytics_weekdays_canonical_read_enabled", True)
    monkeypatch.setattr(analytics_api, "daily_points", lambda *args: [legacy])

    def run(*args: object, **kwargs: object) -> DailyCanonicalOutcome:
        calls.append(args)
        assert kwargs["legacy_points"] == (legacy,)
        return DailyCanonicalOutcome(DailyCanonicalState.MATCH, points=(canonical,))

    monkeypatch.setattr(analytics_api, "run_weekdays_canonical_read", run, raising=False)
    result = analytics_api.weekdays(start=start, end=start, user=USER, db=object())

    assert len(calls) == 1
    assert _flatten(result)[0]["mean_kcal"] == 1800.0


@pytest.mark.parametrize(
    ("period", "days"),
    [("all", 1), (None, 32)],
)
def test_weekdays_ineligible_requests_do_not_attempt_canonical(
    monkeypatch: pytest.MonkeyPatch,
    period: str | None,
    days: int,
) -> None:
    start = date(2026, 9, 7)
    point = _point(start, "1900")
    called = False
    monkeypatch.setattr(analytics_api.settings, "analytics_weekdays_canonical_read_enabled", True)
    monkeypatch.setattr(analytics_api, "daily_points", lambda *args: [point])
    monkeypatch.setattr(analytics_api, "_unlock_big_picture_if_requested", lambda *args: None)

    def fail_if_called(*args: object, **kwargs: object) -> None:
        nonlocal called
        called = True
        raise AssertionError("ineligible Weekdays request must not attempt Canonical")

    monkeypatch.setattr(analytics_api, "run_weekdays_canonical_read", fail_if_called, raising=False)
    result = analytics_api.weekdays(
        start=start,
        end=start + timedelta(days=days - 1),
        period=period,
        user=USER,
        db=object(),
    )

    assert called is False
    assert _flatten(result)[0]["count"] == 1


def test_weekdays_exact_match_preserves_public_response(monkeypatch: pytest.MonkeyPatch) -> None:
    start = date(2026, 9, 7)
    points = [
        _point(start, "1000"),
        _point(start + timedelta(days=1), None),
        _point(start + timedelta(days=2), "1500", tracking_status="incomplete"),
        _point(start + timedelta(days=3), "1750", deviation=None, effective_deviation=None, protein=None),
    ]
    monkeypatch.setattr(analytics_api, "daily_points", lambda *args: points)
    monkeypatch.setattr(analytics_api.settings, "analytics_weekdays_canonical_read_enabled", False)
    legacy = analytics_api.weekdays(start=start, end=start + timedelta(days=3), user=USER, db=object())
    monkeypatch.setattr(analytics_api.settings, "analytics_weekdays_canonical_read_enabled", True)
    monkeypatch.setattr(
        analytics_api,
        "run_weekdays_canonical_read",
        lambda *args, **kwargs: DailyCanonicalOutcome(
            DailyCanonicalState.MATCH,
            points=tuple(points),
        ),
    )

    canonical = analytics_api.weekdays(start=start, end=start + timedelta(days=3), user=USER, db=object())

    assert canonical == legacy


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        (DailyCanonicalState.NOT_COMPARABLE, DailyCanonicalReason.PROJECTION_NOT_READY),
        (DailyCanonicalState.FALLBACK, DailyCanonicalReason.LEGACY_SNAPSHOT_CHANGED),
        (DailyCanonicalState.FALLBACK, DailyCanonicalReason.SOURCE_PRIORITY_POLICY_INVALID),
    ],
)
def test_weekdays_fallback_keeps_legacy_points(
    monkeypatch: pytest.MonkeyPatch,
    state: DailyCanonicalState,
    reason: DailyCanonicalReason,
) -> None:
    point = _point(date(2026, 9, 7), "1900")
    monkeypatch.setattr(analytics_api.settings, "analytics_weekdays_canonical_read_enabled", True)
    monkeypatch.setattr(analytics_api, "daily_points", lambda *args: [point])
    monkeypatch.setattr(
        analytics_api,
        "run_weekdays_canonical_read",
        lambda *args, **kwargs: DailyCanonicalOutcome(state, reason=reason),
    )

    result = analytics_api.weekdays(
        start=date(2026, 9, 7),
        end=date(2026, 9, 7),
        user=USER,
        db=object(),
    )

    assert _flatten(result)[0]["mean_kcal"] == 1900.0


@pytest.mark.parametrize(
    ("outcome", "detail", "state", "reason"),
    [
        (
            CanonicalServingOutcome.CANONICAL_SERVED,
            None,
            DailyCanonicalState.MATCH,
            None,
        ),
        (
            CanonicalServingOutcome.FALLBACK_NOT_READY,
            CanonicalServingDetail.PROJECTION_NOT_READY,
            DailyCanonicalState.NOT_COMPARABLE,
            DailyCanonicalReason.PROJECTION_NOT_READY,
        ),
        (
            CanonicalServingOutcome.FALLBACK_PARITY,
            CanonicalServingDetail.LEGACY_SNAPSHOT_CHANGED,
            DailyCanonicalState.FALLBACK,
            DailyCanonicalReason.LEGACY_SNAPSHOT_CHANGED,
        ),
        (
            CanonicalServingOutcome.FALLBACK_SEAL,
            CanonicalServingDetail.SOURCE_PRIORITY_POLICY_INVALID,
            DailyCanonicalState.FALLBACK,
            DailyCanonicalReason.SOURCE_PRIORITY_POLICY_INVALID,
        ),
    ],
)
def test_weekdays_adapter_maps_shared_serving_result(
    monkeypatch: pytest.MonkeyPatch,
    outcome: CanonicalServingOutcome,
    detail: CanonicalServingDetail | None,
    state: DailyCanonicalState,
    reason: DailyCanonicalReason | None,
) -> None:
    points = (_point(date(2026, 9, 7), "1900"),)
    monkeypatch.setattr(
        weekdays_canonical,
        "serve_canonical",
        lambda request: _serving_result(points, outcome, detail=detail),
    )

    result = weekdays_canonical.run_weekdays_canonical_read(
        USER.id,
        date(2026, 9, 7),
        date(2026, 9, 7),
        legacy_points=points,
    )

    assert result.state is state
    assert result.reason is reason
    assert result.points == (points if outcome is CanonicalServingOutcome.CANONICAL_SERVED else None)


def test_weekdays_adapter_enforces_user_endpoint_and_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    points = (_point(date(2026, 9, 7), "1900"),)
    requests: list[object] = []
    monkeypatch.setattr(
        weekdays_canonical,
        "serve_canonical",
        lambda request: (
            requests.append(request)
            or _serving_result(points, CanonicalServingOutcome.CANONICAL_SERVED)
        ),
    )

    result = weekdays_canonical.run_weekdays_canonical_read(
        USER.id,
        date(2026, 9, 7),
        date(2026, 9, 7),
        legacy_points=points,
    )

    request = requests[0]
    assert request.user_id is USER.id
    assert request.endpoint is CanonicalServingEndpoint.WEEKDAYS
    assert request.legacy_points == points
    assert result.points == points


def test_weekdays_telemetry_is_bounded_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    points = (_point(date(2026, 9, 7), "1900"),)
    monkeypatch.setattr(
        weekdays_canonical,
        "serve_canonical",
        lambda request: _serving_result(
            points,
            CanonicalServingOutcome.FALLBACK_ERROR,
            exception_class="RuntimeError",
        ),
    )

    with caplog.at_level(logging.INFO, logger=weekdays_canonical.LOGGER.name):
        weekdays_canonical.run_weekdays_canonical_read(
            USER.id,
            date(2026, 9, 7),
            date(2026, 9, 7),
            legacy_points=points,
        )

    records = [record.message for record in caplog.records if record.name == weekdays_canonical.LOGGER.name]
    assert len(records) == 1
    message = records[0]
    assert "analytics.weekdays.canonical" in message
    assert "d4f.v1" in message
    assert "RuntimeError" in message
    assert str(USER.id) not in message
    assert "2026-09-07" not in message
    assert "1900" not in message


def test_weekdays_canonical_flag_is_wired_to_templates_and_compose() -> None:
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
        assert "ANALYTICS_WEEKDAYS_CANONICAL_READ_ENABLED" in path.read_text()
