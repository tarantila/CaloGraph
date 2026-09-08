from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from app.models import (
    NutritionExternalIdentity,
    NutritionFoodProfile,
    NutritionIngestionRun,
    NutritionSourceObservation,
)
from app.nutrition.enums import CoverageState, ObservationKind, PresenceState, ResolutionState
from app.nutrition.repositories import (
    append_identity_link,
    current_identity_link,
    validate_source_instance,
)


def _source_observation(db, user, profile):
    run = NutritionIngestionRun(
        user_id=user.id,
        provider_key=profile.provider_key,
        source_instance_id=profile.source_instance_id,
        status="completed",
        coverage_state=CoverageState.COMPLETE.value,
    )
    db.add(run)
    db.flush()
    observation = NutritionSourceObservation(
        user_id=user.id,
        ingestion_run_id=run.id,
        provider_key=profile.provider_key,
        source_instance_id=profile.source_instance_id,
        observation_kind=ObservationKind.PRODUCT_PROFILE.value,
        source_namespace="product",
        source_revision=1,
        observation_fingerprint="a" * 64,
        local_date=date(2026, 9, 7),
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
    )
    db.add(observation)
    db.flush()
    return observation

def test_append_identity_link_starts_revision_one_and_resolves_latest(db, user):
    identity = NutritionExternalIdentity(
        user_id=user.id,
        provider_key="yazio",
        namespace="product",
        identity_value="product-1",
        identity_kind="product",
    )
    first_profile = NutritionFoodProfile(
        user_id=user.id,
        provider_key="yazio",
        source_instance_id=uuid4(),
        profile_status="active",
    )
    second_profile = NutritionFoodProfile(
        user_id=user.id,
        provider_key="yazio",
        source_instance_id=first_profile.source_instance_id,
        profile_status="active",
    )
    db.add_all([identity, first_profile, second_profile])
    db.flush()

    first = append_identity_link(
        db,
        user_id=user.id,
        external_identity_id=identity.id,
        link_role="profile_identity",
        food_profile_id=first_profile.id,
    )
    db.commit()
    assert first.link_revision == 1

    second = append_identity_link(
        db,
        user_id=user.id,
        external_identity_id=identity.id,
        link_role="profile_identity",
        food_profile_id=second_profile.id,
        supersedes_link_id=first.id,
    )
    db.commit()
    assert second.link_revision == 2
    assert current_identity_link(
        db,
        user_id=user.id,
        external_identity_id=identity.id,
        link_role="profile_identity",
    ).id == second.id


def test_identity_link_revision_scope_is_per_role(db, user):
    identity = NutritionExternalIdentity(
        user_id=user.id,
        provider_key="yazio",
        namespace="product",
        identity_value="product-1",
        identity_kind="product",
    )
    profile = NutritionFoodProfile(
        user_id=user.id,
        provider_key="yazio",
        source_instance_id=uuid4(),
        profile_status="active",
    )
    db.add_all([identity, profile])
    db.flush()
    observation = _source_observation(db, user, profile)
    profile_link = append_identity_link(
        db,
        user_id=user.id,
        external_identity_id=identity.id,
        link_role="profile_identity",
        food_profile_id=profile.id,
    )
    source_link = append_identity_link(
        db,
        user_id=user.id,
        external_identity_id=identity.id,
        link_role="source_record",
        source_observation_id=observation.id,
    )
    db.commit()
    assert profile_link.link_revision == 1
    assert source_link.link_revision == 1


def test_append_identity_link_rejects_other_user_target(db, user):
    from app.models import User

    other = User(username="other", password_hash="hash", timezone="UTC")
    identity = NutritionExternalIdentity(
        user_id=user.id,
        provider_key="yazio",
        namespace="product",
        identity_value="product-1",
        identity_kind="product",
    )
    db.add_all([other, identity])
    db.flush()
    with pytest.raises(ValueError, match="same user"):
        append_identity_link(
            db,
            user_id=user.id,
            external_identity_id=identity.id,
            link_role="profile_identity",
            food_profile_id=uuid4(),
        )


def test_current_identity_link_does_not_mix_roles(db, user):
    identity = NutritionExternalIdentity(
        user_id=user.id,
        provider_key="yazio",
        namespace="product",
        identity_value="product-1",
        identity_kind="product",
    )
    profile = NutritionFoodProfile(
        user_id=user.id,
        provider_key="yazio",
        source_instance_id=uuid4(),
        profile_status="active",
    )
    db.add_all([identity, profile])
    db.flush()
    observation = _source_observation(db, user, profile)
    profile_link = append_identity_link(
        db,
        user_id=user.id,
        external_identity_id=identity.id,
        link_role="profile_identity",
        food_profile_id=profile.id,
    )
    source_link = append_identity_link(
        db,
        user_id=user.id,
        external_identity_id=identity.id,
        link_role="source_record",
        source_observation_id=observation.id,
    )
    db.commit()
    assert current_identity_link(db, user.id, identity.id, "profile_identity").id == profile_link.id
    assert current_identity_link(db, user.id, identity.id, "source_record").id == source_link.id



def test_yazio_source_instance_must_be_owned_by_user(db, user):
    with pytest.raises(ValueError, match="same user"):
        validate_source_instance(
            db,
            user_id=user.id,
            provider_key="yazio",
            source_instance_id=uuid4(),
        )