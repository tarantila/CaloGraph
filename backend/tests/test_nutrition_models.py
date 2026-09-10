from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.models import (
    NutritionConsumptionEvent,
    NutritionExternalIdentity,
    NutritionExternalIdentityLink,
    NutritionFieldObservation,
    NutritionFoodProfile,
    NutritionFoodSnapshot,
    NutritionIngestionRun,
    NutritionProvenance,
    NutritionServingObservation,
    NutritionSourceObservation,
    NutritionSourceTombstone,
)
from app.nutrition.enums import (
    ConsumptionEventKind,
    CoverageState,
    LineageState,
    ObservationKind,
    ObservationRole,
    PresenceState,
    ResolutionState,
    ServingScope,
)


def test_phase_a1_tables_are_registered() -> None:
    table_names = {
        NutritionIngestionRun.__tablename__,
        NutritionSourceObservation.__tablename__,
        NutritionExternalIdentity.__tablename__,
        NutritionExternalIdentityLink.__tablename__,
        NutritionConsumptionEvent.__tablename__,
        NutritionFoodProfile.__tablename__,
        NutritionFoodSnapshot.__tablename__,
        NutritionServingObservation.__tablename__,
        NutritionFieldObservation.__tablename__,
        NutritionSourceTombstone.__tablename__,
        NutritionProvenance.__tablename__,
    }
    assert table_names == {
        "nutrition_ingestion_runs",
        "nutrition_source_observations",
        "nutrition_external_identities",
        "nutrition_external_identity_links",
        "nutrition_consumption_events",
        "nutrition_food_profiles",
        "nutrition_food_snapshots",
        "nutrition_serving_observations",
        "nutrition_field_observations",
        "nutrition_source_tombstones",
        "nutrition_provenance",
    }


def test_phase_a1_enum_values_are_stable() -> None:
    assert ObservationKind.PRODUCT.value == "product"
    assert ConsumptionEventKind.SIMPLE_PRODUCT.value == "simple_product"
    assert ServingScope.PROFILE.value == "profile"
    assert PresenceState.EXPLICIT_ZERO.value == "explicit_zero"
    assert CoverageState.PARTIAL.value == "partial"
    assert ResolutionState.CONFLICT.value == "conflict"
    assert LineageState.CONFIRMED.value == "confirmed"
    assert ObservationRole.DERIVED.value == "derived"


def test_source_observation_requires_source_instance_and_fingerprint(db, user) -> None:
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
        provider_key="yazio",
        source_instance_id=run.source_instance_id,
        observation_kind=ObservationKind.PRODUCT.value,
        source_namespace="consumed_item",
        source_revision=1,
        observation_fingerprint="a" * 64,
        local_date=date(2026, 9, 7),
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
    )
    db.add(observation)
    db.commit()
    assert observation.id is not None

    missing_instance = NutritionSourceObservation(
        user_id=user.id,
        ingestion_run_id=run.id,
        provider_key="yazio",
        source_instance_id=None,
        observation_kind=ObservationKind.PRODUCT.value,
        source_namespace="consumed_item",
        source_revision=1,
        observation_fingerprint="b" * 64,
        local_date=date(2026, 9, 7),
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
    )
    db.add(missing_instance)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_source_observation_partial_idempotency_indexes(db, user) -> None:
    source_instance_id = uuid4()
    run = NutritionIngestionRun(
        user_id=user.id,
        provider_key="apple_health",
        source_instance_id=source_instance_id,
        status="completed",
        coverage_state=CoverageState.COMPLETE.value,
    )
    db.add(run)
    db.flush()
    first = NutritionSourceObservation(
        user_id=user.id,
        ingestion_run_id=run.id,
        provider_key="apple_health",
        source_instance_id=source_instance_id,
        observation_kind=ObservationKind.CONSUMPTION_EVENT.value,
        source_namespace="hk_correlation",
        source_record_id="hk-1",
        source_revision=1,
        observation_fingerprint="a" * 64,
        local_date=date(2026, 9, 7),
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
    )
    duplicate_external = NutritionSourceObservation(
        user_id=user.id,
        ingestion_run_id=run.id,
        provider_key="apple_health",
        source_instance_id=source_instance_id,
        observation_kind=ObservationKind.CONSUMPTION_EVENT.value,
        source_namespace="hk_correlation",
        source_record_id="hk-1",
        source_revision=1,
        observation_fingerprint="b" * 64,
        local_date=date(2026, 9, 7),
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
    )
    db.add_all([first, duplicate_external])
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    first = NutritionSourceObservation(
        user_id=user.id,
        ingestion_run_id=run.id,
        provider_key="apple_health",
        source_instance_id=source_instance_id,
        observation_kind=ObservationKind.CONSUMPTION_EVENT.value,
        source_namespace="hk_correlation",
        source_revision=1,
        observation_fingerprint="c" * 64,
        local_date=date(2026, 9, 7),
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
    )
    duplicate_fingerprint = NutritionSourceObservation(
        user_id=user.id,
        ingestion_run_id=run.id,
        provider_key="apple_health",
        source_instance_id=source_instance_id,
        observation_kind=ObservationKind.CONSUMPTION_EVENT.value,
        source_namespace="hk_correlation",
        source_revision=1,
        observation_fingerprint="c" * 64,
        local_date=date(2026, 9, 7),
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
    )
    db.add_all([first, duplicate_fingerprint])
    with pytest.raises(IntegrityError):
        db.commit()


def test_external_identity_links_are_revisioned_per_role(db, user) -> None:
    source_instance_id = uuid4()
    identity = NutritionExternalIdentity(
        user_id=user.id,
        source_instance_id=source_instance_id,
        provider_key="yazio",
        namespace="product",
        identity_value="product-1",
        identity_kind="product",
    )
    profile = NutritionFoodProfile(
        user_id=user.id,
        provider_key="yazio",
        source_instance_id=source_instance_id,
        profile_status="active",
    )
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
        provider_key="yazio",
        source_instance_id=run.source_instance_id,
        observation_kind=ObservationKind.PRODUCT_PROFILE.value,
        source_namespace="product",
        source_revision=1,
        observation_fingerprint="d" * 64,
        local_date=date(2026, 9, 7),
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
    )
    db.add_all([identity, profile, observation])
    db.flush()
    first = NutritionExternalIdentityLink(
        user_id=user.id,
        external_identity_id=identity.id,
        link_role="profile_identity",
        food_profile_id=profile.id,
        link_revision=1,
    )
    db.add(first)
    db.commit()
    db.refresh(first)
    assert first.supersedes_link_id is None

    second = NutritionExternalIdentityLink(
        user_id=user.id,
        external_identity_id=identity.id,
        link_role="profile_identity",
        source_observation_id=observation.id,
        link_revision=2,
        supersedes_link_id=first.id,
        supersedes_link_revision=1,
    )
    db.add(second)
    db.commit()
    assert second.id is not None

    role_independent = NutritionExternalIdentityLink(
        user_id=user.id,
        external_identity_id=identity.id,
        link_role="source_record",
        source_observation_id=observation.id,
        link_revision=1,
    )
    db.add(role_independent)
    db.commit()
    assert role_independent.id is not None
def test_invalid_external_identity_link_supersedes_scope_is_rejected(db, user) -> None:
    source_instance_id = uuid4()
    first_identity = NutritionExternalIdentity(
        user_id=user.id,
        source_instance_id=source_instance_id,
        provider_key="yazio",
        namespace="product",
        identity_value="product-1",
        identity_kind="product",
    )
    second_identity = NutritionExternalIdentity(
        user_id=user.id,
        source_instance_id=source_instance_id,
        provider_key="yazio",
        namespace="product",
        identity_value="product-2",
        identity_kind="product",
    )
    profile = NutritionFoodProfile(
        user_id=user.id,
        provider_key="yazio",
        source_instance_id=source_instance_id,
        profile_status="active",
    )
    db.add_all([first_identity, second_identity, profile])
    db.flush()
    first = NutritionExternalIdentityLink(
        user_id=user.id,
        external_identity_id=first_identity.id,
        link_role="profile_identity",
        food_profile_id=profile.id,
        link_revision=1,
    )
    db.add(first)
    db.commit()
    db.refresh(first)

    invalid = NutritionExternalIdentityLink(
        user_id=user.id,
        external_identity_id=second_identity.id,
        link_role="profile_identity",
        food_profile_id=profile.id,
        link_revision=2,
        supersedes_link_id=first.id,
        supersedes_link_revision=1,
    )
    db.add(invalid)
    with pytest.raises(IntegrityError):
        db.commit()


def test_serving_scope_and_profile_snapshot_reference_are_user_scoped(db, user) -> None:
    source_instance_id = uuid4()
    run = NutritionIngestionRun(
        user_id=user.id,
        provider_key="yazio",
        source_instance_id=source_instance_id,
        status="completed",
        coverage_state=CoverageState.COMPLETE.value,
    )
    db.add(run)
    db.flush()
    observation = NutritionSourceObservation(
        user_id=user.id,
        ingestion_run_id=run.id,
        provider_key="yazio",
        source_instance_id=source_instance_id,
        observation_kind=ObservationKind.PRODUCT_PROFILE.value,
        source_namespace="product",
        source_revision=1,
        observation_fingerprint="e" * 64,
        local_date=date(2026, 9, 7),
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
    )
    profile = NutritionFoodProfile(
        user_id=user.id,
        provider_key="yazio",
        source_instance_id=source_instance_id,
        profile_status="active",
    )
    db.add_all([observation, profile])
    db.flush()
    snapshot = NutritionFoodSnapshot(
        user_id=user.id,
        food_profile_id=profile.id,
        source_observation_id=observation.id,
        content_hash="f" * 64,
        base_unit="g",
    )
    db.add(snapshot)
    db.commit()
    assert snapshot.id is not None

    profile_serving = NutritionServingObservation(
        user_id=user.id,
        food_snapshot_id=snapshot.id,
        serving_scope=ServingScope.PROFILE.value,
        label="portion",
        amount=Decimal("30"),
        unit="g",
    )
    db.add(profile_serving)
    db.commit()
    assert profile_serving.id is not None


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
        provider_key="yazio",
        source_instance_id=run.source_instance_id,
        observation_kind=ObservationKind.PRODUCT_PROFILE.value,
        source_namespace="product",
        source_revision=1,
        observation_fingerprint="1" * 64,
        local_date=date(2026, 9, 7),
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
    )
    db.add(observation)
    db.flush()
    field = NutritionFieldObservation(
        user_id=user.id,
        source_observation_id=observation.id,
        provider_field_path="nutrient.carb",
        provider_raw_value_decimal=Decimal("0.078667"),
        provider_raw_unit="g_per_base_unit",
        metric_key="carbohydrates_g",
        canonical_value=Decimal("0.078667"),
        canonical_unit="g_per_base_unit",
        observation_role=ObservationRole.PROVIDER.value,
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
    )
    db.add(field)
    db.commit()
    assert field.provider_raw_value_decimal == Decimal("0.078667")
    assert field.canonical_value == Decimal("0.078667")


def test_a1_models_have_expected_decimal_precision() -> None:
    assert NutritionFieldObservation.__table__.c.provider_raw_value_decimal.type.precision == 24
    assert NutritionFieldObservation.__table__.c.provider_raw_value_decimal.type.scale == 12
    assert NutritionFieldObservation.__table__.c.canonical_value.type.precision == 24
    assert NutritionFieldObservation.__table__.c.canonical_value.type.scale == 12


def test_model_indexes_are_user_prefixed() -> None:
    indexes = inspect(NutritionSourceObservation).mapper.persist_selectable.indexes
    assert any(index.name == "ix_nutrition_source_observations_user_local_date" for index in indexes)
