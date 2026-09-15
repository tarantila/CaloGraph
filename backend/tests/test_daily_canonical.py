from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.analytics import daily_canonical as canonical
from app.analytics.daily_point_parity import (
    DailyPointParityClassification,
    DailyPointRangeParity,
)
from app.schemas import DailyPoint

PROJECTION_ID_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
PROJECTION_ID_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


@dataclass(frozen=True, slots=True)
class FakeDay:
    comparable: bool
    classification: DailyPointParityClassification


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


class MissingPolicySession(RecordingSession):
    def scalar(self, statement):
        return None


class EmptyScalarResult:
    def all(self):
        return ()


class InvalidPolicySession(RecordingSession):
    def scalar(self, statement):
        return SimpleNamespace(
            id=UUID("11111111-1111-1111-1111-111111111111"),
            user_id=UUID("01234567-89ab-cdef-0123-456789abcdef"),
            version=0,
            effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        )

    def scalars(self, statement):
        return EmptyScalarResult()


class StalePolicySession(RecordingSession):
    def __init__(self, stale_policy_id: UUID) -> None:
        super().__init__()
        user_id = UUID("01234567-89ab-cdef-0123-456789abcdef")
        self.stale = SimpleNamespace(
            id=stale_policy_id,
            user_id=user_id,
            version=1,
            effective_from=datetime(2020, 1, 1, tzinfo=UTC),
        )
        self.current = SimpleNamespace(
            id=uuid4(),
            user_id=user_id,
            version=2,
            effective_from=datetime(2021, 1, 1, tzinfo=UTC),
        )
        self.scalar_calls = 0

    def scalar(self, statement):
        self.scalar_calls += 1
        return self.stale if self.scalar_calls == 1 else self.current

    def scalars(self, statement):
        return EmptyScalarResult()


class PolicySwitchSession(RecordingSession):
    def __init__(self, policy: object) -> None:
        super().__init__()
        self.policy = policy

    def scalar(self, statement):
        return self.policy

    def scalars(self, statement):
        return EmptyScalarResult()


def _parity(
    day: FakeDay,
    *,
    canonical_point: DailyPoint | None = None,
    projection_id: UUID | None = PROJECTION_ID_A,
    projection_ready: bool = False,
    calorie_usable: bool = False,
) -> DailyPointRangeParity:
    local_date = date(2026, 1, 1)
    return DailyPointRangeParity(
        start=local_date,
        end=local_date,
        days=(day,),
        days_compared=1 if day.comparable else 0,
        status_matches=1 if day.classification is DailyPointParityClassification.MATCH else 0,
        days_not_comparable=0 if day.comparable else 1,
        expected_tracking_differences=0,
        unexplained_tracking_mismatches=0,
        unexpected_target_activity_mismatches=0,
        canonical_points=() if canonical_point is None else (canonical_point,),
        canonical_projection_ids=(projection_id,),
        canonical_projection_ready=(projection_ready,),
        canonical_calorie_usable=(calorie_usable,),
    )


def _run(
    monkeypatch,
    session,
    parity,
    *,
    policy_id: UUID | None = None,
    compare=None,
    head_metadata: dict[date, tuple[UUID, UUID]] | None = None,
):
    monkeypatch.setattr(canonical, "SessionLocal", lambda: session)
    if compare is None:
        def compare(*args, **kwargs):
            return parity
    monkeypatch.setattr(canonical, "compare_daily_point_range", compare)
    if head_metadata is None and policy_id is not None:
        head_metadata = {date(2026, 1, 1): (PROJECTION_ID_A, policy_id)}
    if head_metadata is not None:
        monkeypatch.setattr(
            canonical,
            "_read_canonical_head_metadata",
            lambda *args, **kwargs: head_metadata,
        )
    return canonical.run_daily_canonical_read(
        UUID("01234567-89ab-cdef-0123-456789abcdef"),
        date(2026, 1, 1),
        date(2026, 1, 1),
        None,
        None,
        None,
        enabled=True,
        max_days=31,
    )


def test_evaluator_not_comparable_falls_back_without_serving(monkeypatch) -> None:
    session = RecordingSession()
    parity = _parity(
        FakeDay(False, DailyPointParityClassification.NOT_COMPARABLE),
    )

    result = _run(monkeypatch, session, parity)

    assert result.state is canonical.DailyCanonicalState.NOT_COMPARABLE
    assert result.reason == "not_comparable"
    assert result.points is None
    assert session.commit_count == 0
    assert session.rollback_count == 0
    assert session.close_count == 1


def test_evaluator_missing_source_priority_policy_falls_back(monkeypatch) -> None:
    session = MissingPolicySession()
    policy_id = uuid4()
    point = DailyPoint.model_construct(date=date(2026, 1, 1))
    parity = _parity(
        FakeDay(True, DailyPointParityClassification.MATCH),
        canonical_point=point,
        projection_ready=True,
        calorie_usable=True,
    )

    result = _run(monkeypatch, session, parity, policy_id=policy_id)

    assert result.state is canonical.DailyCanonicalState.FALLBACK
    assert result.reason == "source_priority_policy_invalid"
    assert result.points is None
    assert session.commit_count == 0
    assert session.rollback_count == 0


def test_evaluator_invalid_source_priority_policy_falls_back(monkeypatch) -> None:
    session = InvalidPolicySession()
    policy_id = uuid4()
    point = DailyPoint.model_construct(date=date(2026, 1, 1))
    parity = _parity(
        FakeDay(True, DailyPointParityClassification.MATCH),
        canonical_point=point,
        projection_ready=True,
        calorie_usable=True,
    )

    result = _run(monkeypatch, session, parity, policy_id=policy_id)

    assert result.state is canonical.DailyCanonicalState.FALLBACK
    assert result.reason == "source_priority_policy_invalid"
    assert result.points is None
    assert session.commit_count == 0
    assert session.rollback_count == 0


def test_evaluator_stale_source_priority_policy_falls_back(monkeypatch) -> None:
    policy_id = uuid4()
    session = StalePolicySession(policy_id)
    point = DailyPoint.model_construct(date=date(2026, 1, 1))
    parity = _parity(
        FakeDay(True, DailyPointParityClassification.MATCH),
        canonical_point=point,
        projection_ready=True,
        calorie_usable=True,
    )

    result = _run(monkeypatch, session, parity, policy_id=policy_id)

    assert result.state is canonical.DailyCanonicalState.FALLBACK
    assert result.reason == "source_priority_policy_invalid"
    assert result.points is None
    assert session.scalar_calls == 2
    assert session.commit_count == 0
    assert session.rollback_count == 0


def test_evaluator_rejects_projection_head_changed_after_compare(monkeypatch) -> None:
    session = RecordingSession()
    policy_id = uuid4()
    point = DailyPoint.model_construct(date=date(2026, 1, 1))
    parity = _parity(
        FakeDay(True, DailyPointParityClassification.MATCH),
        canonical_point=point,
        projection_id=PROJECTION_ID_A,
        projection_ready=True,
        calorie_usable=True,
    )
    monkeypatch.setattr(canonical, "_valid_source_priority_policy", lambda *args, **kwargs: True)

    result = _run(
        monkeypatch,
        session,
        parity,
        head_metadata={date(2026, 1, 1): (PROJECTION_ID_B, policy_id)},
    )

    assert result.state is canonical.DailyCanonicalState.NOT_COMPARABLE
    assert result.reason == "projection_not_ready"
    assert result.points is None
    assert session.commit_count == 0
    assert session.rollback_count == 0


def test_evaluator_revalidates_effective_policy_after_compare(monkeypatch) -> None:
    user_id = UUID("01234567-89ab-cdef-0123-456789abcdef")
    policy_a_id = uuid4()
    policy_c_id = uuid4()
    policy_a = SimpleNamespace(
        id=policy_a_id,
        user_id=user_id,
        version=1,
        effective_from=datetime(2020, 1, 1, tzinfo=UTC),
    )
    policy_c = SimpleNamespace(
        id=policy_c_id,
        user_id=user_id,
        version=2,
        effective_from=datetime(2021, 1, 1, tzinfo=UTC),
    )
    session = PolicySwitchSession(policy_a)
    point = DailyPoint.model_construct(date=date(2026, 1, 1))
    parity = _parity(
        FakeDay(True, DailyPointParityClassification.MATCH),
        canonical_point=point,
        projection_id=PROJECTION_ID_A,
        projection_ready=True,
        calorie_usable=True,
    )
    switched_at: datetime | None = None

    def compare(*args, **kwargs):
        nonlocal switched_at
        switched_at = datetime.now(UTC)
        return parity

    def effective_policy(db, requested_user_id, at):
        assert switched_at is not None
        assert requested_user_id == user_id
        return policy_c if at >= switched_at else policy_a

    monkeypatch.setattr(canonical, "get_effective_policy", effective_policy)
    result = _run(
        monkeypatch,
        session,
        parity,
        policy_id=policy_a_id,
        compare=compare,
    )

    assert result.state is canonical.DailyCanonicalState.FALLBACK
    assert result.reason == "source_priority_policy_invalid"
    assert result.points is None


def test_evaluator_ready_projection_without_primary_calories_falls_back(monkeypatch) -> None:
    session = RecordingSession()
    parity = _parity(
        FakeDay(True, DailyPointParityClassification.MATCH),
        canonical_point=DailyPoint.model_construct(date=date(2026, 1, 1)),
        projection_ready=True,
        calorie_usable=False,
    )

    result = _run(monkeypatch, session, parity)

    assert result.state is canonical.DailyCanonicalState.FALLBACK
    assert result.reason == "projection_no_primary_values"
    assert result.points is None
    assert session.commit_count == 0
    assert session.rollback_count == 0


def test_evaluator_strict_match_serves_carried_canonical_object(monkeypatch) -> None:
    session = RecordingSession()
    point = DailyPoint.model_construct(date=date(2026, 1, 1))
    policy_id = uuid4()
    parity = _parity(
        FakeDay(True, DailyPointParityClassification.MATCH),
        canonical_point=point,
        projection_ready=True,
        calorie_usable=True,
    )
    monkeypatch.setattr(canonical, "_valid_source_priority_policy", lambda *args, **kwargs: True)

    result = _run(monkeypatch, session, parity, policy_id=policy_id)

    assert result.state is canonical.DailyCanonicalState.MATCH
    assert result.points is not None
    assert result.points[0] is point
    assert session.commit_count == 0
    assert session.rollback_count == 0
    assert session.close_count == 1


def test_ineligible_evaluator_telemetry_uses_public_bounded_outcome(monkeypatch, caplog) -> None:
    monkeypatch.setattr(
        canonical,
        "compare_daily_point_range",
        lambda *args, **kwargs: pytest.fail("ineligible request must not compare"),
    )

    with caplog.at_level(logging.INFO, logger=canonical.LOGGER.name):
        result = canonical.run_daily_canonical_read(
            UUID("01234567-89ab-cdef-0123-456789abcdef"),
            date(2026, 1, 1),
            date(2026, 1, 1),
            "apple",
            None,
            None,
            enabled=True,
            max_days=31,
        )

    assert result.state is canonical.DailyCanonicalState.SKIPPED
    assert len(caplog.records) == 1
    payload = json.loads(caplog.records[0].getMessage())
    assert payload["event"] == canonical.TELEMETRY_EVENT
    assert payload["version"] == canonical.TELEMETRY_VERSION
    assert payload["outcome"] == "legacy_fallback_not_eligible"
    assert set(payload) == {"event", "version", "outcome", "range_bucket", "duration_bucket"}
    assert payload["outcome"] in {
        "canonical_served",
        "legacy_fallback_not_eligible",
        "legacy_fallback_not_ready",
        "legacy_fallback_not_comparable",
        "legacy_fallback_expected_difference",
        "legacy_fallback_unexplained_mismatch",
        "legacy_fallback_error",
    }
