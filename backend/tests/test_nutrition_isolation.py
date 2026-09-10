from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import (
    NutritionConsumptionEvent,
    NutritionExternalIdentity,
    NutritionExternalIdentityLink,
    NutritionFoodProfile,
    NutritionFoodSnapshot,
    NutritionIngestionRun,
    NutritionSourceObservation,
)
from app.nutrition.enums import (
    ConsumptionEventKind,
    CoverageState,
    ObservationKind,
    PresenceState,
    ResolutionState,
)


def _user(db, username):
    from app.models import User

    item = User(username=username, password_hash="hash", timezone="UTC")
    db.add(item)
    db.flush()
    return item


def _run(db, user, source_instance_id=None):
    run = NutritionIngestionRun(
        user_id=user.id,
        provider_key="yazio",
        source_instance_id=source_instance_id or uuid4(),
        status="completed",
        coverage_state=CoverageState.COMPLETE.value,
    )
    db.add(run)
    db.flush()
    return run


def _observation(db, user, run, fingerprint):
    observation = NutritionSourceObservation(
        user_id=user.id,
        ingestion_run_id=run.id,
        provider_key="yazio",
        source_instance_id=run.source_instance_id,
        observation_kind=ObservationKind.PRODUCT.value,
        source_namespace="product",
        source_revision=1,
        observation_fingerprint=fingerprint,
        local_date=date(2026, 9, 7),
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
    )
    db.add(observation)
    db.flush()
    return observation


def test_event_cannot_reference_other_users_source_observation(db):
    first = _user(db, "first")
    second = _user(db, "second")
    first_run = _run(db, first)
    second_run = _run(db, second)
    first_observation = _observation(db, first, first_run, "1" * 64)
    second_observation = _observation(db, second, second_run, "2" * 64)
    event = NutritionConsumptionEvent(
        user_id=first.id,
        source_observation_id=second_observation.id,
        provider_key="yazio",
        source_instance_id=second_run.source_instance_id,
        event_kind=ConsumptionEventKind.PRODUCT.value,
        local_date=date(2026, 9, 7),
    )
    db.add(event)
    with pytest.raises(IntegrityError):
        db.commit()
    assert first_observation.user_id == first.id


def test_identity_link_cannot_cross_users(db):
    first = _user(db, "first")
    second = _user(db, "second")
    first_identity = NutritionExternalIdentity(
        user_id=first.id,
        source_instance_id=uuid4(),
        provider_key="yazio",
        namespace="product",
        identity_value="product-1",
        identity_kind="product",
    )
    second_profile = NutritionFoodProfile(
        user_id=second.id,
        provider_key="yazio",
        source_instance_id=uuid4(),
        profile_status="active",
    )
    db.add_all([first_identity, second_profile])
    db.flush()
    link = NutritionExternalIdentityLink(
        user_id=first.id,
        external_identity_id=first_identity.id,
        link_role="profile_identity",
        food_profile_id=second_profile.id,
        link_revision=1,
    )
    db.add(link)
    with pytest.raises(IntegrityError):
        db.commit()


def test_food_snapshot_cannot_cross_profile_users(db):
    first = _user(db, "first")
    second = _user(db, "second")
    first_run = _run(db, first)
    first_observation = _observation(db, first, first_run, "3" * 64)
    second_profile = NutritionFoodProfile(
        user_id=second.id,
        provider_key="yazio",
        source_instance_id=uuid4(),
        profile_status="active",
    )
    db.add(second_profile)
    db.flush()
    snapshot = NutritionFoodSnapshot(
        user_id=first.id,
        food_profile_id=second_profile.id,
        source_observation_id=first_observation.id,
        content_hash="4" * 64,
    )
    db.add(snapshot)
    with pytest.raises(IntegrityError):
        db.commit()
