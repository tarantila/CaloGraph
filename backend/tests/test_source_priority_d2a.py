from __future__ import annotations

import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from threading import Barrier, Event
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

import app.source_priority.bootstrap as bootstrap_module
from app.database import SessionLocal, engine
from app.models import GoogleHealthConnection, User, YazioConnection
from app.nutrition.resolution.metrics import CANONICAL_METRICS
from app.source_priority.application import create_policy_with_rules
from app.source_priority.bootstrap import (
    NutritionPriorityBootstrapResult,
    NutritionPriorityBootstrapStatus,
    bootstrap_nutrition_priority,
)
from app.source_priority.contracts import PriorityRuleSpec
from app.source_priority.models import SourcePriorityPolicy, SourcePriorityRule

POSTGRES_TESTS_ENABLED = os.environ.get(
    "CALOGRAPH_ALLOW_DESTRUCTIVE_POSTGRES_TESTS"
) == "1" and bool(os.environ.get("CALOGRAPH_POSTGRES_TEST_URL"))

BOOTSTRAP_AT = datetime(2026, 9, 13, 12, tzinfo=UTC)

EXPECTED_PROVIDER_ORDER = ("google_health", "yazio")
GOOGLE_SCOPE = "https://www.googleapis.com/auth/googlehealth.nutrition.readonly"


def _bootstrap(user: User, *, effective_from: datetime = BOOTSTRAP_AT):
    return bootstrap_nutrition_priority(
        session_factory=SessionLocal,
        user_id=user.id,
        effective_from=effective_from,
    )


class _TrackedSession:
    def __init__(self, *, on_commit: Callable[[], None] | None = None) -> None:
        self.db = SessionLocal()
        self.closed = False
        self._on_commit = on_commit

    def __enter__(self):
        self.db.__enter__()
        return self

    def __getattr__(self, name: str):
        return getattr(self.db, name)

    def commit(self) -> None:
        self.db.commit()
        if self._on_commit is not None:
            self._on_commit()

    def __exit__(self, *args):
        try:
            return self.db.__exit__(*args)
        finally:
            self.closed = True


def _tracked_session_factory(
    sessions: list[_TrackedSession],
    *,
    on_commit: Callable[[], None] | None = None,
) -> _TrackedSession:
    session = _TrackedSession(on_commit=on_commit)
    sessions.append(session)
    return session


def _add_yazio(db, user: User) -> YazioConnection:
    connection = YazioConnection(
        user_id=user.id,
        encrypted_email=b"encrypted-email",
        encrypted_password=b"encrypted-password",
        source_identifier="yazio-account",
    )
    db.add(connection)
    db.commit()
    return connection


def _add_google(db, user: User) -> GoogleHealthConnection:
    connection = GoogleHealthConnection(
        user_id=user.id,
        encrypted_refresh_token=b"encrypted-refresh-token",
        granted_scopes=[GOOGLE_SCOPE],
        state="active",
    )
    db.add(connection)
    db.commit()
    return connection


def _policies(db, user: User) -> list[SourcePriorityPolicy]:
    db.expire_all()
    return list(
        db.scalars(
            select(SourcePriorityPolicy)
            .where(SourcePriorityPolicy.user_id == user.id)
            .order_by(SourcePriorityPolicy.version)
        )
    )


def _rules(db, user: User) -> list[SourcePriorityRule]:
    db.expire_all()
    return list(
        db.scalars(
            select(SourcePriorityRule)
            .where(SourcePriorityRule.user_id == user.id)
            .order_by(
                SourcePriorityRule.data_area,
                SourcePriorityRule.metric_key,
                SourcePriorityRule.provider_key,
            )
        )
    )


def _policy_snapshot(db, user: User) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            policy.id,
            policy.user_id,
            policy.version,
            policy.effective_from,
            policy.created_at,
        )
        for policy in _policies(db, user)
    )


def _rule_snapshot(db, user: User) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            rule.id,
            rule.user_id,
            rule.policy_id,
            rule.data_area,
            rule.metric_key,
            rule.provider_key,
            rule.priority_rank,
            rule.created_at,
        )
        for rule in _rules(db, user)
    )


def _create_existing_policy(
    db,
    user: User,
    *,
    rules: tuple[PriorityRuleSpec, ...],
    effective_from: datetime = BOOTSTRAP_AT,
    version: int = 1,
) -> SourcePriorityPolicy:
    snapshot = create_policy_with_rules(
        db,
        user.id,
        version,
        effective_from,
        rules,
    )
    db.commit()
    policy = db.get(SourcePriorityPolicy, snapshot.policy_id)
    assert policy is not None
    return policy


def _assert_utc_effective_from(
    policy: SourcePriorityPolicy,
    expected: datetime = BOOTSTRAP_AT,
) -> None:
    stored = policy.effective_from
    if stored.tzinfo is None:
        stored = stored.replace(tzinfo=UTC)
    assert stored == expected
    if policy.effective_from.tzinfo is not None:
        assert policy.effective_from.utcoffset() == timedelta(0)
    assert stored.utcoffset() == timedelta(0)


def _assert_no_policy_rows(db, user: User) -> None:
    assert _policies(db, user) == []
    assert _rules(db, user) == []


def test_bootstrap_result_is_frozen_slotted_and_zero_provider_state_is_empty(db, user) -> None:
    result = _bootstrap(user)

    assert isinstance(result, NutritionPriorityBootstrapResult)
    assert hasattr(result, "__slots__")
    assert not hasattr(result, "__dict__")
    assert result.status is NutritionPriorityBootstrapStatus.NO_PROVIDERS
    assert result.policy_id is None
    assert result.policy_version is None
    assert result.available_provider_keys == ()
    with pytest.raises(FrozenInstanceError):
        result.status = NutritionPriorityBootstrapStatus.CREATED
    _assert_no_policy_rows(db, user)


def test_bootstrap_creates_v1_wildcard_for_only_yazio(db, user) -> None:
    _add_yazio(db, user)

    result = _bootstrap(user)

    assert result.status is NutritionPriorityBootstrapStatus.CREATED
    assert result.policy_id is not None
    assert result.policy_version == 1
    assert result.available_provider_keys == ("yazio",)
    policies = _policies(db, user)
    rules = _rules(db, user)
    assert len(policies) == 1
    assert result.policy_id == policies[0].id
    assert policies[0].version == 1
    _assert_utc_effective_from(policies[0])
    assert len(rules) == 1
    assert (rules[0].data_area, rules[0].metric_key, rules[0].provider_key) == (
        "nutrition",
        None,
        "yazio",
    )
    assert rules[0].priority_rank == 1


def test_bootstrap_creates_v1_wildcard_for_only_google_health(db, user) -> None:
    _add_google(db, user)

    result = _bootstrap(user)

    assert result.status is NutritionPriorityBootstrapStatus.CREATED
    assert result.policy_id is not None
    assert result.policy_version == 1
    assert result.available_provider_keys == ("google_health",)
    policies = _policies(db, user)
    rules = _rules(db, user)
    assert len(policies) == 1
    assert result.policy_id == policies[0].id
    assert policies[0].version == 1
    _assert_utc_effective_from(policies[0])
    assert len(rules) == 1
    assert (rules[0].data_area, rules[0].metric_key, rules[0].provider_key) == (
        "nutrition",
        None,
        "google_health",
    )
    assert rules[0].priority_rank == 1


def test_bootstrap_normalizes_equivalent_non_utc_effective_from_to_utc(db, user) -> None:
    _add_yazio(db, user)
    local_effective = datetime(2026, 9, 13, 14, tzinfo=timezone(timedelta(hours=2)))

    result = _bootstrap(user, effective_from=local_effective)

    assert result.status is NutritionPriorityBootstrapStatus.CREATED
    assert result.policy_id is not None
    policies = _policies(db, user)
    assert len(policies) == 1
    assert policies[0].id == result.policy_id
    _assert_utc_effective_from(policies[0], BOOTSTRAP_AT)


def test_bootstrap_requires_selection_for_yazio_and_google_without_policy(db, user) -> None:
    _add_yazio(db, user)
    _add_google(db, user)

    result = _bootstrap(user)

    assert result.status is NutritionPriorityBootstrapStatus.SELECTION_REQUIRED
    assert result.policy_id is None
    assert result.policy_version is None
    assert result.available_provider_keys == EXPECTED_PROVIDER_ORDER
    _assert_no_policy_rows(db, user)


def test_bootstrap_classifies_existing_valid_wildcard_without_new_version(db, user) -> None:
    _add_yazio(db, user)
    _add_google(db, user)

    policy = _create_existing_policy(
        db,
        user,
        rules=(
            PriorityRuleSpec(
                data_area="nutrition",
                metric_key=None,
                provider_key="yazio",
                priority_rank=1,
            ),
            PriorityRuleSpec(
                data_area="nutrition",
                metric_key=None,
                provider_key="google_health",
                priority_rank=2,
            ),
        ),
    )

    result = _bootstrap(user)

    assert result.status is NutritionPriorityBootstrapStatus.EXISTING_POLICY
    assert result.policy_id == policy.id
    assert result.policy_version == 1
    assert result.available_provider_keys == EXPECTED_PROVIDER_ORDER
    assert [item.version for item in _policies(db, user)] == [1]
    assert [(item.provider_key, item.priority_rank) for item in _rules(db, user)] == [
        ("google_health", 2),
        ("yazio", 1),
    ]


def test_any_existing_version_two_policy_blocks_auto_v1(db, user) -> None:
    _add_yazio(db, user)
    policy = _create_existing_policy(
        db,
        user,
        version=2,
        rules=(
            PriorityRuleSpec(
                data_area="nutrition",
                metric_key=None,
                provider_key="yazio",
                priority_rank=1,
            ),
        ),
    )

    result = _bootstrap(user)

    assert result.status is NutritionPriorityBootstrapStatus.EXISTING_POLICY
    assert result.policy_id == policy.id
    assert result.policy_version == 2
    assert result.available_provider_keys == ("yazio",)
    assert [item.version for item in _policies(db, user)] == [2]
    assert len(_rules(db, user)) == 1


def test_future_dated_policy_blocks_auto_v1_and_requires_configuration(db, user) -> None:
    _add_yazio(db, user)
    future = BOOTSTRAP_AT + timedelta(days=1)
    policy = _create_existing_policy(
        db,
        user,
        effective_from=future,
        rules=(
            PriorityRuleSpec(
                data_area="nutrition",
                metric_key=None,
                provider_key="yazio",
                priority_rank=1,
            ),
        ),
    )

    result = _bootstrap(user)

    assert result.status is NutritionPriorityBootstrapStatus.CONFIGURATION_REQUIRED
    assert result.policy_id == policy.id
    assert result.policy_version == 1
    assert result.available_provider_keys == ("yazio",)
    assert [item.version for item in _policies(db, user)] == [1]
    assert len(_rules(db, user)) == 1


def test_other_area_policy_blocks_auto_v1_and_requires_configuration(db, user) -> None:
    _add_yazio(db, user)
    policy = _create_existing_policy(
        db,
        user,
        rules=(
            PriorityRuleSpec(
                data_area="activity",
                metric_key=None,
                provider_key="yazio",
                priority_rank=1,
            ),
        ),
    )

    result = _bootstrap(user)

    assert result.status is NutritionPriorityBootstrapStatus.CONFIGURATION_REQUIRED
    assert result.policy_id == policy.id
    assert result.policy_version == 1
    assert result.available_provider_keys == ("yazio",)
    assert [item.version for item in _policies(db, user)] == [1]
    assert [(item.data_area, item.metric_key) for item in _rules(db, user)] == [("activity", None)]


def test_incomplete_nutrition_policy_is_not_repaired(db, user) -> None:
    _add_yazio(db, user)
    policy = _create_existing_policy(
        db,
        user,
        rules=(
            PriorityRuleSpec(
                data_area="nutrition",
                metric_key="protein_g",
                provider_key="yazio",
                priority_rank=1,
            ),
        ),
    )

    result = _bootstrap(user)

    assert result.status is NutritionPriorityBootstrapStatus.CONFIGURATION_REQUIRED
    assert result.policy_id == policy.id
    assert result.policy_version == 1
    assert result.available_provider_keys == ("yazio",)
    assert [item.version for item in _policies(db, user)] == [1]
    assert [(item.data_area, item.metric_key, item.provider_key) for item in _rules(db, user)] == [
        ("nutrition", "protein_g", "yazio")
    ]


def test_metric_specific_rules_covering_every_canonical_metric_are_existing_policy(
    db, user
) -> None:
    _add_yazio(db, user)
    rules = tuple(
        PriorityRuleSpec(
            data_area="nutrition",
            metric_key=metric_key,
            provider_key="yazio",
            priority_rank=1,
        )
        for metric_key in CANONICAL_METRICS
    )
    policy = _create_existing_policy(db, user, rules=rules)

    result = _bootstrap(user)

    assert result.status is NutritionPriorityBootstrapStatus.EXISTING_POLICY
    assert result.policy_id == policy.id
    assert result.policy_version == 1
    assert result.available_provider_keys == ("yazio",)
    persisted_rules = _rules(db, user)
    assert len(persisted_rules) == len(CANONICAL_METRICS)
    assert {item.metric_key for item in persisted_rules} == set(CANONICAL_METRICS)
    assert all(
        item.data_area == "nutrition" and item.provider_key == "yazio" for item in persisted_rules
    )
    assert all(item.priority_rank == 1 for item in persisted_rules)


def test_near_complete_metric_specific_policy_requires_configuration_without_mutation(
    db, user
) -> None:
    _add_yazio(db, user)
    metric_keys = tuple(CANONICAL_METRICS)
    rules = tuple(
        PriorityRuleSpec(
            data_area="nutrition",
            metric_key=metric_key,
            provider_key="yazio",
            priority_rank=1,
        )
        for metric_key in metric_keys[:-1]
    )
    policy = _create_existing_policy(db, user, rules=rules)
    policies_before = _policy_snapshot(db, user)
    rules_before = _rule_snapshot(db, user)

    result = _bootstrap(user)

    assert result.status is NutritionPriorityBootstrapStatus.CONFIGURATION_REQUIRED
    assert result.policy_id == policy.id
    assert result.policy_version == 1
    assert result.available_provider_keys == ("yazio",)
    assert _policy_snapshot(db, user) == policies_before
    assert _rule_snapshot(db, user) == rules_before


def test_existing_yazio_policy_is_immutable_when_google_becomes_available(db, user) -> None:
    _add_yazio(db, user)
    first = _bootstrap(user)
    assert first.status is NutritionPriorityBootstrapStatus.CREATED
    policy_before = _policy_snapshot(db, user)
    rules_before = _rule_snapshot(db, user)
    _add_google(db, user)

    second = _bootstrap(user)

    assert second.status is NutritionPriorityBootstrapStatus.EXISTING_POLICY
    assert second.policy_id == first.policy_id == policy_before[0][0]
    assert second.policy_version == 1
    assert second.available_provider_keys == EXPECTED_PROVIDER_ORDER
    assert _policy_snapshot(db, user) == policy_before
    rules_after = _rule_snapshot(db, user)
    assert rules_after == rules_before
    assert all(row[5] != "google_health" for row in rules_after)


def test_existing_google_policy_is_immutable_when_yazio_becomes_available(db, user) -> None:
    _add_google(db, user)
    first = _bootstrap(user)
    assert first.status is NutritionPriorityBootstrapStatus.CREATED
    policy_before = _policy_snapshot(db, user)
    rules_before = _rule_snapshot(db, user)
    _add_yazio(db, user)

    second = _bootstrap(user)

    assert second.status is NutritionPriorityBootstrapStatus.EXISTING_POLICY
    assert second.policy_id == first.policy_id == policy_before[0][0]
    assert second.policy_version == 1
    assert second.available_provider_keys == EXPECTED_PROVIDER_ORDER
    assert _policy_snapshot(db, user) == policy_before
    rules_after = _rule_snapshot(db, user)
    assert rules_after == rules_before
    assert all(row[5] != "yazio" for row in rules_after)


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL bootstrap race tests are not explicitly enabled",
)
def test_bootstrap_postgres_race_has_one_v1_and_closes_both_sessions(
    db,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert engine.dialect.name == "postgresql"
    _add_yazio(db, user)

    original_create = bootstrap_module.create_policy_with_rules
    barrier = Barrier(2)

    def gated_create(*args, **kwargs):
        barrier.wait(timeout=30)
        return original_create(*args, **kwargs)

    monkeypatch.setattr(bootstrap_module, "create_policy_with_rules", gated_create)

    sessions: list[_TrackedSession] = []

    def session_factory() -> _TrackedSession:
        return _tracked_session_factory(sessions)

    def attempt() -> object:
        return bootstrap_nutrition_priority(
            session_factory=session_factory,
            user_id=user.id,
            effective_from=BOOTSTRAP_AT,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: attempt(), range(2)))

    assert sorted(result.status for result in results) == [
        NutritionPriorityBootstrapStatus.CREATED,
        NutritionPriorityBootstrapStatus.EXISTING_POLICY,
    ]
    winner = next(
        result for result in results if result.status is NutritionPriorityBootstrapStatus.CREATED
    )
    loser = next(
        result
        for result in results
        if result.status is NutritionPriorityBootstrapStatus.EXISTING_POLICY
    )
    assert loser.policy_id == winner.policy_id
    assert loser.policy_version == winner.policy_version == 1
    with SessionLocal() as check:
        policies = list(
            check.scalars(
                select(SourcePriorityPolicy).where(SourcePriorityPolicy.user_id == user.id)
            )
        )
        rules = list(
            check.scalars(select(SourcePriorityRule).where(SourcePriorityRule.user_id == user.id))
        )
    assert len(policies) == 1
    assert len(rules) == 1
    assert rules[0].policy_id == policies[0].id
    assert rules[0].provider_key == "yazio"
    assert all(session.closed for session in sessions)


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL bootstrap race tests are not explicitly enabled",
)
def test_bootstrap_postgres_earlier_effective_loser_is_existing_policy(
    db,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert engine.dialect.name == "postgresql"
    _add_yazio(db, user)
    early = BOOTSTRAP_AT
    late = BOOTSTRAP_AT + timedelta(days=1)
    original_create = bootstrap_module.create_policy_with_rules
    barrier = Barrier(2)
    later_committed = Event()

    def gated_create(db, user_id, version, effective_from, rules):
        barrier.wait(timeout=30)
        if effective_from == early:
            assert later_committed.wait(timeout=30)
        return original_create(db, user_id, version, effective_from, rules)

    monkeypatch.setattr(bootstrap_module, "create_policy_with_rules", gated_create)
    sessions: list[_TrackedSession] = []

    def attempt(effective_from: datetime):
        def on_commit() -> None:
            if effective_from == late:
                later_committed.set()

        return bootstrap_nutrition_priority(
            session_factory=lambda: _tracked_session_factory(
                sessions,
                on_commit=on_commit,
            ),
            user_id=user.id,
            effective_from=effective_from,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, (early, late)))

    assert sorted(result.status for result in results) == [
        NutritionPriorityBootstrapStatus.CREATED,
        NutritionPriorityBootstrapStatus.EXISTING_POLICY,
    ]
    winner = next(
        result for result in results if result.status is NutritionPriorityBootstrapStatus.CREATED
    )
    loser = next(
        result
        for result in results
        if result.status is NutritionPriorityBootstrapStatus.EXISTING_POLICY
    )
    assert loser.policy_id == winner.policy_id
    assert loser.policy_version == winner.policy_version == 1
    assert all(session.closed for session in sessions)


def test_bootstrap_propagates_unrelated_integrity_error_and_closes_session(
    db,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _add_yazio(db, user)
    error = IntegrityError(
        "INSERT",
        {},
        SimpleNamespace(
            diag=SimpleNamespace(constraint_name="some_other_constraint"),
        ),
    )

    def fail_create(*args, **kwargs):
        raise error

    monkeypatch.setattr(bootstrap_module, "create_policy_with_rules", fail_create)

    sessions: list[_TrackedSession] = []

    def session_factory() -> _TrackedSession:
        return _tracked_session_factory(sessions)

    with pytest.raises(IntegrityError) as raised:
        bootstrap_nutrition_priority(
            session_factory=session_factory,
            user_id=user.id,
            effective_from=BOOTSTRAP_AT,
        )
    assert raised.value is error
    assert all(session.closed for session in sessions)
