from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import conftest
from app.database import SessionLocal, engine
from app.models import GoogleHealthConnection, User, YazioConnection
from app.schemas_source_priority import NutritionPriorityUpdateRequest
from app.source_priority.models import SourcePriorityPolicy, SourcePriorityRule
from app.source_priority.public import StalePolicyConflict, update_nutrition_priority


POSTGRES_TESTS_ENABLED = (
    os.environ.get("CALOGRAPH_ALLOW_DESTRUCTIVE_POSTGRES_TESTS") == "1"
    and bool(os.environ.get("CALOGRAPH_POSTGRES_TEST_URL"))
)
GOOGLE_SCOPE = "https://www.googleapis.com/auth/googlehealth.nutrition.readonly"


def _add_connections(db: Session, user: User) -> None:
    db.add(
        YazioConnection(
            user_id=user.id,
            encrypted_email=b"encrypted-email",
            encrypted_password=b"encrypted-password",
            source_identifier=f"yazio-{uuid4()}",
        )
    )
    db.add(
        GoogleHealthConnection(
            user_id=user.id,
            encrypted_refresh_token=b"encrypted-refresh-token",
            granted_scopes=[GOOGLE_SCOPE],
            state="active",
        )
    )
    db.commit()


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL update concurrency tests are not explicitly enabled",
)
def test_postgres_concurrent_updates_serialize_and_preserve_prior_policy(user) -> None:
    conftest.assert_safe_test_database()
    assert engine.dialect.name == "postgresql"
    with SessionLocal() as db:
        _add_connections(db, user)

    with SessionLocal() as seed:
        initial, changed = update_nutrition_priority(
            seed,
            user.id,
            NutritionPriorityUpdateRequest(
                expected_version=None,
                source_order=["yazio", "google_health"],
            ),
        )
        assert changed is True
        assert initial.version == 1

    barrier = Barrier(2)

    def attempt(order: list[str]):
        with SessionLocal() as db:
            barrier.wait(timeout=30)
            try:
                result = update_nutrition_priority(
                    db,
                    user.id,
                    NutritionPriorityUpdateRequest(expected_version=1, source_order=order),
                )
                db.execute(select(User).where(User.id == user.id))
                return ("ok", result)
            except StalePolicyConflict as exc:
                db.execute(select(User).where(User.id == user.id))
                return ("stale", exc.current_version)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                attempt,
                (["google_health", "yazio"], ["yazio", "google_health"]),
            )
        )

    assert [result[0] for result in results].count("ok") == 1
    assert [result[0] for result in results].count("stale") == 1
    winner = next(result for result in results if result[0] == "ok")
    assert winner[1][0].version == 2
    assert winner[1][1] is True
    loser = next(result for result in results if result[0] == "stale")
    assert loser[1] == 2

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
                .order_by(SourcePriorityRule.priority_rank, SourcePriorityRule.id)
            )
        )
        assert [policy.version for policy in policies] == [1, 2]
        rules_by_policy = {
            policy.id: [(rule.provider_key, rule.priority_rank) for rule in rules if rule.policy_id == policy.id]
            for policy in policies
        }
        assert rules_by_policy[policies[0].id] == [("yazio", 1), ("google_health", 2)]
        assert rules_by_policy[policies[1].id] == [("google_health", 1), ("yazio", 2)]
        check.execute(select(User).where(User.id == user.id))
