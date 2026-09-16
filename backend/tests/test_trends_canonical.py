from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest

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
)
BALANCE = {
    "tracked_days": 4,
    "within_budget_days": 2,
    "over_budget_days": 1,
    "over_maintenance_days": 1,
    "unclassified_budget_days": 0,
}


def _point(
    local_date: date,
    calories: str | None = "1900",
    *,
    tracking_status: str = "complete",
) -> DailyPoint:
    value = Decimal(calories) if calories is not None else None
    target = Decimal("2000") if value is not None else None
    return DailyPoint(
        date=local_date,
        calories_kcal=value,
        target_kcal=target,
        maintenance_kcal=Decimal("2200") if target is not None else None,
        deviation_kcal=value - target if value is not None and target is not None else None,
        activity_mode="off",
        activity_source_type=None,
        active_energy_kcal=None,
        activity_credit_kcal=Decimal("0"),
        activity_data_status="disabled",
        effective_budget_kcal=target,
        effective_maintenance_kcal=Decimal("2200") if target is not None else None,
        effective_deviation_kcal=value - target if value is not None and target is not None else None,
        protein_g=Decimal("100") if value is not None else None,
        carbs_g=None,
        fat_g=None,
        tracking_status=tracking_status,
        tracking_score=1 if value is not None else 0,
        tracking_reasons=["Kalorienwert vorhanden"] if value is not None else ["Keine Ernährungsdaten vorhanden"],
    )


def _prepare(monkeypatch: pytest.MonkeyPatch, points: list[DailyPoint]) -> None:
    monkeypatch.setattr(analytics_api, "daily_points", lambda *args: points)
    monkeypatch.setattr(analytics_api, "_historical_budget_balance", lambda *args: BALANCE)
    monkeypatch.setattr(analytics_api, "_unlock_big_picture_if_requested", lambda *args: None)


def test_trends_default_request_reads_90_calendar_days(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[date, date]] = []

    def read_points(_db: object, _user: object, start: date, end: date) -> list[DailyPoint]:
        captured.append((start, end))
        return []

    monkeypatch.setattr(analytics_api, "daily_points", read_points)
    monkeypatch.setattr(analytics_api, "_historical_budget_balance", lambda *args: BALANCE)
    monkeypatch.setattr(analytics_api, "_unlock_big_picture_if_requested", lambda *args: None)

    result = analytics_api.trends(user=USER, db=object())

    assert result["points"] == []
    assert (captured[0][1] - captured[0][0]).days + 1 == 90


def test_trends_explicit_range_is_passed_without_lookback(monkeypatch: pytest.MonkeyPatch) -> None:
    start = date(2026, 9, 1)
    end = date(2026, 9, 7)
    captured: list[tuple[date, date]] = []

    def read_points(_db: object, _user: object, actual_start: date, actual_end: date) -> list[DailyPoint]:
        captured.append((actual_start, actual_end))
        return [_point(start)]

    monkeypatch.setattr(analytics_api, "daily_points", read_points)
    monkeypatch.setattr(analytics_api, "_historical_budget_balance", lambda *args: BALANCE)
    monkeypatch.setattr(analytics_api, "_unlock_big_picture_if_requested", lambda *args: None)

    analytics_api.trends(start=start, end=end, user=USER, db=object())

    assert captured == [(start, end)]


def test_trends_period_all_keeps_hook_and_legacy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    points = [_point(date(2026, 9, 1))]
    _prepare(monkeypatch, points)
    monkeypatch.setattr(
        analytics_api,
        "_unlock_big_picture_if_requested",
        lambda _db, _user, period: calls.append(period),
    )

    result = analytics_api.trends(
        start=date(2026, 9, 1),
        end=date(2026, 9, 1),
        period="all",
        user=USER,
        db=object(),
    )

    assert calls == ["all"]
    assert result["points"][0]["calories_kcal"] == "1900"


def test_trends_include_incomplete_false_preserves_status_and_excludes_average(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = date(2026, 9, 1)
    points = [_point(start, "100"), _point(start + timedelta(days=1), "600", tracking_status="incomplete")]
    _prepare(monkeypatch, points)

    result = analytics_api.trends(
        start=start,
        end=start + timedelta(days=1),
        include_incomplete=False,
        user=USER,
        db=object(),
    )

    assert result["incomplete_included"] is False
    assert result["points"][1]["tracking_status"] == "incomplete"
    assert result["points"][1]["average_7d"] == 100.0
    assert tuple(point.tracking_status for point in points) == ("complete", "incomplete")


def test_trends_include_incomplete_true_changes_output_only_and_restores_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = date(2026, 9, 1)
    points = [_point(start, "100"), _point(start + timedelta(days=1), "600", tracking_status="incomplete")]
    _prepare(monkeypatch, points)

    result = analytics_api.trends(
        start=start,
        end=start + timedelta(days=1),
        include_incomplete=True,
        user=USER,
        db=object(),
    )

    assert result["incomplete_included"] is True
    assert [item["tracking_status"] for item in result["points"]] == ["complete", "incomplete"]
    assert result["points"][1]["average_7d"] == 350.0
    assert tuple(point.tracking_status for point in points) == ("complete", "incomplete")


def test_trends_uses_7_14_and_28_day_calendar_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    start = date(2026, 1, 1)
    points = [_point(start + timedelta(days=index), str(index + 1)) for index in range(28)]
    _prepare(monkeypatch, points)

    result = analytics_api.trends(start=start, end=start + timedelta(days=27), user=USER, db=object())
    last = result["points"][-1]

    assert last["average_7d"] == 25.0
    assert last["average_14d"] == 21.5
    assert last["average_28d"] == 14.5


def test_trends_moving_averages_start_with_available_points(monkeypatch: pytest.MonkeyPatch) -> None:
    start = date(2026, 1, 1)
    points = [_point(start + timedelta(days=index), str(index + 1)) for index in range(3)]
    _prepare(monkeypatch, points)

    result = analytics_api.trends(start=start, end=start + timedelta(days=2), user=USER, db=object())

    assert result["points"][0]["average_7d"] == 1.0
    assert result["points"][1]["average_14d"] == 1.5
    assert result["points"][2]["average_28d"] == 2.0


def test_trends_moving_averages_ignore_no_data_and_incomplete_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = date(2026, 1, 1)
    points = [
        _point(start, "100"),
        _point(start + timedelta(days=1), None),
        _point(start + timedelta(days=2), "600", tracking_status="incomplete"),
        _point(start + timedelta(days=3), "300"),
    ]
    _prepare(monkeypatch, points)

    result = analytics_api.trends(
        start=start,
        end=start + timedelta(days=3),
        include_incomplete=False,
        user=USER,
        db=object(),
    )

    assert result["points"][-1]["average_7d"] == 200.0
    assert result["points"][-1]["average_14d"] == 200.0
    assert result["points"][-1]["average_28d"] == 200.0


def test_trends_preserves_historical_balance_as_independent_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare(monkeypatch, [_point(date(2026, 9, 1))])

    result = analytics_api.trends(
        start=date(2026, 9, 1),
        end=date(2026, 9, 1),
        user=USER,
        db=object(),
    )

    assert result["budget_balance"] == BALANCE


def test_trends_public_wire_contract_and_decimal_serialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare(monkeypatch, [_point(date(2026, 9, 1), "1234.567")])

    result = analytics_api.trends(
        start=date(2026, 9, 1),
        end=date(2026, 9, 1),
        user=USER,
        db=object(),
    )

    assert set(result) == {"points", "incomplete_included", "budget_balance"}
    assert result["points"][0]["calories_kcal"] == "1234.567"
    assert result["points"][0]["average_7d"] == 1234.567


@pytest.mark.parametrize("days", [1, 7, 28, 31, 32])
def test_trends_explicit_range_preserves_order_and_cardinality(
    monkeypatch: pytest.MonkeyPatch,
    days: int,
) -> None:
    start = date(2026, 9, 1)
    points = [_point(start + timedelta(days=index), str(index + 1)) for index in range(days)]
    _prepare(monkeypatch, points)

    result = analytics_api.trends(
        start=start,
        end=start + timedelta(days=days - 1),
        user=USER,
        db=object(),
    )

    assert [item["date"] for item in result["points"]] == [point.date.isoformat() for point in points]
    assert len(result["points"]) == days


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


def test_trends_canonical_flag_defaults_off() -> None:
    assert Settings(_env_file=None, environment="test").analytics_trends_canonical_read_enabled is False


def test_trends_flag_off_keeps_legacy_without_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    start = date(2026, 9, 1)
    points = [_point(start, "1900")]
    _prepare(monkeypatch, points)
    monkeypatch.setattr(analytics_api.settings, "analytics_trends_canonical_read_enabled", False)
    called = False

    def fail_if_called(*args: object, **kwargs: object) -> None:
        nonlocal called
        called = True
        raise AssertionError("disabled Trends Canonical read must not run")

    monkeypatch.setattr(analytics_api, "run_trends_canonical_read", fail_if_called, raising=False)
    result = analytics_api.trends(
        start=start,
        end=start,
        include_incomplete=False,
        user=USER,
        db=object(),
    )

    assert called is False
    assert result["points"][0]["calories_kcal"] == "1900"


@pytest.mark.parametrize(
    ("period", "days"),
    [("all", 1), (None, 32)],
)
def test_trends_ineligible_requests_do_not_attempt_canonical(
    monkeypatch: pytest.MonkeyPatch,
    period: str | None,
    days: int,
) -> None:
    start = date(2026, 9, 1)
    points = [_point(start, "1900")]
    _prepare(monkeypatch, points)
    monkeypatch.setattr(analytics_api.settings, "analytics_trends_canonical_read_enabled", True)
    called = False

    def fail_if_called(*args: object, **kwargs: object) -> None:
        nonlocal called
        called = True
        raise AssertionError("ineligible Trends request must not attempt Canonical")

    monkeypatch.setattr(analytics_api, "run_trends_canonical_read", fail_if_called, raising=False)
    result = analytics_api.trends(
        start=start,
        end=start + timedelta(days=days - 1),
        period=period,
        include_incomplete=False,
        user=USER,
        db=object(),
    )

    assert called is False
    assert result["points"][0]["calories_kcal"] == "1900"


def test_trends_31_day_request_uses_canonical_points(monkeypatch: pytest.MonkeyPatch) -> None:
    start = date(2026, 9, 1)
    legacy = [_point(start + timedelta(days=index), "1900") for index in range(31)]
    canonical = [_point(start + timedelta(days=index), "1800") for index in range(31)]
    _prepare(monkeypatch, legacy)
    monkeypatch.setattr(analytics_api.settings, "analytics_trends_canonical_read_enabled", True)
    calls: list[tuple[object, ...]] = []

    def run(*args: object, **kwargs: object) -> DailyCanonicalOutcome:
        calls.append(args)
        assert kwargs["legacy_points"] == tuple(legacy)
        return DailyCanonicalOutcome(DailyCanonicalState.MATCH, points=tuple(canonical))

    monkeypatch.setattr(analytics_api, "run_trends_canonical_read", run, raising=False)
    result = analytics_api.trends(
        start=start,
        end=start + timedelta(days=30),
        include_incomplete=False,
        user=USER,
        db=object(),
    )

    assert len(calls) == 1
    assert result["points"][0]["calories_kcal"] == "1800"


def test_trends_exact_match_preserves_public_response_for_both_incomplete_modes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = date(2026, 9, 1)
    points = [
        _point(start, "1000"),
        _point(start + timedelta(days=1), None, tracking_status="no_data"),
        _point(start + timedelta(days=2), "1500", tracking_status="incomplete"),
    ]
    _prepare(monkeypatch, points)
    monkeypatch.setattr(analytics_api.settings, "analytics_trends_canonical_read_enabled", False)
    legacy_false = analytics_api.trends(
        start=start,
        end=start + timedelta(days=2),
        include_incomplete=False,
        user=USER,
        db=object(),
    )
    legacy_true = analytics_api.trends(
        start=start,
        end=start + timedelta(days=2),
        include_incomplete=True,
        user=USER,
        db=object(),
    )
    monkeypatch.setattr(analytics_api.settings, "analytics_trends_canonical_read_enabled", True)
    monkeypatch.setattr(
        analytics_api,
        "run_trends_canonical_read",
        lambda *args, **kwargs: DailyCanonicalOutcome(
            DailyCanonicalState.MATCH,
            points=tuple(points),
        ),
        raising=False,
    )

    canonical_false = analytics_api.trends(
        start=start,
        end=start + timedelta(days=2),
        include_incomplete=False,
        user=USER,
        db=object(),
    )
    canonical_true = analytics_api.trends(
        start=start,
        end=start + timedelta(days=2),
        include_incomplete=True,
        user=USER,
        db=object(),
    )

    assert canonical_false == legacy_false
    assert canonical_true == legacy_true


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        (DailyCanonicalState.NOT_COMPARABLE, DailyCanonicalReason.PROJECTION_NOT_READY),
        (DailyCanonicalState.FALLBACK, DailyCanonicalReason.LEGACY_SNAPSHOT_CHANGED),
        (DailyCanonicalState.FALLBACK, DailyCanonicalReason.SOURCE_PRIORITY_POLICY_INVALID),
        (DailyCanonicalState.ERROR, None),
    ],
)
def test_trends_fallback_never_mixes_points(
    monkeypatch: pytest.MonkeyPatch,
    state: DailyCanonicalState,
    reason: DailyCanonicalReason | None,
) -> None:
    start = date(2026, 9, 1)
    points = [_point(start, "1900")]
    _prepare(monkeypatch, points)
    monkeypatch.setattr(analytics_api.settings, "analytics_trends_canonical_read_enabled", True)
    monkeypatch.setattr(
        analytics_api,
        "run_trends_canonical_read",
        lambda *args, **kwargs: DailyCanonicalOutcome(state, reason=reason),
        raising=False,
    )

    result = analytics_api.trends(
        start=start,
        end=start,
        include_incomplete=False,
        user=USER,
        db=object(),
    )

    assert result["points"][0]["calories_kcal"] == "1900"


def test_trends_mutation_happens_after_serving_snapshot_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = date(2026, 9, 1)
    points = [_point(start, "600", tracking_status="incomplete")]
    _prepare(monkeypatch, points)
    monkeypatch.setattr(analytics_api.settings, "analytics_trends_canonical_read_enabled", True)
    snapshots: list[tuple[DailyPoint, ...]] = []

    def fallback(*args: object, **kwargs: object) -> DailyCanonicalOutcome:
        snapshot = kwargs["legacy_points"]
        assert isinstance(snapshot, tuple)
        snapshots.append(snapshot)
        assert snapshot[0].tracking_status == "incomplete"
        return DailyCanonicalOutcome(DailyCanonicalState.FALLBACK, reason=DailyCanonicalReason.LEGACY_SNAPSHOT_CHANGED)

    monkeypatch.setattr(analytics_api, "run_trends_canonical_read", fallback, raising=False)
    analytics_api.trends(
        start=start,
        end=start,
        include_incomplete=True,
        user=USER,
        db=object(),
    )

    assert snapshots[0][0].tracking_status == "incomplete"


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
def test_trends_adapter_maps_shared_serving_result(
    monkeypatch: pytest.MonkeyPatch,
    outcome: CanonicalServingOutcome,
    detail: CanonicalServingDetail | None,
    state: DailyCanonicalState,
    reason: DailyCanonicalReason | None,
) -> None:
    from app.analytics import trends_canonical

    points = (_point(date(2026, 9, 1), "1900"),)
    monkeypatch.setattr(
        trends_canonical,
        "serve_canonical",
        lambda request: _serving_result(points, outcome, detail=detail),
    )

    result = trends_canonical.run_trends_canonical_read(
        USER.id,
        date(2026, 9, 1),
        date(2026, 9, 1),
        legacy_points=points,
    )

    assert result.state is state
    assert result.reason is reason
    assert result.points == (points if outcome is CanonicalServingOutcome.CANONICAL_SERVED else None)


def test_trends_adapter_enforces_user_endpoint_and_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.analytics import trends_canonical

    points = (_point(date(2026, 9, 1), "1900"),)
    requests: list[object] = []
    monkeypatch.setattr(
        trends_canonical,
        "serve_canonical",
        lambda request: (
            requests.append(request)
            or _serving_result(points, CanonicalServingOutcome.CANONICAL_SERVED)
        ),
    )

    result = trends_canonical.run_trends_canonical_read(
        USER.id,
        date(2026, 9, 1),
        date(2026, 9, 1),
        legacy_points=points,
    )

    request = requests[0]
    assert request.user_id == USER.id
    assert request.endpoint is CanonicalServingEndpoint.TRENDS
    assert request.legacy_points == points
    assert result.points == points


def test_trends_adapter_telemetry_is_bounded_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    from app.analytics import trends_canonical

    points = (_point(date(2026, 9, 1), "1900"),)
    monkeypatch.setattr(
        trends_canonical,
        "serve_canonical",
        lambda request: (_ for _ in ()).throw(RuntimeError("secret calories 1900")),
    )

    with caplog.at_level(logging.INFO, logger=trends_canonical.LOGGER.name):
        trends_canonical.run_trends_canonical_read(
            USER.id,
            date(2026, 9, 1),
            date(2026, 9, 1),
            legacy_points=points,
        )

    records = [record.message for record in caplog.records if record.name == trends_canonical.LOGGER.name]
    assert len(records) == 1
    message = records[0]
    assert "analytics.trends.canonical" in message
    assert "d4g.v1" in message
    assert "RuntimeError" in message
    assert str(USER.id) not in message
    assert "2026-09-01" not in message
    assert "1900" not in message


def test_trends_get_path_adds_no_new_business_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    class ExplodingDb:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"unexpected database access: {name}")

    _prepare(monkeypatch, [_point(date(2026, 9, 1), "1900")])
    result = analytics_api.trends(
        start=date(2026, 9, 1),
        end=date(2026, 9, 1),
        include_incomplete=False,
        user=USER,
        db=ExplodingDb(),
    )
    assert result["budget_balance"] == BALANCE



def test_trends_canonical_flag_is_wired_to_templates_and_compose() -> None:
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
        assert "ANALYTICS_TRENDS_CANONICAL_READ_ENABLED" in path.read_text()
