from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass
from datetime import date, timedelta
from uuid import UUID

import pytest

from app.analytics import daily_shadow as shadow
from app.analytics.daily_point_parity import DailyPointParityClassification
from app.analytics.daily_shadow import (
    DailyShadowOutcome,
    DailyShadowState,
    check_daily_shadow_eligibility,
    collapse_daily_point_range,
    run_daily_shadow,
)


@dataclass(frozen=True, slots=True)
class FakeDay:
    comparable: bool
    classification: DailyPointParityClassification


@dataclass(frozen=True, slots=True)
class FakeRange:
    days: tuple[FakeDay, ...]
    days_compared: int = 0
    days_not_comparable: int = 0
    expected_tracking_differences: int = 0
    unexplained_tracking_mismatches: int = 0
    unexpected_target_activity_mismatches: int = 0


@pytest.mark.parametrize(
    ("kwargs", "state", "reason"),
    [
        (
            {"enabled": False, "source": None, "tracking": None, "weekday": None},
            DailyShadowState.DISABLED,
            "disabled",
        ),
        (
            {"enabled": True, "source": "apple", "tracking": None, "weekday": None},
            DailyShadowState.SKIPPED,
            "source_filter",
        ),
        (
            {"enabled": True, "source": None, "tracking": "complete", "weekday": None},
            DailyShadowState.SKIPPED,
            "tracking_filter",
        ),
        (
            {"enabled": True, "source": None, "tracking": None, "weekday": 2},
            DailyShadowState.SKIPPED,
            "weekday_filter",
        ),
        (
            {"enabled": True, "source": None, "tracking": None, "weekday": None},
            DailyShadowState.SKIPPED,
            "range_too_large",
        ),
    ],
)
def test_daily_shadow_eligibility_has_static_skip_reasons(kwargs, state, reason) -> None:
    outcome = check_daily_shadow_eligibility(
        start=date(2026, 1, 1),
        end=date(2026, 1, 31) + timedelta(days=1 if reason == "range_too_large" else 0),
        max_days=31,
        **kwargs,
    )

    assert outcome.state is state
    assert outcome.reason == reason


def test_daily_shadow_eligibility_accepts_inclusive_maximum_range() -> None:
    outcome = check_daily_shadow_eligibility(
        enabled=True,
        source=None,
        tracking=None,
        weekday=None,
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
        max_days=31,
    )

    assert outcome.state is DailyShadowState.MATCH
    assert outcome.reason is None


def test_daily_shadow_eligibility_rejects_one_day_above_maximum() -> None:
    outcome = check_daily_shadow_eligibility(
        enabled=True,
        source=None,
        tracking=None,
        weekday=None,
        start=date(2026, 1, 1),
        end=date(2026, 2, 1),
        max_days=31,
    )

    assert outcome.state is DailyShadowState.SKIPPED
    assert outcome.reason == "range_too_large"


def test_daily_shadow_eligibility_rejects_reversed_range_without_database_access() -> None:
    outcome = check_daily_shadow_eligibility(
        enabled=True,
        source=None,
        tracking=None,
        weekday=None,
        start=date(2026, 2, 1),
        end=date(2026, 1, 1),
        max_days=31,
    )

    assert outcome.state is DailyShadowState.SKIPPED
    assert outcome.reason == "invalid_range"


@pytest.mark.parametrize(
    ("days", "expected"),
    [
        (
            (FakeDay(True, DailyPointParityClassification.MATCH),),
            DailyShadowState.MATCH,
        ),
        (
            (
                FakeDay(True, DailyPointParityClassification.MATCH),
                FakeDay(True, DailyPointParityClassification.EXPLICIT_ZERO_SEMANTIC_DIFFERENCE),
            ),
            DailyShadowState.EXPECTED_DIFFERENCE,
        ),
        (
            (FakeDay(True, DailyPointParityClassification.NUTRITION_VALUE_SEMANTIC_DIFFERENCE),),
            DailyShadowState.EXPECTED_DIFFERENCE,
        ),
        (
            (FakeDay(True, DailyPointParityClassification.UNEXPLAINED_MISMATCH),),
            DailyShadowState.UNEXPLAINED_MISMATCH,
        ),
        (
            (FakeDay(False, DailyPointParityClassification.MATCH),),
            DailyShadowState.NOT_COMPARABLE,
        ),
        (
            (FakeDay(True, DailyPointParityClassification.NOT_COMPARABLE),),
            DailyShadowState.NOT_COMPARABLE,
        ),
    ],
)
def test_collapse_uses_day_level_classification_precedence(days, expected) -> None:
    result = collapse_daily_point_range(FakeRange(days=days))

    assert result.state is expected


def test_collapse_unexplained_mismatch_beats_expected_difference() -> None:
    result = collapse_daily_point_range(
        FakeRange(
            days=(
                FakeDay(True, DailyPointParityClassification.EXPLICIT_ZERO_SEMANTIC_DIFFERENCE),
                FakeDay(True, DailyPointParityClassification.UNEXPLAINED_MISMATCH),
            )
        )
    )

    assert result.state is DailyShadowState.UNEXPLAINED_MISMATCH


def test_daily_shadow_outcome_is_frozen_and_slot_backed() -> None:
    assert DailyShadowOutcome.__dataclass_params__.frozen
    assert DailyShadowOutcome.__slots__
    with pytest.raises(FrozenInstanceError):
        DailyShadowOutcome(DailyShadowState.MATCH).state = DailyShadowState.ERROR  # type: ignore[misc]


class RecordingSession:
    def __init__(self) -> None:
        self.rollback_count = 0
        self.commit_count = 0
        self.close_count = 0

    def __enter__(self) -> RecordingSession:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        self.close_count += 1
        return False

    def rollback(self) -> None:
        self.rollback_count += 1

    def commit(self) -> None:
        self.commit_count += 1


class SentinelFailure(RuntimeError):
    pass


def test_run_daily_shadow_failure_rolls_back_closes_and_fails_open(monkeypatch, caplog) -> None:
    session = RecordingSession()

    monkeypatch.setattr(shadow, "SessionLocal", lambda: session)

    def fail(*args, **kwargs):
        raise SentinelFailure(
            "SENTINEL nutrition=123.45 date=2026-01-01 uuid=01234567-89ab-cdef-0123-456789abcdef "
            "provider=secret request_id=req-123"
        )

    monkeypatch.setattr(shadow, "compare_daily_point_range", fail)

    with caplog.at_level("INFO", logger=shadow.LOGGER.name):
        result = run_daily_shadow(
            UUID("01234567-89ab-cdef-0123-456789abcdef"),
            date(2026, 1, 1),
            date(2026, 1, 1),
            None,
            None,
            None,
            enabled=True,
            max_days=31,
        )

    assert result.state is DailyShadowState.ERROR
    assert session.rollback_count == 1
    assert session.commit_count == 0
    assert session.close_count == 1
    rendered = " ".join(record.getMessage() for record in caplog.records)
    assert "SENTINEL" not in rendered
    assert "123.45" not in rendered
    assert "2026-01-01" not in rendered
    assert "01234567-89ab-cdef" not in rendered
    assert "secret" not in rendered
    assert "req-123" not in rendered
    assert "nutrition" not in rendered
    assert all(record.exc_info is None for record in caplog.records)


def test_run_daily_shadow_success_never_commits_and_passes_bounded_inputs(monkeypatch) -> None:
    session = RecordingSession()
    calls = []
    parity = FakeRange(
        days=(FakeDay(True, DailyPointParityClassification.MATCH),),
        days_compared=1,
    )

    monkeypatch.setattr(shadow, "SessionLocal", lambda: session)

    def compare(*args, **kwargs):
        calls.append((args, kwargs))
        return parity

    monkeypatch.setattr(shadow, "compare_daily_point_range", compare)

    result = run_daily_shadow(
        UUID("01234567-89ab-cdef-0123-456789abcdef"),
        date(2026, 1, 1),
        date(2026, 1, 1),
        None,
        None,
        None,
        enabled=True,
        max_days=31,
    )

    assert result.state is DailyShadowState.MATCH
    assert session.rollback_count == 0
    assert session.commit_count == 0
    assert session.close_count == 1
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == (session,)
    assert kwargs == {
        "user_id": UUID("01234567-89ab-cdef-0123-456789abcdef"),
        "start": date(2026, 1, 1),
        "end": date(2026, 1, 1),
        "max_days": 31,
    }


def test_run_daily_shadow_skips_without_opening_session(monkeypatch) -> None:
    opened = False

    def open_session():
        nonlocal opened
        opened = True
        raise AssertionError("ineligible shadow must not access the database")

    monkeypatch.setattr(shadow, "SessionLocal", open_session)

    result = run_daily_shadow(
        UUID("01234567-89ab-cdef-0123-456789abcdef"),
        date(2026, 1, 1),
        date(2026, 1, 1),
        "apple",
        None,
        None,
        enabled=True,
        max_days=31,
    )

    assert result.state is DailyShadowState.SKIPPED
    assert result.reason == "source_filter"
    assert opened is False
