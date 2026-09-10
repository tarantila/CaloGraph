from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from decimal import Decimal
from threading import Barrier, Event, Lock, Thread
from uuid import uuid4

import pytest
from sqlalchemy import func, select

import app.nutrition.projection.orchestration as orchestration
from app.database import SessionLocal, engine
from app.models import (
    NutritionDailyProjection,
    NutritionProjectionHead,
    User,
    YazioConnection,
)
from app.nutrition.models import NutritionSourceObservation
from app.nutrition.projection import ProjectionPersistenceStatus, rebuild_nutrition_day
from app.nutrition.resolution.metrics import CANONICAL_METRICS
from app.services.yazio_nutrition_ingestion import ingest_yazio_food_diary
from app.services.yazio_provider import (
    YazioDailyNutrientSummary,
    YazioFoodDiary,
    YazioNutrientValues,
)
from app.source_priority.application import create_policy_with_rules
from app.source_priority.contracts import PriorityRuleSpec

POSTGRES_TESTS_ENABLED = (
    os.environ.get("CALOGRAPH_ALLOW_DESTRUCTIVE_POSTGRES_TESTS") == "1"
    and bool(os.environ.get("CALOGRAPH_POSTGRES_TEST_URL"))
)
pytestmark = pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL orchestration tests are not explicitly enabled",
)
DAY = date(2026, 9, 11)
POLICY_V1_AT = datetime(2026, 1, 1, tzinfo=UTC)
POLICY_V2_AT = datetime(2026, 2, 1, tzinfo=UTC)


def _create_user() -> User:
    with SessionLocal() as db:
        user = User(username=f"b6-postgres-{uuid4()}", password_hash="hash", timezone="UTC")
        db.add(user)
        db.flush()
        db.commit()
        db.refresh(user)
        return user


def _seed_day(*, policy_versions: int = 1) -> User:
    user = _create_user()
    with SessionLocal() as db:
        connection = YazioConnection(
            user_id=user.id,
            encrypted_email=b"encrypted-email",
            encrypted_password=b"encrypted-password",
            source_identifier="b6-postgres-source",
        )
        db.add(connection)
        db.flush()
        nutrients = YazioNutrientValues(
            energy=Decimal("2100"),
            protein=Decimal("120"),
            carb=Decimal("230"),
            fat=Decimal("70"),
            fiber=Decimal("30"),
            sugar=Decimal("50"),
            saturated_fat=Decimal("20"),
        )
        ingest_yazio_food_diary(
            db,
            user_id=user.id,
            source_instance_id=connection.id,
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
                        nutrients=nutrients,
                        energy_goal=None,
                    ),
                ),
            ),
        )
        create_policy_with_rules(
            db,
            user.id,
            version=1,
            effective_from=POLICY_V1_AT,
            rules=(PriorityRuleSpec("nutrition", None, "yazio", 1),),
        )
        if policy_versions == 2:
            create_policy_with_rules(
                db,
                user.id,
                version=2,
                effective_from=POLICY_V2_AT,
                rules=(PriorityRuleSpec("nutrition", None, "yazio", 1),),
            )
        db.commit()
    return user


def _gate_first_two_persistence_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    original = orchestration.persist_daily_projection
    barrier = Barrier(2)
    counter = 0
    counter_lock = Lock()

    def synchronized(db, *, build_input):
        nonlocal counter
        with counter_lock:
            counter += 1
            ordinal = counter
        if ordinal <= 2:
            barrier.wait(timeout=30)
        return original(db, build_input=build_input)

    monkeypatch.setattr(orchestration, "persist_daily_projection", synchronized)


def test_b6_postgres_same_input_stale_snapshot_retries_to_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert engine.dialect.name == "postgresql"
    user = _seed_day()
    _gate_first_two_persistence_calls(monkeypatch)

    def build() -> ProjectionPersistenceStatus:
        with SessionLocal() as db:
            return rebuild_nutrition_day(
                db,
                user_id=user.id,
                local_date=DAY,
                policy_at=POLICY_V1_AT,
            ).status

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: build(), range(2)))

    assert sorted(results, key=str) == sorted(
        (ProjectionPersistenceStatus.CREATED, ProjectionPersistenceStatus.UNCHANGED),
        key=str,
    )
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(NutritionDailyProjection)) == 1
        assert db.scalar(select(func.count()).select_from(NutritionProjectionHead)) == 1


def test_b6_postgres_different_input_stale_snapshot_retries_to_version_two(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert engine.dialect.name == "postgresql"
    user = _seed_day(policy_versions=2)
    _gate_first_two_persistence_calls(monkeypatch)

    def build(policy_at: datetime) -> int | None:
        with SessionLocal() as db:
            return rebuild_nutrition_day(
                db,
                user_id=user.id,
                local_date=DAY,
                policy_at=policy_at,
            ).projection_version

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(build, (POLICY_V1_AT, POLICY_V2_AT)))

    assert sorted(results) == [1, 2]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(NutritionDailyProjection)) == 2
        assert db.scalar(select(func.count()).select_from(NutritionProjectionHead)) == 1


def test_b6_postgres_uses_one_snapshot_for_all_provider_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert engine.dialect.name == "postgresql"
    user = _seed_day()
    first_metric_read = Event()
    allow_projection_to_continue = Event()
    writer_errors: list[BaseException] = []
    original_collect = orchestration.collect_provider_candidates

    def collect_with_snapshot_gate(db, **kwargs):
        result = original_collect(db, **kwargs)
        if kwargs["metric_key"] == next(iter(CANONICAL_METRICS)):
            first_metric_read.set()
            assert allow_projection_to_continue.wait(timeout=30)
        return result

    def update_source() -> None:
        try:
            assert first_metric_read.wait(timeout=30)
            with SessionLocal() as writer:
                source = writer.scalar(select(NutritionSourceObservation))
                assert source is not None
                source.observation_fingerprint = "c" * 64
                writer.commit()
        except BaseException as error:
            writer_errors.append(error)
        finally:
            allow_projection_to_continue.set()

    monkeypatch.setattr(orchestration, "collect_provider_candidates", collect_with_snapshot_gate)
    writer = Thread(target=update_source)
    writer.start()
    with SessionLocal() as db:
        first = rebuild_nutrition_day(
            db,
            user_id=user.id,
            local_date=DAY,
            policy_at=POLICY_V1_AT,
        )
    writer.join(timeout=30)

    assert not writer.is_alive()
    assert writer_errors == []
    assert first.projection_version == 1

    with SessionLocal() as db:
        second = rebuild_nutrition_day(
            db,
            user_id=user.id,
            local_date=DAY,
            policy_at=POLICY_V1_AT,
        )

    assert second.projection_version == 2
