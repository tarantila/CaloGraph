from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import (
    NutritionConsumptionEvent,
    NutritionExternalIdentity,
    NutritionFoodProfile,
    NutritionFoodSnapshot,
    NutritionIngestionRun,
    NutritionServingObservation,
    NutritionSourceObservation,
)
from app.nutrition.enums import (
    ConsumptionEventKind,
    CoverageState,
    LineageState,
    ObservationKind,
    PresenceState,
    ResolutionState,
    ServingScope,
)


def _run(user_id, db, *, provider_key="yazio", source_instance_id=None):
    run = NutritionIngestionRun(
        user_id=user_id,
        provider_key=provider_key,
        source_instance_id=source_instance_id or uuid4(),
        status="completed",
        coverage_state=CoverageState.COMPLETE.value,
    )
    db.add(run)
    db.flush()
    return run


def _observation(user_id, db, run, *, fingerprint="a" * 64, source_record_id=None, flush=True):
    observation = NutritionSourceObservation(
        user_id=user_id,
        ingestion_run_id=run.id,
        provider_key=run.provider_key,
        source_instance_id=run.source_instance_id,
        observation_kind=ObservationKind.PRODUCT.value,
        source_namespace="product",
        source_record_id=source_record_id,
        source_revision=1,
        observation_fingerprint=fingerprint,
        local_date=date(2026, 9, 7),
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
    )
    db.add(observation)
    if flush:
        db.flush()
    return observation


def test_source_observation_rejects_non_sha256_fingerprint(db, user):
    run = _run(user.id, db)
    _observation(user.id, db, run, fingerprint="short", flush=False)
    with pytest.raises(IntegrityError):
        db.commit()

def test_source_observation_external_revision_is_distinct(db, user):
    run = _run(user.id, db)
    first = _observation(user.id, db, run, source_record_id="source-1")
    db.commit()
    second = NutritionSourceObservation(
        user_id=user.id,
        ingestion_run_id=run.id,
        provider_key=run.provider_key,
        source_instance_id=run.source_instance_id,
        observation_kind=ObservationKind.PRODUCT.value,
        source_namespace="product",
        source_record_id="source-1",
        source_revision=2,
        observation_fingerprint="b" * 64,
        local_date=date(2026, 9, 7),
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
    )
    db.add(second)
    db.commit()
    assert first.id != second.id


def test_external_identity_is_user_scoped(db, user):
    other_user = type(user)(
        username="other",
        password_hash="hash",
        timezone="UTC",
    )
    db.add(other_user)
    db.flush()
    first = NutritionExternalIdentity(
        user_id=user.id,
        source_instance_id=uuid4(),
        provider_key="yazio",
        namespace="product",
        identity_value="same-value",
        identity_kind="product",
    )
    second = NutritionExternalIdentity(
        user_id=other_user.id,
        source_instance_id=uuid4(),
        provider_key="yazio",
        namespace="product",
        identity_value="same-value",
        identity_kind="product",
    )
    db.add_all([first, second])
    db.commit()
    assert first.id != second.id


def test_profile_snapshot_cross_user_reference_is_rejected(db, user):
    other_user = type(user)(
        username="other",
        password_hash="hash",
        timezone="UTC",
    )
    db.add(other_user)
    db.flush()
    run = _run(user.id, db)
    observation = _observation(user.id, db, run)
    profile = NutritionFoodProfile(
        user_id=user.id,
        provider_key="yazio",
        source_instance_id=run.source_instance_id,
        profile_status="active",
    )
    db.add(profile)
    db.flush()
    snapshot = NutritionFoodSnapshot(
        user_id=other_user.id,
        food_profile_id=profile.id,
        source_observation_id=observation.id,
        content_hash="c" * 64,
    )
    db.add(snapshot)
    with pytest.raises(IntegrityError):
        db.commit()


def test_serving_requires_exactly_one_scope_parent(db, user):
    run = _run(user.id, db)
    observation = _observation(user.id, db, run)
    profile = NutritionFoodProfile(
        user_id=user.id,
        provider_key="yazio",
        source_instance_id=run.source_instance_id,
        profile_status="active",
    )
    db.add(profile)
    db.flush()
    snapshot = NutritionFoodSnapshot(
        user_id=user.id,
        food_profile_id=profile.id,
        source_observation_id=observation.id,
        content_hash="d" * 64,
    )
    db.add(snapshot)
    db.flush()
    invalid = NutritionServingObservation(
        user_id=user.id,
        consumption_event_id=uuid4(),
        food_snapshot_id=snapshot.id,
        serving_scope=ServingScope.PROFILE.value,
        label="portion",
        amount=1,
        unit="g",
    )
    db.add(invalid)
    with pytest.raises(IntegrityError):
        db.commit()


def test_event_revision_cannot_cross_provider_or_source_instance(db, user):
    first_run = _run(user.id, db, provider_key="yazio", source_instance_id=uuid4())
    second_run = _run(user.id, db, provider_key="other", source_instance_id=uuid4())
    first_observation = _observation(user.id, db, first_run, fingerprint="e" * 64)
    second_observation = _observation(user.id, db, second_run, fingerprint="f" * 64)
    first = NutritionConsumptionEvent(
        user_id=user.id,
        source_observation_id=first_observation.id,
        provider_key=first_run.provider_key,
        source_instance_id=first_run.source_instance_id,
        event_kind=ConsumptionEventKind.PRODUCT.value,
        logical_event_key="meal-1",
        revision=1,
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
        lineage_state=LineageState.CONFIRMED.value,
    )
    db.add(first)
    db.flush()
    invalid = NutritionConsumptionEvent(
        user_id=user.id,
        source_observation_id=second_observation.id,
        provider_key=second_run.provider_key,
        source_instance_id=second_run.source_instance_id,
        event_kind=ConsumptionEventKind.PRODUCT.value,
        logical_event_key="meal-1",
        revision=2,
        supersedes_event_id=first.id,
        supersedes_revision=1,
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
        lineage_state=LineageState.CONFIRMED.value,
    )
    db.add(invalid)
    with pytest.raises(IntegrityError):
        db.commit()
