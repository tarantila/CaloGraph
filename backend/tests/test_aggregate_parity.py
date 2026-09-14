from __future__ import annotations

import inspect
from datetime import UTC, date, datetime
from decimal import Decimal
from types import MappingProxyType
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import User
from app.nutrition.models import (
    NutritionConsumptionEvent,
    NutritionDailyProjection,
    NutritionIngestionRun,
    NutritionProjectionHead,
    NutritionSourceObservation,
)
from app.source_priority.application import create_policy_with_rules
from app.source_priority.contracts import PriorityRuleSpec
from app.analytics.aggregate_parity import (
    CANONICAL_HISTORY_CHUNK_SIZE,
    CanonicalHistorySummary,
    DataQualityParity,
    DataQualityParityClassification,
    HistoricalBudgetBalanceClassification,
    HistoricalBudgetBalanceParity,
    MovingAverageParity,
    MovingAverageParityClassification,
    MovingAverageRangeParity,
    iter_canonical_history_date_chunks,
)


_DATE_1 = date(2026, 1, 1)
_DATE_2 = date(2026, 1, 2)
_DATE_3 = date(2026, 1, 3)
_DATE_4 = date(2026, 1, 4)
def _run(db: Session, user: User) -> NutritionIngestionRun:
    run = NutritionIngestionRun(
        user_id=user.id,
        provider_key="test-provider",
        source_instance_id=uuid4(),
        status="completed",
    )
    db.add(run)
    db.flush()
    return run


def _observation(
    db: Session,
    user: User,
    local_date: date | None,
    *,
    run: NutritionIngestionRun | None = None,
) -> NutritionSourceObservation:
    run = run or _run(db, user)
    observation = NutritionSourceObservation(
        user_id=user.id,
        ingestion_run_id=run.id,
        provider_key="test-provider",
        source_instance_id=run.source_instance_id,
        observation_kind="consumption_event",
        source_namespace="tests",
        source_record_id=None,
        source_revision=1,
        observation_fingerprint=(uuid4().hex + uuid4().hex)[:64],
        local_date=local_date,
        presence_state="supplied",
        coverage_state="complete",
        resolution_state="resolved",
        lineage_state="unknown",
    )
    db.add(observation)
    db.flush()
    return observation


def _event(db: Session, user: User, local_date: date, observation: NutritionSourceObservation) -> None:
    db.add(
        NutritionConsumptionEvent(
            user_id=user.id,
            source_observation_id=observation.id,
            provider_key="test-provider",
            source_instance_id=observation.source_instance_id,
            event_kind="product",
            logical_event_key=f"event-{uuid4()}",
            revision=1,
            local_date=local_date,
            presence_state="supplied",
            coverage_state="complete",
            resolution_state="resolved",
            lineage_state="unknown",
        )
    )
    db.flush()


def _projection_head(db: Session, user: User, local_date: date) -> None:
    policy = create_policy_with_rules(
        db,
        user.id,
        version=1,
        effective_from=datetime(2025, 1, 1, tzinfo=UTC),
        rules=(
            PriorityRuleSpec(
                data_area="nutrition",
                metric_key=None,
                provider_key="yazio",
                priority_rank=1,
            ),
        ),
    )
    projection = NutritionDailyProjection(
        user_id=user.id,
        local_date=local_date,
        projection_version=1,
        projection_algorithm_version="nutrition-daily-v1",
        priority_policy_id=policy.policy_id,
        input_watermark="aggregate-parity-test",
        projection_status="ready",
    )
    db.add(projection)
    db.flush()
    db.add(
        NutritionProjectionHead(
            user_id=user.id,
            local_date=local_date,
            current_projection_id=projection.id,
        )
    )
    db.flush()


def test_canonical_history_union_is_scoped_bounded_and_keyset_paginated(
    db: Session, user: User
) -> None:
    assert CANONICAL_HISTORY_CHUNK_SIZE == 500
    run = _run(db, user)
    observation = _observation(db, user, _DATE_1, run=run)
    _observation(db, user, None, run=run)
    _event(db, user, _DATE_2, observation)
    _projection_head(db, user, _DATE_3)
    _event(db, user, _DATE_1, observation)  # duplicate date across sources

    other = User(username="aggregate-parity-other", password_hash="synthetic-password-hash")
    db.add(other)
    db.flush()
    other_run = _run(db, other)
    other_observation = _observation(db, other, _DATE_4, run=other_run)
    _event(db, other, _DATE_4, other_observation)
    _projection_head(db, other, _DATE_4)

    chunks = tuple(
        iter_canonical_history_date_chunks(
            db,
            user.id,
            start_date=_DATE_1,
            end_date=_DATE_3,
            chunk_size=2,
        )
    )

    assert chunks == ((_DATE_1, _DATE_2), (_DATE_3,))
    assert all(len(chunk) <= 2 for chunk in chunks)


def test_canonical_history_empty_result_is_safe(db: Session, user: User) -> None:
    assert tuple(iter_canonical_history_date_chunks(db, user.id)) == ()


@pytest.mark.parametrize("chunk_size", (0, -1, True, 1.5))
def test_canonical_history_rejects_invalid_chunk_sizes(
    db: Session, user: User, chunk_size: object
) -> None:
    with pytest.raises(ValueError):
        iter_canonical_history_date_chunks(db, user.id, chunk_size=chunk_size)  # type: ignore[arg-type]


def test_canonical_history_rejects_reversed_or_invalid_date_bounds(
    db: Session, user: User
) -> None:
    with pytest.raises(ValueError):
        iter_canonical_history_date_chunks(db, user.id, start_date=_DATE_2, end_date=_DATE_1)
    with pytest.raises(ValueError):
        iter_canonical_history_date_chunks(
            db,
            user.id,
            start_date=datetime(2026, 1, 1),  # type: ignore[arg-type]
        )


def test_aggregate_contracts_are_frozen_slotted_and_identifier_free() -> None:
    moving = MovingAverageParity(
        local_date=_DATE_1,
        window=7,
        legacy_value=Decimal("1"),
        canonical_value=Decimal("2"),
        classification=MovingAverageParityClassification.UNEXPLAINED_MISMATCH,
    )
    moving_range = MovingAverageRangeParity(_DATE_1, _DATE_3, (moving,))
    budget = HistoricalBudgetBalanceParity(
        legacy_counts={"tracked_days": 1},
        canonical_counts={"tracked_days": 2},
        classification=HistoricalBudgetBalanceClassification.UNEXPLAINED_MISMATCH,
    )
    quality = DataQualityParity(
        start_date=_DATE_1,
        end_date=_DATE_3,
        total_days=3,
        recorded_days=2,
        missing_days=1,
        incomplete_days=0,
        coverage_ratio=Decimal("0.6666666667"),
        comparable_days=2,
        comparable_coverage_ratio=Decimal("1"),
        not_comparable_days=0,
        classification=DataQualityParityClassification.MATCH,
    )
    history = CanonicalHistorySummary(_DATE_1, _DATE_3, 3, 1, 1, 0)

    for contract in (moving, moving_range, budget, quality, history):
        assert contract.__dataclass_params__.frozen is True
        assert contract.__slots__
        assert "user_id" not in contract.__slots__

    assert isinstance(moving_range.results, tuple)
    assert isinstance(budget.legacy_counts, MappingProxyType)
    assert isinstance(budget.canonical_counts, MappingProxyType)
    with pytest.raises(TypeError):
        budget.legacy_counts["tracked_days"] = 4  # type: ignore[index]
    with pytest.raises(Exception):
        moving_range.results += (moving,)  # type: ignore[misc]
    with pytest.raises(Exception):
        quality.total_days = 4  # type: ignore[misc]


def test_public_analytics_routes_do_not_import_internal_aggregate_module() -> None:
    import app.api.analytics as analytics_routes

    assert "aggregate_parity" not in inspect.getsource(analytics_routes)
