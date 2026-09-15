from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.analytics import daily_canonical as canonical
from app.analytics.daily_point_parity import DailyPointParityClassification
from app.schemas import DailyPoint
from app.source_priority.contracts import PriorityPolicySnapshot, PriorityRuleSnapshot

USER_ID = UUID("01234567-89ab-cdef-0123-456789abcdef")
PROJECTION_ID_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
PROJECTION_ID_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
PROJECTION_ID_C = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
POLICY_ID_A = UUID("11111111-1111-1111-1111-111111111111")
POLICY_ID_B = UUID("22222222-2222-2222-2222-222222222222")
RULE_ID_A = UUID("33333333-3333-3333-3333-333333333333")
RULE_ID_B = UUID("44444444-4444-4444-4444-444444444444")


@dataclass(frozen=True, slots=True)
class FakeDay:
    comparable: bool
    classification: DailyPointParityClassification


class SealRows:
    def __init__(self, rows: tuple[SimpleNamespace, ...]) -> None:
        self.rows = rows

    def all(self) -> tuple[SimpleNamespace, ...]:
        return self.rows


class RecordingSession:
    def __init__(self, rows: tuple[SimpleNamespace, ...] = ()) -> None:
        self.rows = rows
        self.execute_count = 0
        self.rollback_count = 0
        self.commit_count = 0
        self.close_count = 0

    def __enter__(self) -> RecordingSession:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        self.close_count += 1
        return False

    def execute(self, statement):
        del statement
        self.execute_count += 1
        return SealRows(self.rows)

    def rollback(self) -> None:
        self.rollback_count += 1

    def commit(self) -> None:
        self.commit_count += 1


def _policy(
    *,
    policy_id: UUID = POLICY_ID_A,
    version: int = 1,
    effective_from: datetime = datetime(2020, 1, 1, tzinfo=UTC),
    rules: tuple[PriorityRuleSnapshot, ...] | None = None,
) -> PriorityPolicySnapshot:
    return PriorityPolicySnapshot(
        policy_id=policy_id,
        user_id=USER_ID,
        version=version,
        effective_from=effective_from,
        rules=rules
        if rules is not None
        else (
            PriorityRuleSnapshot(
                rule_id=RULE_ID_A,
                data_area="nutrition",
                metric_key=None,
                provider_key="apple_health",
                priority_rank=1,
            ),
        ),
    )


def _seal_rows(
    *,
    dates: tuple[date, ...],
    projection_ids: tuple[UUID, ...],
    projection_policy_id: UUID = POLICY_ID_A,
    effective_policy: PriorityPolicySnapshot | None = None,
    rules: tuple[PriorityRuleSnapshot, ...] | None = None,
) -> tuple[SimpleNamespace, ...]:
    policy = _policy() if effective_policy is None else effective_policy
    actual_rules = policy.rules if rules is None else rules
    return tuple(
        SimpleNamespace(
            head_local_date=local_date,
            head_projection_id=projection_id,
            projection_id=projection_id,
            projection_user_id=USER_ID,
            projection_local_date=local_date,
            projection_status="ready",
            projection_policy_id=projection_policy_id,
            effective_policy_id=policy.policy_id,
            effective_policy_user_id=policy.user_id,
            effective_policy_version=policy.version,
            effective_policy_effective_from=policy.effective_from,
            rule_id=rule.rule_id if rule is not None else None,
            rule_user_id=USER_ID if rule is not None else None,
            rule_policy_id=policy.policy_id if rule is not None else None,
            rule_data_area=rule.data_area if rule is not None else None,
            rule_metric_key=rule.metric_key if rule is not None else None,
            rule_provider_key=rule.provider_key if rule is not None else None,
            rule_priority_rank=rule.priority_rank if rule is not None else None,
        )
        for local_date, projection_id in zip(dates, projection_ids, strict=True)
        for rule in actual_rules or (None,)
    )


def _parity(
    *,
    dates: tuple[date, ...] = (date(2026, 1, 1),),
    classifications: tuple[DailyPointParityClassification, ...] | None = None,
    canonical_points: tuple[DailyPoint, ...] | None = None,
    projection_ids: tuple[UUID, ...] = (PROJECTION_ID_A,),
    projection_ready: bool = True,
    calorie_usable: bool = True,
    policy: PriorityPolicySnapshot | None = None,
) -> SimpleNamespace:
    day_classifications = classifications or (DailyPointParityClassification.MATCH,) * len(dates)
    points = canonical_points or tuple(DailyPoint.model_construct(date=local_date) for local_date in dates)
    return SimpleNamespace(
        start=dates[0],
        end=dates[-1],
        days=tuple(FakeDay(True, classification) for classification in day_classifications),
        canonical_points=points,
        canonical_projection_ids=projection_ids,
        canonical_projection_ready=(projection_ready,) * len(dates),
        canonical_calorie_usable=(calorie_usable,) * len(dates),
        canonical_policy_snapshot=_policy() if policy is None else policy,
    )


def _run(
    monkeypatch,
    session: RecordingSession,
    parity: SimpleNamespace,
    *,
    compare=None,
    head_metadata: dict[date, tuple[UUID, UUID]] | None = None,
):
    monkeypatch.setattr(canonical, "SessionLocal", lambda: session)
    if compare is None:

        def compare(*args, **kwargs):
            return parity

    monkeypatch.setattr(canonical, "compare_daily_point_range", compare)
    if head_metadata is None:
        policy_id = parity.canonical_policy_snapshot.policy_id
        head_metadata = {
            local_date: (projection_id, policy_id)
            for local_date, projection_id in zip(
                (parity.start + timedelta(days=offset) for offset in range(len(parity.days))),
                parity.canonical_projection_ids,
                strict=True,
            )
        }
    monkeypatch.setattr(
        canonical,
        "_read_canonical_head_metadata",
        lambda *args, **kwargs: head_metadata,
        raising=False,
    )
    monkeypatch.setattr(
        canonical,
        "_valid_source_priority_policy",
        lambda *args, **kwargs: True,
        raising=False,
    )
    return canonical.run_daily_canonical_read(
        USER_ID,
        parity.start,
        parity.end,
        None,
        None,
        None,
        enabled=True,
        max_days=31,
    )


def test_evaluator_not_comparable_falls_back_without_serving(monkeypatch) -> None:
    parity = _parity(
        classifications=(DailyPointParityClassification.NOT_COMPARABLE,),
        projection_ready=False,
        calorie_usable=False,
    )
    session = RecordingSession()

    result = _run(monkeypatch, session, parity)

    assert result.state is canonical.DailyCanonicalState.NOT_COMPARABLE
    assert result.reason == "not_comparable"
    assert result.points is None
    assert session.execute_count == 0
    assert session.commit_count == 0
    assert session.rollback_count == 0
    assert session.close_count == 1


def test_evaluator_rejects_head_changed_before_final_seal(monkeypatch) -> None:
    parity = _parity()
    session = RecordingSession(
        _seal_rows(
            dates=(date(2026, 1, 1),),
            projection_ids=(PROJECTION_ID_B,),
        )
    )

    result = _run(monkeypatch, session, parity)

    assert result.state is canonical.DailyCanonicalState.FALLBACK
    assert result.points is None
    assert session.execute_count == 1
    assert session.commit_count == 0
    assert session.rollback_count == 0


def test_evaluator_rejects_rule_added_before_final_seal(monkeypatch) -> None:
    expected_policy = _policy()
    added_rule = PriorityRuleSnapshot(
        rule_id=RULE_ID_B,
        data_area="nutrition",
        metric_key="dietary_energy_kcal",
        provider_key="yazio",
        priority_rank=1,
    )
    parity = _parity(policy=expected_policy)
    session = RecordingSession(
        _seal_rows(
            dates=(date(2026, 1, 1),),
            projection_ids=(PROJECTION_ID_A,),
            rules=(*expected_policy.rules, added_rule),
        )
    )

    result = _run(monkeypatch, session, parity)

    assert result.state is canonical.DailyCanonicalState.FALLBACK
    assert result.points is None
    assert session.execute_count == 1


def test_evaluator_rejects_rule_changed_before_final_seal(monkeypatch) -> None:
    expected_policy = _policy()
    changed_rule = PriorityRuleSnapshot(
        rule_id=RULE_ID_A,
        data_area="nutrition",
        metric_key="dietary_energy_kcal",
        provider_key="apple_health",
        priority_rank=1,
    )
    parity = _parity(policy=expected_policy)
    session = RecordingSession(
        _seal_rows(
            dates=(date(2026, 1, 1),),
            projection_ids=(PROJECTION_ID_A,),
            rules=(changed_rule,),
        )
    )

    result = _run(monkeypatch, session, parity)

    assert result.state is canonical.DailyCanonicalState.FALLBACK
    assert result.points is None
    assert session.execute_count == 1


def test_evaluator_rejects_rule_rank_changed_before_final_seal(monkeypatch) -> None:
    expected_policy = _policy(
        rules=(
            PriorityRuleSnapshot(
                rule_id=RULE_ID_A,
                data_area="nutrition",
                metric_key=None,
                provider_key="apple_health",
                priority_rank=1,
            ),
            PriorityRuleSnapshot(
                rule_id=RULE_ID_B,
                data_area="nutrition",
                metric_key=None,
                provider_key="yazio",
                priority_rank=2,
            ),
        )
    )
    changed_rule = PriorityRuleSnapshot(
        rule_id=RULE_ID_B,
        data_area="nutrition",
        metric_key=None,
        provider_key="yazio",
        priority_rank=3,
    )
    parity = _parity(policy=expected_policy)
    session = RecordingSession(
        _seal_rows(
            dates=(date(2026, 1, 1),),
            projection_ids=(PROJECTION_ID_A,),
            rules=(expected_policy.rules[0], changed_rule),
        )
    )

    result = _run(monkeypatch, session, parity)

    assert result.state is canonical.DailyCanonicalState.FALLBACK
    assert result.points is None
    assert session.execute_count == 1


def test_evaluator_rejects_rule_deleted_before_final_seal(monkeypatch) -> None:
    parity = _parity()
    session = RecordingSession(
        _seal_rows(
            dates=(date(2026, 1, 1),),
            projection_ids=(PROJECTION_ID_A,),
            rules=(),
        )
    )

    result = _run(monkeypatch, session, parity)

    assert result.state is canonical.DailyCanonicalState.FALLBACK
    assert result.points is None
    assert session.execute_count == 1


def test_evaluator_rejects_policy_changed_before_final_seal(monkeypatch) -> None:
    expected_policy = _policy()
    replacement_policy = _policy(
        policy_id=POLICY_ID_B,
        version=2,
        effective_from=datetime(2021, 1, 1, tzinfo=UTC),
    )
    parity = _parity(policy=expected_policy)
    session = RecordingSession(
        _seal_rows(
            dates=(date(2026, 1, 1),),
            projection_ids=(PROJECTION_ID_A,),
            effective_policy=replacement_policy,
        )
    )

    result = _run(monkeypatch, session, parity)

    assert result.state is canonical.DailyCanonicalState.FALLBACK
    assert result.points is None
    assert session.execute_count == 1


def test_evaluator_serves_two_day_range_after_single_matching_seal(monkeypatch) -> None:
    dates = (date(2026, 1, 1), date(2026, 1, 2))
    points = tuple(DailyPoint.model_construct(date=local_date) for local_date in dates)
    parity = _parity(
        dates=dates,
        canonical_points=points,
        projection_ids=(PROJECTION_ID_A, PROJECTION_ID_B),
    )
    session = RecordingSession(
        _seal_rows(
            dates=dates,
            projection_ids=(PROJECTION_ID_A, PROJECTION_ID_B),
        )
    )

    result = _run(monkeypatch, session, parity)

    assert result.state is canonical.DailyCanonicalState.MATCH
    assert result.points == points
    assert session.execute_count == 1
    assert session.commit_count == 0
    assert session.rollback_count == 0


def test_evaluator_rejects_entire_two_day_range_when_second_head_changes(monkeypatch) -> None:
    dates = (date(2026, 1, 1), date(2026, 1, 2))
    parity = _parity(
        dates=dates,
        projection_ids=(PROJECTION_ID_A, PROJECTION_ID_B),
    )
    session = RecordingSession(
        _seal_rows(
            dates=dates,
            projection_ids=(PROJECTION_ID_A, PROJECTION_ID_C),
        )
    )

    result = _run(monkeypatch, session, parity)

    assert result.state is canonical.DailyCanonicalState.FALLBACK
    assert result.points is None
    assert session.execute_count == 1


def test_evaluator_ready_projection_without_primary_calories_falls_back(monkeypatch) -> None:
    parity = _parity(calorie_usable=False)
    session = RecordingSession()

    result = _run(monkeypatch, session, parity)

    assert result.state is canonical.DailyCanonicalState.FALLBACK
    assert result.reason == "projection_no_primary_values"
    assert result.points is None
    assert session.execute_count == 0
    assert session.commit_count == 0
    assert session.rollback_count == 0


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
