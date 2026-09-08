from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import (
    NutritionDailyProjection,
    NutritionDailyProjectionFact,
    NutritionDailyProjectionLineage,
    NutritionIngestionRun,
    NutritionProjectionHead,
    NutritionSourceObservation,
    User,
)
from app.nutrition.enums import (
    CoverageState,
    LineageState,
    ObservationKind,
    PresenceState,
    ProjectionGranularity,
    ProjectionLineageRole,
    ProjectionStatus,
    ResolutionState,
)
from app.nutrition.repositories import (
    create_projection,
    create_projection_fact,
    create_projection_lineage,
    get_current_projection,
    get_projection_by_version,
    set_projection_head,
)
from app.source_priority.repositories import create_policy

PROJECTION_DATE = date(2026, 9, 7)
INPUT_WATERMARK = "nutrition-input-2026-09-08T12:00:00Z"


def _other_user(db, username: str = "projection-other") -> User:
    other = User(username=username, password_hash="hash", timezone="UTC")
    db.add(other)
    db.flush()
    return other


def _policy(db, user: User, *, version: int = 1):
    return create_policy(
        db,
        user_id=user.id,
        version=version,
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _projection(
    db,
    user: User,
    policy,
    *,
    local_date: date = PROJECTION_DATE,
    version: int = 1,
    status: ProjectionStatus = ProjectionStatus.READY,
):
    return create_projection(
        db,
        user_id=user.id,
        local_date=local_date,
        projection_version=version,
        projection_algorithm_version="nutrition-a2-test",
        priority_policy_id=policy.id,
        input_watermark=INPUT_WATERMARK,
        projection_status=status.value,
    )


def _observation(db, user: User, *, suffix: str = "1"):
    run = NutritionIngestionRun(
        user_id=user.id,
        provider_key="yazio",
        source_instance_id=uuid4(),
        status="completed",
        coverage_state=CoverageState.COMPLETE.value,
    )
    db.add(run)
    db.flush()
    observation = NutritionSourceObservation(
        user_id=user.id,
        ingestion_run_id=run.id,
        provider_key=run.provider_key,
        source_instance_id=run.source_instance_id,
        observation_kind=ObservationKind.DAILY_SUMMARY.value,
        source_namespace="daily",
        source_revision=1,
        observation_fingerprint=(suffix * 64)[:64],
        local_date=PROJECTION_DATE,
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
        lineage_state=LineageState.CONFIRMED.value,
    )
    db.add(observation)
    db.flush()
    return observation


def _fact(
    db,
    user: User,
    projection,
    *,
    metric_key: str = "energy_kcal",
    value: Decimal | None = Decimal("2000.123456789012"),
    presence_state: PresenceState = PresenceState.SUPPLIED,
    coverage_state: CoverageState = CoverageState.COMPLETE,
    resolution_state: ResolutionState = ResolutionState.RESOLVED,
    lineage_state: LineageState = LineageState.CONFIRMED,
    granularity: ProjectionGranularity = ProjectionGranularity.SUMMARY,
):
    return create_projection_fact(
        db,
        user_id=user.id,
        projection_id=projection.id,
        metric_key=metric_key,
        value=value,
        unit="kcal",
        selected_provider_key="yazio",
        selected_granularity=granularity.value,
        presence_state=presence_state.value,
        coverage_state=coverage_state.value,
        resolution_state=resolution_state.value,
        lineage_state=lineage_state.value,
        diagnostic_metadata={"test": True},
    )


def test_projection_version_date_and_user_scope_are_unique(db, user):
    policy = _policy(db, user)
    first = _projection(db, user, policy)
    db.commit()

    duplicate = NutritionDailyProjection(
        user_id=user.id,
        local_date=PROJECTION_DATE,
        projection_version=1,
        projection_algorithm_version="duplicate",
        priority_policy_id=policy.id,
        input_watermark=INPUT_WATERMARK,
        projection_status=ProjectionStatus.READY.value,
    )
    db.add(duplicate)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    second_date = _projection(db, user, policy, local_date=date(2026, 9, 8))
    second_version = _projection(db, user, policy, version=2)
    other = _other_user(db)
    other_projection = _projection(db, other, _policy(db, other))
    db.commit()

    assert first.id != second_date.id
    assert second_version.projection_version == 2
    assert other_projection.user_id == other.id


def test_projection_version_must_be_positive(db, user):
    policy = _policy(db, user)
    db.add(
        NutritionDailyProjection(
            user_id=user.id,
            local_date=PROJECTION_DATE,
            projection_version=0,
            projection_algorithm_version="invalid",
            priority_policy_id=policy.id,
            input_watermark=INPUT_WATERMARK,
            projection_status=ProjectionStatus.READY.value,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()


def test_projection_policy_foreign_key_is_user_scoped(db, user):
    other = _other_user(db)
    policy = _policy(db, user)
    db.commit()
    db.add(
        NutritionDailyProjection(
            user_id=other.id,
            local_date=PROJECTION_DATE,
            projection_version=1,
            projection_algorithm_version="cross-user-policy",
            priority_policy_id=policy.id,
            input_watermark=INPUT_WATERMARK,
            projection_status=ProjectionStatus.READY.value,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()


def test_projection_fact_preserves_decimal_zero_null_and_independent_states(db, user):
    projection = _projection(db, user, _policy(db, user))
    supplied = _fact(db, user, projection)
    explicit_zero = _fact(
        db,
        user,
        projection,
        metric_key="protein_g",
        value=Decimal("0"),
        presence_state=PresenceState.EXPLICIT_ZERO,
        coverage_state=CoverageState.PARTIAL,
        resolution_state=ResolutionState.CONFLICT,
        lineage_state=LineageState.UNCERTAIN,
        granularity=ProjectionGranularity.PARTIAL_EVENT,
    )
    missing = _fact(
        db,
        user,
        projection,
        metric_key="fiber_g",
        value=None,
        presence_state=PresenceState.MISSING,
        coverage_state=CoverageState.UNKNOWN,
        resolution_state=ResolutionState.UNRESOLVED,
        lineage_state=LineageState.UNKNOWN,
        granularity=ProjectionGranularity.EVENT,
    )
    db.commit()
    db.expire_all()

    loaded = db.get(NutritionDailyProjectionFact, supplied.id)
    loaded_zero = db.get(NutritionDailyProjectionFact, explicit_zero.id)
    loaded_missing = db.get(NutritionDailyProjectionFact, missing.id)
    assert loaded is not None and loaded.value == Decimal("2000.123456789012")
    assert loaded.presence_state == PresenceState.SUPPLIED.value
    assert loaded.coverage_state == CoverageState.COMPLETE.value
    assert loaded.resolution_state == ResolutionState.RESOLVED.value
    assert loaded.lineage_state == LineageState.CONFIRMED.value
    assert loaded_zero is not None and loaded_zero.value == Decimal("0.000000000000")
    assert loaded_zero.presence_state == PresenceState.EXPLICIT_ZERO.value
    assert loaded_zero.coverage_state == CoverageState.PARTIAL.value
    assert loaded_zero.resolution_state == ResolutionState.CONFLICT.value
    assert loaded_zero.lineage_state == LineageState.UNCERTAIN.value
    assert loaded_zero.selected_granularity == ProjectionGranularity.PARTIAL_EVENT.value
    assert loaded_missing is not None and loaded_missing.value is None
    assert loaded_missing.presence_state == PresenceState.MISSING.value
    assert loaded_missing.coverage_state == CoverageState.UNKNOWN.value
    assert loaded_missing.resolution_state == ResolutionState.UNRESOLVED.value
    assert loaded_missing.lineage_state == LineageState.UNKNOWN.value
    assert loaded_missing.selected_granularity == ProjectionGranularity.EVENT.value


def test_projection_fact_metric_is_unique_per_projection_but_not_across_projections(db, user):
    policy = _policy(db, user)
    first = _projection(db, user, policy, version=1)
    second = _projection(db, user, policy, version=2)
    _fact(db, user, first)
    _fact(db, user, second)
    db.commit()

    db.add(
        NutritionDailyProjectionFact(
            user_id=user.id,
            projection_id=first.id,
            metric_key="energy_kcal",
            value=Decimal("2100"),
            unit="kcal",
            selected_provider_key="yazio",
            selected_granularity=ProjectionGranularity.SUMMARY.value,
            presence_state=PresenceState.SUPPLIED.value,
            coverage_state=CoverageState.COMPLETE.value,
            resolution_state=ResolutionState.RESOLVED.value,
            lineage_state=LineageState.CONFIRMED.value,
            diagnostic_metadata={},
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()


def test_projection_lineage_roles_and_same_user_source_fk(db, user):
    other = _other_user(db)
    projection = _projection(db, user, _policy(db, user))
    other_projection = _projection(db, other, _policy(db, other))
    fact = _fact(db, user, projection)
    other_observation = _observation(db, other, suffix="2")
    observation = _observation(db, user, suffix="3")
    db.commit()

    lineage = create_projection_lineage(
        db,
        user_id=user.id,
        projection_fact_id=fact.id,
        source_observation_id=observation.id,
        provider_key="yazio",
        role=ProjectionLineageRole.SELECTED.value,
        granularity=ProjectionGranularity.SUMMARY.value,
        contribution_value=Decimal("2000.123456789012"),
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        reason_code="selected-source",
        metadata={"test": True},
    )
    db.commit()
    assert lineage.role == ProjectionLineageRole.SELECTED.value
    assert lineage.granularity == ProjectionGranularity.SUMMARY.value
    assert lineage.contribution_value == Decimal("2000.123456789012")

    db.add(
        NutritionDailyProjectionLineage(
            user_id=user.id,
            projection_fact_id=fact.id,
            source_observation_id=other_observation.id,
            provider_key="yazio",
            role=ProjectionLineageRole.REJECTED.value,
            coverage_state=CoverageState.UNKNOWN.value,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    assert get_projection_by_version(db, other.id, PROJECTION_DATE, 1) is other_projection


def test_projection_lineage_roles_are_unique_per_fact_observation(db, user):
    projection = _projection(db, user, _policy(db, user))
    fact = _fact(db, user, projection)
    observation = _observation(db, user)
    create_projection_lineage(
        db,
        user_id=user.id,
        projection_fact_id=fact.id,
        source_observation_id=observation.id,
        provider_key="yazio",
        role=ProjectionLineageRole.SELECTED.value,
        coverage_state=CoverageState.COMPLETE.value,
    )
    db.commit()
    db.add(
        NutritionDailyProjectionLineage(
            user_id=user.id,
            projection_fact_id=fact.id,
            source_observation_id=observation.id,
            provider_key="yazio",
            role=ProjectionLineageRole.SELECTED.value,
            coverage_state=CoverageState.COMPLETE.value,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()


def test_projection_fetch_is_user_and_date_scoped(db, user):
    other = _other_user(db)
    policy = _policy(db, user)
    other_policy = _policy(db, other)
    first = _projection(db, user, policy, version=1)
    second = _projection(db, user, policy, version=2)
    other_projection = _projection(db, other, other_policy)
    db.commit()

    assert get_projection_by_version(db, user.id, PROJECTION_DATE, 1) is first
    assert get_projection_by_version(db, user.id, PROJECTION_DATE, 2) is second
    assert get_projection_by_version(db, user.id, date(2026, 9, 8), 1) is None
    assert get_projection_by_version(db, other.id, PROJECTION_DATE, 1) is other_projection
    assert get_projection_by_version(db, user.id, PROJECTION_DATE, 99) is None


def test_projection_head_initial_set_replacement_and_old_retention(db, user):
    policy = _policy(db, user)
    first = _projection(db, user, policy, version=1)
    second = _projection(db, user, policy, version=2)
    db.commit()

    set_projection_head(db, user.id, PROJECTION_DATE, first.id)
    db.commit()
    assert get_current_projection(db, user.id, PROJECTION_DATE) is first
    initial_head = db.get(NutritionProjectionHead, (user.id, PROJECTION_DATE))
    assert initial_head is not None and initial_head.current_projection_id == first.id

    set_projection_head(db, user.id, PROJECTION_DATE, second.id)
    db.commit()
    assert get_current_projection(db, user.id, PROJECTION_DATE) is second
    replaced_head = db.get(NutritionProjectionHead, (user.id, PROJECTION_DATE))
    assert replaced_head is not None and replaced_head.current_projection_id == second.id
    assert get_projection_by_version(db, user.id, PROJECTION_DATE, 1) is first
    assert get_projection_by_version(db, user.id, PROJECTION_DATE, 2) is second


def test_projection_head_rejects_cross_user_cross_date_and_missing_projection(db, user):
    other = _other_user(db)
    projection = _projection(db, user, _policy(db, user))
    other_projection = _projection(db, other, _policy(db, other))
    db.commit()

    with pytest.raises(ValueError):
        set_projection_head(db, other.id, PROJECTION_DATE, projection.id)
    with pytest.raises(ValueError):
        set_projection_head(db, user.id, date(2026, 9, 8), projection.id)
    with pytest.raises(ValueError):
        set_projection_head(db, user.id, PROJECTION_DATE, uuid4())
    db.rollback()
    assert get_current_projection(db, user.id, PROJECTION_DATE) is None
    assert get_current_projection(db, other.id, PROJECTION_DATE) is None
    assert other_projection.id != projection.id


def test_immutable_projection_records_have_no_mutation_or_deletion_primitives():
    import app.nutrition.repositories as repositories

    for name in (
        "update_projection",
        "delete_projection",
        "update_projection_fact",
        "delete_projection_fact",
        "update_projection_lineage",
        "delete_projection_lineage",
    ):
        assert not hasattr(repositories, name)
