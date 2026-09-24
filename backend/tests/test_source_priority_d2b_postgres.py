from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from threading import Barrier, Event, Lock
from uuid import uuid4

import conftest
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.nutrition.projection.orchestration as orchestration
from app.database import SessionLocal, engine
from app.models import (
    GoogleHealthConnection,
    NutritionDailyProjection,
    NutritionProjectionHead,
    User,
    YazioConnection,
)
from app.nutrition.projection.refresh import refresh_stale_nutrition_projections
from app.schemas_source_priority import NutritionPriorityUpdateRequest
from app.services.credential_crypto import encrypt_credential
from app.services.yazio_nutrition_ingestion import ingest_yazio_food_diary
from app.services.yazio_provider import (
    YazioDailyNutrientSummary,
    YazioFoodDiary,
    YazioNutrientValues,
)
from app.source_priority.application import create_policy_with_rules
from app.source_priority.contracts import PriorityRuleSpec
from app.source_priority.models import SourcePriorityPolicy, SourcePriorityRule
from app.source_priority.public import StalePolicyConflict, update_nutrition_priority

POSTGRES_TESTS_ENABLED = os.environ.get(
    "CALOGRAPH_ALLOW_DESTRUCTIVE_POSTGRES_TESTS"
) == "1" and bool(os.environ.get("CALOGRAPH_POSTGRES_TEST_URL"))
GOOGLE_SCOPE = "https://www.googleapis.com/auth/googlehealth.nutrition.readonly"

DAY = date(2026, 9, 11)


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
            client_id="source-priority-d2b-client",
            encrypted_client_secret=encrypt_credential("source-priority-d2b-client-secret"),
            user_id=user.id,
            encrypted_refresh_token=b"encrypted-refresh-token",
            granted_scopes=[GOOGLE_SCOPE],
            state="active",
        )
    )
    db.commit()


def _seed_refresh_day(user: User) -> None:
    with SessionLocal() as db:
        _add_connections(db, user)
        yazio = db.scalar(select(YazioConnection).where(YazioConnection.user_id == user.id))
        assert yazio is not None
        ingest_yazio_food_diary(
            db,
            user_id=user.id,
            source_instance_id=yazio.id,
            requested_start=DAY,
            requested_end=DAY,
            diary=YazioFoodDiary(
                requested_start_day=DAY,
                requested_end_day=DAY,
                consumed_products=(),
                consumed_simple_products=(),
                product_profiles=(),
                daily_summaries=(
                    YazioDailyNutrientSummary(
                        local_date=DAY,
                        nutrients=YazioNutrientValues(
                            energy=Decimal("2100"),
                            protein=Decimal("120"),
                            carb=Decimal("230"),
                            fat=Decimal("70"),
                            fiber=Decimal("30"),
                            sugar=Decimal("50"),
                            saturated_fat=Decimal("20"),
                        ),
                        energy_goal=None,
                    ),
                ),
            ),
        )
        create_policy_with_rules(
            db,
            user.id,
            version=1,
            effective_from=datetime.now(UTC) - timedelta(minutes=2),
            rules=(
                PriorityRuleSpec("nutrition", None, "yazio", 1),
                PriorityRuleSpec("nutrition", None, "google_health", 2),
            ),
        )
        db.commit()


def _gate_first_two_persistence_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    original = orchestration.persist_daily_projection
    barrier = Barrier(2)
    counter_lock = Lock()
    calls = 0

    def synchronized(db, *, build_input):
        nonlocal calls
        with counter_lock:
            calls += 1
            ordinal = calls
        if ordinal <= 2:
            barrier.wait(timeout=30)
        return original(db, build_input=build_input)

    monkeypatch.setattr(orchestration, "persist_daily_projection", synchronized)


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
                (["google_health", "yazio"], ["google_health", "yazio"]),
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
            policy.id: [
                (rule.provider_key, rule.priority_rank)
                for rule in rules
                if rule.policy_id == policy.id
            ]
            for policy in policies
        }
        assert rules_by_policy[policies[0].id] == [("yazio", 1), ("google_health", 2)]
        assert rules_by_policy[policies[1].id] == [("google_health", 1), ("yazio", 2)]
        check.execute(select(User).where(User.id == user.id))


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL refresh concurrency tests are not explicitly enabled",
)
def test_postgres_concurrent_refreshes_under_one_policy_leave_one_fresh_head(
    user, monkeypatch: pytest.MonkeyPatch
) -> None:
    conftest.assert_safe_test_database()
    assert engine.dialect.name == "postgresql"
    _seed_refresh_day(user)
    _gate_first_two_persistence_calls(monkeypatch)

    def refresh():
        return refresh_stale_nutrition_projections(
            session_factory=SessionLocal,
            user_id=user.id,
            expected_version=1,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: refresh(), range(2)))

    assert [result.processed_count for result in results] == [1, 1]
    assert sorted(result.created_count for result in results) == [0, 1]
    assert sorted(result.unchanged_count for result in results) == [0, 1]
    assert all(result.projection_refresh_required is False for result in results)
    with SessionLocal() as db:
        projections = list(
            db.scalars(
                select(NutritionDailyProjection).where(
                    NutritionDailyProjection.user_id == user.id,
                    NutritionDailyProjection.local_date == DAY,
                )
            )
        )
        head = db.get(NutritionProjectionHead, (user.id, DAY))
        assert len(projections) == 1
        assert head is not None
        assert head.current_projection_id == projections[0].id
        assert projections[0].projection_status == "ready"


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL refresh concurrency tests are not explicitly enabled",
)
def test_postgres_policy_put_during_refresh_requires_next_policy_batch(
    user, monkeypatch: pytest.MonkeyPatch
) -> None:
    conftest.assert_safe_test_database()
    assert engine.dialect.name == "postgresql"
    _seed_refresh_day(user)

    import app.nutrition.projection.refresh as refresh_module

    started = Event()
    release = Event()
    original_rebuild = refresh_module.rebuild_nutrition_day

    def gated_rebuild(*args, **kwargs):
        started.set()
        assert release.wait(timeout=30)
        return original_rebuild(*args, **kwargs)

    monkeypatch.setattr(refresh_module, "rebuild_nutrition_day", gated_rebuild)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            refresh_stale_nutrition_projections,
            session_factory=SessionLocal,
            user_id=user.id,
            expected_version=1,
        )
        assert started.wait(timeout=30)
        with SessionLocal() as db:
            state, changed = update_nutrition_priority(
                db,
                user.id,
                NutritionPriorityUpdateRequest(
                    expected_version=1,
                    source_order=["google_health", "yazio"],
                ),
            )
        assert changed is True
        assert state.version == 2
        release.set()
        first = future.result(timeout=60)

    assert first.policy_version == 1
    assert first.processed_count == 1
    assert first.created_count == 1
    assert first.projection_refresh_required is True
    with SessionLocal() as db:
        policies = list(
            db.scalars(
                select(SourcePriorityPolicy)
                .where(SourcePriorityPolicy.user_id == user.id)
                .order_by(SourcePriorityPolicy.version)
            )
        )
        projections = list(
            db.scalars(
                select(NutritionDailyProjection).where(
                    NutritionDailyProjection.user_id == user.id,
                    NutritionDailyProjection.local_date == DAY,
                )
            )
        )
        assert [policy.version for policy in policies] == [1, 2]
        assert len(projections) == 1
        assert projections[0].priority_policy_id == policies[0].id
    second = refresh_stale_nutrition_projections(
        session_factory=SessionLocal,
        user_id=user.id,
        expected_version=2,
    )
    assert second.policy_version == 2
    assert second.processed_count == 1
    assert second.created_count == 1
    assert second.projection_refresh_required is False
    with SessionLocal() as db:
        policies = list(
            db.scalars(
                select(SourcePriorityPolicy)
                .where(SourcePriorityPolicy.user_id == user.id)
                .order_by(SourcePriorityPolicy.version)
            )
        )
        projections = list(
            db.scalars(
                select(NutritionDailyProjection)
                .where(
                    NutritionDailyProjection.user_id == user.id,
                    NutritionDailyProjection.local_date == DAY,
                )
                .order_by(NutritionDailyProjection.projection_version)
            )
        )
        head = db.get(NutritionProjectionHead, (user.id, DAY))
        assert [policy.version for policy in policies] == [1, 2]
        assert [projection.priority_policy_id for projection in projections] == [
            policies[0].id,
            policies[1].id,
        ]
        assert head is not None
        current = db.get(NutritionDailyProjection, head.current_projection_id)
        assert current is not None
        assert current.priority_policy_id == policies[1].id


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL transaction visibility tests are not explicitly enabled",
)
def test_postgres_complete_policy_is_visible_only_after_commit(user) -> None:
    conftest.assert_safe_test_database()
    assert engine.dialect.name == "postgresql"
    effective_from = datetime(2026, 9, 12, tzinfo=UTC)

    with SessionLocal() as writer, SessionLocal() as reader:
        snapshot = create_policy_with_rules(
            writer,
            user.id,
            version=1,
            effective_from=effective_from,
            rules=(
                PriorityRuleSpec("nutrition", None, "yazio", 1),
                PriorityRuleSpec("nutrition", None, "google_health", 2),
            ),
        )
        assert (
            reader.scalar(
                select(SourcePriorityPolicy).where(SourcePriorityPolicy.id == snapshot.policy_id)
            )
            is None
        )
        assert (
            reader.scalar(
                select(SourcePriorityRule).where(SourcePriorityRule.policy_id == snapshot.policy_id)
            )
            is None
        )

        writer.commit()
        reader.rollback()
        policy = reader.scalar(
            select(SourcePriorityPolicy).where(SourcePriorityPolicy.id == snapshot.policy_id)
        )
        rules = list(
            reader.scalars(
                select(SourcePriorityRule)
                .where(SourcePriorityRule.policy_id == snapshot.policy_id)
                .order_by(SourcePriorityRule.priority_rank)
            )
        )

    assert policy is not None
    assert policy.version == 1
    assert [(rule.provider_key, rule.priority_rank) for rule in rules] == [
        ("yazio", 1),
        ("google_health", 2),
    ]
