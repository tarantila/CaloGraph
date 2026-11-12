from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event

import pytest
import test_source_priority_d2a as d2a
from sqlalchemy import select

from app.database import SessionLocal, engine
from app.models import User
from app.schemas_source_priority import NutritionPriorityUpdateRequest
from app.source_priority.bootstrap import (
    NutritionPriorityBootstrapStatus,
    bootstrap_nutrition_priority,
)
from app.source_priority.models import SourcePriorityPolicy, SourcePriorityRule
from app.source_priority.public import StalePolicyConflict, update_nutrition_priority

POSTGRES_TESTS_ENABLED = (
    os.environ.get("CALOGRAPH_ALLOW_DESTRUCTIVE_POSTGRES_TESTS") == "1"
    and bool(os.environ.get("CALOGRAPH_POSTGRES_TEST_URL"))
)


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL bootstrap race tests are not explicitly enabled",
)
def test_bootstrap_postgres_race_has_one_v1_and_closes_both_sessions(
    db,
    user: User,
) -> None:
    assert engine.dialect.name == "postgresql"
    d2a._add_yazio(db, user)
    barrier = Barrier(2)
    sessions: list[d2a._TrackedSession] = []

    def attempt() -> object:
        barrier.wait(timeout=30)
        return bootstrap_nutrition_priority(
            session_factory=lambda: d2a._tracked_session_factory(sessions),
            user_id=user.id,
            effective_from=d2a.BOOTSTRAP_AT,
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
    reason="isolated PostgreSQL bootstrap/update race tests are not explicitly enabled",
)
def test_bootstrap_and_explicit_update_race_leave_complete_policy_versions(
    db,
    user: User,
) -> None:
    assert engine.dialect.name == "postgresql"
    d2a._add_yazio(db, user)
    barrier = Barrier(2)

    def run_bootstrap():
        barrier.wait(timeout=30)
        return bootstrap_nutrition_priority(
            session_factory=SessionLocal,
            user_id=user.id,
            effective_from=d2a.BOOTSTRAP_AT,
        )

    def run_explicit_update():
        barrier.wait(timeout=30)
        with SessionLocal() as explicit_db:
            try:
                return update_nutrition_priority(
                    explicit_db,
                    user.id,
                    NutritionPriorityUpdateRequest(
                        expected_version=None,
                        source_order=["yazio"],
                    ),
                )
            except StalePolicyConflict:
                # Bootstrap may commit v1 first, making the no-existing-version
                # expectation stale before the explicit update acquires its lock.
                return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        bootstrap_result, explicit_result = list(
            executor.map(lambda operation: operation(), (run_bootstrap, run_explicit_update))
        )

    assert bootstrap_result.status in (
        NutritionPriorityBootstrapStatus.CREATED,
        NutritionPriorityBootstrapStatus.EXISTING_POLICY,
        NutritionPriorityBootstrapStatus.CONFIGURATION_REQUIRED,
    )
    if explicit_result is None:
        assert bootstrap_result.status is NutritionPriorityBootstrapStatus.CREATED
    else:
        assert explicit_result[0].version in (1, 2)
    with SessionLocal() as check:
        policies = list(
            check.scalars(
                select(SourcePriorityPolicy)
                .where(SourcePriorityPolicy.user_id == user.id)
                .order_by(SourcePriorityPolicy.version)
            )
        )
        rules = list(
            check.scalars(
                select(SourcePriorityRule)
                .where(SourcePriorityRule.user_id == user.id)
                .order_by(SourcePriorityRule.priority_rank)
            )
        )

    assert [policy.version for policy in policies] in ([1], [1, 2])
    if explicit_result is None:
        assert [policy.version for policy in policies] == [1]
    assert len(rules) == len(policies)
    rules_by_policy = {
        policy.id: [
            (rule.provider_key, rule.priority_rank) for rule in rules if rule.policy_id == policy.id
        ]
        for policy in policies
    }
    assert all(rules_by_policy[policy.id] == [("yazio", 1)] for policy in policies)


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL bootstrap race tests are not explicitly enabled",
)
def test_bootstrap_postgres_earlier_effective_loser_is_existing_policy(
    db,
    user: User,
) -> None:
    assert engine.dialect.name == "postgresql"
    d2a._add_yazio(db, user)
    early = d2a.BOOTSTRAP_AT
    late = d2a.BOOTSTRAP_AT + timedelta(days=1)
    late_started = Event()
    late_committed = Event()
    sessions: list[d2a._TrackedSession] = []

    def attempt(effective_from):
        def on_commit() -> None:
            if effective_from == late:
                late_committed.set()

        if effective_from == late:
            late_started.set()
        else:
            assert late_started.wait(timeout=30)
            assert late_committed.wait(timeout=30)
        return bootstrap_nutrition_priority(
            session_factory=lambda: d2a._tracked_session_factory(
                sessions,
                on_commit=on_commit,
            ),
            user_id=user.id,
            effective_from=effective_from,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, (early, late)))

    assert sorted(result.status for result in results) == [
        NutritionPriorityBootstrapStatus.CONFIGURATION_REQUIRED,
        NutritionPriorityBootstrapStatus.CREATED,
    ]
    winner = next(
        result for result in results if result.status is NutritionPriorityBootstrapStatus.CREATED
    )
    loser = next(
        result
        for result in results
        if result.status is NutritionPriorityBootstrapStatus.CONFIGURATION_REQUIRED
    )
    assert loser.policy_id == winner.policy_id
    assert loser.policy_version == winner.policy_version == 1
    assert all(session.closed for session in sessions)
