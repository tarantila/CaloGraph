from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models import YazioConnection
from app.nutrition.enums import (
    ConsumptionEventKind,
    CoverageState,
    LineageState,
    ObservationKind,
    PresenceState,
    ResolutionState,
)
from app.nutrition.models import (
    NutritionConsumptionEvent,
    NutritionFoodSnapshot,
    NutritionSourceObservation,
)
from app.nutrition.repositories import (
    append_identity_link,
    create_ingestion_run,
    create_source_observation,
    get_current_consumption_event,
    get_or_create_consumption_event,
    get_or_create_external_identity,
    get_or_create_food_profile,
    get_or_create_food_snapshot,
    get_or_create_source_observation,
)


def _connection(db, user) -> YazioConnection:
    connection = YazioConnection(
        user_id=user.id,
        encrypted_email=b"encrypted-email",
        encrypted_password=b"encrypted-password",
        source_identifier="test-source",
    )
    db.add(connection)
    db.flush()
    return connection


def _run(db, user, connection):
    return create_ingestion_run(
        db,
        user_id=user.id,
        provider_key="yazio",
        source_instance_id=connection.id,
        connector_variant="sdk-v22",
        requested_start_date=date(2026, 9, 1),
        requested_end_date=date(2026, 9, 2),
    )


def _observation(db, user, connection, run, *, record_id="item-1", revision=1, fingerprint="a" * 64):
    return get_or_create_source_observation(
        db,
        user_id=user.id,
        ingestion_run_id=run.id,
        provider_key="yazio",
        source_instance_id=connection.id,
        source_namespace="yazio.consumed_item",
        source_record_id=record_id,
        source_revision=revision,
        observation_fingerprint=fingerprint,
        observation_kind=ObservationKind.CONSUMPTION_EVENT.value,
        provider_civil_datetime=datetime(2026, 9, 1, 12, 30),
        local_date=date(2026, 9, 1),
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
        lineage_state=LineageState.CONFIRMED.value,
        provider_metadata={"safe": "value"},
    )


def test_source_observation_retries_are_idempotent_and_user_scoped(db, user):
    connection = _connection(db, user)
    run = _run(db, user, connection)
    first = _observation(db, user, connection, run)
    second = _observation(db, user, connection, run)
    assert second.id == first.id
    assert db.scalar(select(func.count()).select_from(NutritionSourceObservation)) == 1
    fingerprint_first = _observation(
        db,
        user,
        connection,
        run,
        record_id=None,
        fingerprint="b" * 64,
    )
    fingerprint_retry = _observation(
        db,
        user,
        connection,
        run,
        record_id=None,
        fingerprint="b" * 64,
    )
    assert fingerprint_retry.id == fingerprint_first.id
    assert db.scalar(select(func.count()).select_from(NutritionSourceObservation)) == 2
    changed = _observation(
        db,
        user,
        connection,
        run,
        fingerprint="c" * 64,
    )
    assert changed.source_revision == 2
    assert changed.id != first.id
    public_observation = create_source_observation(
        db,
        user_id=user.id,
        ingestion_run_id=run.id,
        provider_key="yazio",
        source_instance_id=connection.id,
        source_namespace="yazio.daily_summary",
        source_record_id="summary-1",
        source_revision=1,
        observation_fingerprint="f" * 64,
        observation_kind=ObservationKind.DAILY_SUMMARY.value,
        local_date=date(2026, 9, 1),
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
        lineage_state=LineageState.CONFIRMED.value,
    )
    assert public_observation.source_namespace == "yazio.daily_summary"

    other = type(user)(username="other", password_hash="hash", timezone="UTC")
    db.add(other)
    db.flush()
    with pytest.raises(ValueError, match="same user"):
        get_or_create_source_observation(
            db,
            user_id=other.id,
            ingestion_run_id=run.id,
            provider_key="yazio",
            source_instance_id=connection.id,
            source_namespace="yazio.consumed_item",
            source_record_id="item-1",
            source_revision=1,
            observation_fingerprint="a" * 64,
            observation_kind=ObservationKind.CONSUMPTION_EVENT.value,
        )


def test_external_identity_and_profile_snapshot_are_idempotent_and_revisioned(db, user):
    connection = _connection(db, user)
    run = _run(db, user, connection)
    observation = _observation(db, user, connection, run, record_id="product-1")
    identity = get_or_create_external_identity(
        db,
        user_id=user.id,
        provider_key="yazio",
        namespace="yazio.product",
        identity_value="product-1",
        identity_kind="product",
        source_instance_id=connection.id,
    )
    same_identity = get_or_create_external_identity(
        db,
        user_id=user.id,
        provider_key="yazio",
        namespace="yazio.product",
        identity_value="product-1",
        identity_kind="product",
        source_instance_id=connection.id,
        provider_metadata={"source": "retry"},
    )
    assert same_identity.id == identity.id
    assert same_identity.provider_metadata == {"source": "retry"}
    ean_identity = get_or_create_external_identity(
        db,
        user_id=user.id,
        provider_key="yazio",
        namespace="yazio.ean",
        identity_value="product-1",
        identity_kind="ean",
        source_instance_id=connection.id,
    )
    assert ean_identity.id != identity.id

    profile = get_or_create_food_profile(
        db,
        user_id=user.id,
        provider_key="yazio",
        source_instance_id=connection.id,
        external_identity_id=identity.id,
    )
    same_profile = get_or_create_food_profile(
        db,
        user_id=user.id,
        provider_key="yazio",
        source_instance_id=connection.id,
        external_identity_id=identity.id,
    )
    assert same_profile.id == profile.id

    snapshot = get_or_create_food_snapshot(
        db,
        user_id=user.id,
        food_profile_id=profile.id,
        source_observation_id=observation.id,
        content_hash="b" * 64,
        provider_revision="rev-1",
        name="Food",
        base_unit="g",
        provider_updated_at=datetime(2026, 9, 1, 8, 0),
        provider_metadata={"energy": "12.34"},
    )
    same_snapshot = get_or_create_food_snapshot(
        db,
        user_id=user.id,
        food_profile_id=profile.id,
        source_observation_id=observation.id,
        content_hash="b" * 64,
        provider_revision="rev-1",
    )
    changed_snapshot = get_or_create_food_snapshot(
        db,
        user_id=user.id,
        food_profile_id=profile.id,
        source_observation_id=observation.id,
        content_hash="c" * 64,
        provider_revision="rev-2",
        name="Changed Food",
    )
    assert same_snapshot.id == snapshot.id
    assert changed_snapshot.id != snapshot.id
    db.refresh(profile)
    assert profile.current_snapshot_id == changed_snapshot.id
    db.refresh(snapshot)
    assert snapshot.name == "Food"
    assert db.scalar(select(func.count()).select_from(NutritionFoodSnapshot)) == 2
    historical_snapshot = get_or_create_food_snapshot(
        db,
        user_id=user.id,
        food_profile_id=profile.id,
        source_observation_id=observation.id,
        content_hash="b" * 64,
        provider_revision="rev-1",
    )
    db.refresh(profile)
    assert historical_snapshot.id == snapshot.id
    assert profile.current_snapshot_id == changed_snapshot.id


def test_consumption_event_retry_reuses_revision_and_changed_content_supersedes(db, user):
    connection = _connection(db, user)
    run = _run(db, user, connection)
    first_observation = _observation(db, user, connection, run, fingerprint="d" * 64)
    first = get_or_create_consumption_event(
        db,
        user_id=user.id,
        source_observation_id=first_observation.id,
        provider_key="yazio",
        source_instance_id=connection.id,
        event_kind=ConsumptionEventKind.PRODUCT.value,
        logical_event_key="item-1",
        amount=Decimal("1.25"),
        amount_unit="g",
        provider_civil_datetime=datetime(2026, 9, 1, 12, 30),
        local_date=date(2026, 9, 1),
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
        lineage_state=LineageState.CONFIRMED.value,
        provider_metadata={"content_hash": "d" * 64},
    )
    assert first.amount == Decimal("1.250000000000")
    assert first.provider_civil_datetime == datetime(2026, 9, 1, 12, 30)
    assert first.local_date == date(2026, 9, 1)
    assert first.provider_metadata == {"content_hash": "d" * 64}
    retry = get_or_create_consumption_event(
        db,
        user_id=user.id,
        source_observation_id=first_observation.id,
        provider_key="yazio",
        source_instance_id=connection.id,
        event_kind=ConsumptionEventKind.PRODUCT.value,
        logical_event_key="item-1",
        amount=Decimal("1.25"),
        amount_unit="g",
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
        lineage_state=LineageState.CONFIRMED.value,
        provider_metadata={"content_hash": "d" * 64},
    )
    second_observation = _observation(db, user, connection, run, revision=2, fingerprint="e" * 64)
    changed = get_or_create_consumption_event(
        db,
        user_id=user.id,
        source_observation_id=second_observation.id,
        provider_key="yazio",
        source_instance_id=connection.id,
        event_kind=ConsumptionEventKind.PRODUCT.value,
        logical_event_key="item-1",
        amount=Decimal("2.5"),
        amount_unit="g",
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
        lineage_state=LineageState.CONFIRMED.value,
        provider_metadata={"content_hash": "e" * 64},
    )
    assert retry.id == first.id
    assert changed.revision == 2
    assert changed.supersedes_event_id == first.id
    assert changed.supersedes_revision == 1
    assert db.scalar(select(func.count()).select_from(NutritionConsumptionEvent)) == 2


def test_repository_flushes_without_commit_and_rolls_back(db, user):
    connection = _connection(db, user)
    run = _run(db, user, connection)
    assert db.scalar(select(func.count()).select_from(NutritionSourceObservation)) == 0
    observation = _observation(db, user, connection, run)
    assert observation.id is not None
    assert db.scalar(select(func.count()).select_from(NutritionSourceObservation)) == 1
    db.rollback()
    assert db.scalar(select(func.count()).select_from(NutritionSourceObservation)) == 0


def test_cross_user_profile_and_event_targets_are_rejected(db, user):
    connection = _connection(db, user)
    run = _run(db, user, connection)
    observation = _observation(db, user, connection, run)
    identity = get_or_create_external_identity(
        db,
        user_id=user.id,
        provider_key="yazio",
        namespace="yazio.product",
        identity_value="product-1",
        identity_kind="product",
        source_instance_id=connection.id,
    )
    profile = get_or_create_food_profile(
        db,
        user_id=user.id,
        provider_key="yazio",
        source_instance_id=connection.id,
        external_identity_id=identity.id,
    )
    with pytest.raises(ValueError, match="same user"):
        get_or_create_external_identity(
            db,
            user_id=user.id,
            provider_key="yazio",
            namespace="yazio.product",
            identity_value="foreign-source",
            identity_kind="product",
            source_instance_id=uuid4(),
        )
    with pytest.raises(ValueError, match="same user"):
        get_current_consumption_event(
            db,
            user_id=user.id,
            provider_key="yazio",
            source_instance_id=uuid4(),
            logical_event_key="item-1",
        )
    foreign_identity = get_or_create_external_identity(
        db,
        user_id=user.id,
        provider_key="other-provider",
        namespace="other.product",
        identity_value="product-1",
        identity_kind="product",
        source_instance_id=uuid4(),
    )
    with pytest.raises(ValueError, match="provider"):
        append_identity_link(
            db,
            user_id=user.id,
            external_identity_id=foreign_identity.id,
            link_role="profile_identity",
            food_profile_id=profile.id,
        )
    other = type(user)(username="other", password_hash="hash", timezone="UTC")
    db.add(other)
    db.flush()
    with pytest.raises(ValueError, match="same user"):
        get_or_create_food_snapshot(
            db,
            user_id=other.id,
            food_profile_id=profile.id,
            source_observation_id=observation.id,
            content_hash="f" * 64,
        )
    with pytest.raises(ValueError, match="same user"):
        get_or_create_consumption_event(
            db,
            user_id=other.id,
            source_observation_id=observation.id,
            provider_key="yazio",
            source_instance_id=connection.id,
            event_kind=ConsumptionEventKind.PRODUCT.value,
            logical_event_key="item-1",
            presence_state=PresenceState.SUPPLIED.value,
            coverage_state=CoverageState.COMPLETE.value,
            resolution_state=ResolutionState.RESOLVED.value,
            lineage_state=LineageState.CONFIRMED.value,
        )


def test_decimal_civil_time_local_date_and_metadata_round_trip(db, user):
    connection = _connection(db, user)
    run = _run(db, user, connection)
    observation = _observation(db, user, connection, run)
    assert observation.provider_civil_datetime == datetime(2026, 9, 1, 12, 30)
    assert observation.local_date == date(2026, 9, 1)
    assert observation.provider_metadata == {"safe": "value"}
    identity = get_or_create_external_identity(
        db,
        user_id=user.id,
        provider_key="yazio",
        namespace="yazio.ean",
        identity_value="123",
        identity_kind="ean",
        source_instance_id=connection.id,
        provider_metadata={"decimal": "1.2"},
    )
    assert identity.provider_metadata == {"decimal": "1.2"}
