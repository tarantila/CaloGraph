from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update

from app.models import YazioConnection
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
from app.nutrition.models import (
    NutritionConsumptionEvent,
    NutritionDailyProjection,
    NutritionExternalIdentity,
    NutritionExternalIdentityLink,
    NutritionFieldObservation,
    NutritionFoodProfile,
    NutritionFoodSnapshot,
    NutritionIngestionRun,
    NutritionProvenance,
    NutritionServingObservation,
    NutritionSourceObservation,
)
from app.services.yazio_nutrition_ingestion import ingest_yazio_food_diary
from app.services.yazio_provider import (
    YazioConsumedProduct,
    YazioConsumedSimpleProduct,
    YazioDailyNutrientSummary,
    YazioFoodDiary,
    YazioNutrientValues,
    YazioProductProfile,
    YazioServing,
)

PROVIDER = "yazio"
DAY = date(2026, 9, 1)


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


def _diary(
    *,
    base_unit: str = "g",
    product_amount: Decimal | None = Decimal("2.5"),
    profile_energy: Decimal | None = Decimal("2"),
    profile_protein: Decimal | None = Decimal("0"),
    simple: bool = True,
    summary: bool = True,
    updated_at: datetime | None = datetime(2026, 8, 31, 10, 0),
    token: str = "must-not-store",
) -> YazioFoodDiary:
    profile = YazioProductProfile(
        product_id="product-1",
        name="Food",
        producer="Producer",
        category="Category",
        base_unit=base_unit,
        nutrients=YazioNutrientValues(energy=profile_energy, protein=profile_protein, additional={"mystery": Decimal("4")}),
        servings=(
            YazioServing(label="portion", amount=Decimal("30"), unit=base_unit),
            YazioServing(label="small", amount=Decimal("10"), unit=base_unit),
        ),
        eans=("111", "222"),
        language="en",
        countries=("DE",),
        updated_at=updated_at,
        is_verified=True,
        is_private=False,
        is_deleted=False,
        metadata={"profile_safe": "yes"},
    )
    products = (
        YazioConsumedProduct(
            consumed_item_id="event-1",
            product_id="product-1",
            amount=product_amount,
            provider_civil_datetime=datetime(2026, 9, 1, 12, 34, 56),
            local_date=DAY,
            daytime="lunch",
            serving="portion",
            serving_quantity=Decimal("2"),
            provider_timezone="UTC+02:00",
            metadata={"event_safe": "yes"},
        ),
    )
    simple_products = (
        YazioConsumedSimpleProduct(
            consumed_item_id="simple-1",
            amount=Decimal("1"),
            provider_civil_datetime=datetime(2026, 9, 1, 8, 10),
            local_date=DAY,
            daytime="breakfast",
            nutrients=YazioNutrientValues(energy=Decimal("0"), protein=None, additional={"unknown": Decimal("7")}),
            serving="cup",
            serving_quantity=Decimal("1"),
            provider_timezone=None,
            name="Tea",
            metadata={
                "is_ai_generated": True,
                "simple_safe": "yes",
                "token": token,
                "api_key": "must-not-store",
            },
        ),
    ) if simple else ()
    summaries = (
        YazioDailyNutrientSummary(
            local_date=DAY,
            nutrients=YazioNutrientValues(energy=Decimal("0"), protein=Decimal("3")),
            energy_goal=Decimal("2000"),
            metadata={"summary_safe": "yes"},
        ),
    ) if summary else ()
    return YazioFoodDiary(
        requested_start_day=DAY,
        requested_end_day=DAY,
        consumed_products=products,
        consumed_simple_products=simple_products,
        product_profiles=(profile,),
        daily_summaries=summaries,
    )


def _ingest(db, user, diary=None, **kwargs):
    connection = kwargs.pop("connection", None) or _connection(db, user)
    simple = kwargs.pop("simple", True)
    summary = kwargs.pop("summary", True)
    if diary is None:
        diary = _diary(simple=simple, summary=summary)
    assert not kwargs
    return ingest_yazio_food_diary(
        db,
        user_id=user.id,
        source_instance_id=connection.id,
        requested_start=DAY,
        requested_end=DAY,
        diary=diary,
    )


def test_product_event_uses_profile_base_unit_without_scaling(db, user):
    run = _ingest(db, user, simple=False, summary=False)
    event = db.scalar(select(NutritionConsumptionEvent))
    fields = db.scalars(select(NutritionFieldObservation).where(NutritionFieldObservation.source_observation_id == event.source_observation_id)).all()
    by_metric = {field.metric_key: field for field in fields}
    assert run.connector_variant == "sdk-v22"
    assert run.coverage_state == CoverageState.COMPLETE.value
    assert event.event_kind == ConsumptionEventKind.PRODUCT.value
    assert event.amount == Decimal("2.5")
    assert event.amount_unit == "g"
    assert by_metric["dietary_energy_kcal"].canonical_value == Decimal("5.0")
    assert by_metric["protein_g"].canonical_value == Decimal("0.0")
    assert by_metric["protein_g"].presence_state == PresenceState.EXPLICIT_ZERO.value




def test_product_event_without_base_unit_is_unresolved_and_not_derived(db, user):
    _ingest(db, user, _diary(base_unit=None), simple=False, summary=False)
    event = db.scalar(select(NutritionConsumptionEvent))
    assert event.resolution_state == ResolutionState.UNRESOLVED.value
    assert event.amount_unit is None
    fields = db.scalars(
        select(NutritionFieldObservation).where(
            NutritionFieldObservation.source_observation_id == event.source_observation_id
        )
    ).all()
    assert not any(field.observation_role == ObservationRole.DERIVED.value for field in fields)
def test_product_event_ml_uses_same_formula_and_ignores_serving_quantity(db, user):
    _ingest(db, user, _diary(base_unit="ml"), simple=False, summary=False)
    event = db.scalar(select(NutritionConsumptionEvent))
    fields = db.scalars(select(NutritionFieldObservation).where(NutritionFieldObservation.source_observation_id == event.source_observation_id)).all()
    assert event.amount == Decimal("2.5")
    assert event.amount_unit == "ml"
    assert {field.metric_key: field for field in fields}["dietary_energy_kcal"].canonical_value == Decimal("5.0")

def test_simple_product_preserves_direct_values_name_ai_zero_missing_unknown(db, user):
    _ingest(db, user, _diary(simple=True, summary=False), **{})
    event = db.scalar(select(NutritionConsumptionEvent).where(NutritionConsumptionEvent.event_kind == "simple_product"))
    assert event is not None
    assert event.provider_metadata["name"] == "Tea"
    assert event.provider_metadata["is_ai_generated"] is True
    assert "token" not in event.provider_metadata
    assert "api_key" not in event.provider_metadata
    fields = db.scalars(select(NutritionFieldObservation).where(NutritionFieldObservation.source_observation_id == event.source_observation_id)).all()
    by_path = {field.provider_field_path: field for field in fields}
    assert by_path["nutrients.energy"].canonical_value == Decimal("0")
    assert by_path["nutrients.energy"].presence_state == PresenceState.EXPLICIT_ZERO.value
    assert by_path["nutrients.protein"].presence_state == PresenceState.MISSING.value
    assert by_path["nutrients.unknown"].provider_raw_value_decimal == Decimal("7")
    assert by_path["nutrients.unknown"].metric_key is None

def test_profile_metadata_eans_servings_and_no_ean_event_link(db, user):
    _ingest(db, user, simple=False, summary=False)
    assert db.scalar(select(NutritionFoodProfile)) is not None
    snapshot = db.scalar(select(NutritionFoodSnapshot))
    assert snapshot.name == "Food"
    assert snapshot.is_verified is True
    identities = db.scalars(select(NutritionExternalIdentity).where(NutritionExternalIdentity.user_id == user.id)).all()
    assert {(item.namespace, item.identity_value) for item in identities} >= {
        ("yazio.product", "product-1"), ("yazio.ean", "111"), ("yazio.ean", "222")
    }
    servings = db.scalars(select(NutritionServingObservation)).all()
    assert {serving.serving_scope for serving in servings} == {ServingScope.PROFILE.value, ServingScope.EVENT.value}
    assert len([serving for serving in servings if serving.serving_scope == ServingScope.PROFILE.value]) == 2
    assert all(serving.food_snapshot_id == snapshot.id for serving in servings if serving.serving_scope == "profile")
    assert all(link.consumption_event_id is None for link in db.scalars(select(NutritionExternalIdentityLink)).all() if link.external_identity_id in {item.id for item in identities if item.namespace == "yazio.ean"})
    product_identity = next(item for item in identities if item.namespace == "yazio.product")
    event = db.scalar(select(NutritionConsumptionEvent))
    assert any(
        link.external_identity_id == product_identity.id and link.consumption_event_id == event.id
        for link in db.scalars(select(NutritionExternalIdentityLink)).all()
    )


def test_daily_summary_is_observation_only(db, user):
    _ingest(db, user, simple=False)
    summary_observations = db.scalars(select(NutritionSourceObservation).where(NutritionSourceObservation.observation_kind == ObservationKind.DAILY_SUMMARY.value)).all()
    assert len(summary_observations) == 1
    assert summary_observations[0].local_date == DAY
    assert db.scalar(select(func.count()).select_from(NutritionConsumptionEvent)) == 1
    assert db.scalar(select(func.count()).select_from(NutritionDailyProjection)) == 0
    summary_fields = db.scalars(
        select(NutritionFieldObservation).where(
            NutritionFieldObservation.source_observation_id == summary_observations[0].id
        )
    ).all()
    goal = next(field for field in summary_fields if field.provider_field_path == "energy_goal")
    assert goal.metric_key is None
    assert goal.canonical_value is None
    assert goal.canonical_unit is None
    assert goal.provider_raw_unit == "kcal"


def test_missing_daily_summary_is_marked_missing_and_unresolved(db, user):
    diary = _diary(simple=False)
    missing = replace(
        diary.daily_summaries[0],
        nutrients=YazioNutrientValues(),
        energy_goal=None,
        metadata={"provider_summary_missing": True},
    )
    _ingest(db, user, replace(diary, daily_summaries=(missing,)))
    summary = db.scalar(
        select(NutritionSourceObservation).where(
            NutritionSourceObservation.observation_kind == ObservationKind.DAILY_SUMMARY.value
        )
    )
    assert summary.presence_state == PresenceState.MISSING.value
    assert summary.coverage_state == CoverageState.PARTIAL.value
    assert summary.resolution_state == ResolutionState.UNRESOLVED.value

def test_absent_energy_goal_remains_missing_provider_field(db, user):
    diary = _diary(simple=False, summary=True)
    diary = replace(diary, daily_summaries=(replace(diary.daily_summaries[0], energy_goal=None),))
    _ingest(db, user, diary)
    summary = db.scalar(select(NutritionSourceObservation).where(NutritionSourceObservation.observation_kind == ObservationKind.DAILY_SUMMARY.value))
    goal = next(
        field
        for field in db.scalars(select(NutritionFieldObservation).where(NutritionFieldObservation.source_observation_id == summary.id)).all()
        if field.provider_field_path == "energy_goal"
    )
    assert goal.presence_state == PresenceState.MISSING.value
    assert goal.metric_key is None
    assert goal.provider_raw_unit is None


def test_present_zero_amount_marks_source_observation_explicit_zero(db, user):
    _ingest(db, user, _diary(product_amount=Decimal("0"), simple=False, summary=False))
    observation = db.scalar(select(NutritionSourceObservation).where(NutritionSourceObservation.source_namespace == "yazio.consumed_item"))
    assert observation.presence_state == PresenceState.EXPLICIT_ZERO.value


def test_product_identity_links_are_event_specific_and_retry_stable(db, user):
    diary = _diary(simple=False, summary=False)
    second = replace(diary.consumed_products[0], consumed_item_id="event-2")
    diary = replace(diary, consumed_products=(diary.consumed_products[0], second))
    connection = _connection(db, user)
    _ingest(db, user, diary, connection=connection)
    _ingest(db, user, diary, connection=connection)
    product = db.scalar(select(NutritionExternalIdentity).where(NutritionExternalIdentity.namespace == "yazio.product"))
    links = db.scalars(
        select(NutritionExternalIdentityLink).where(
            NutritionExternalIdentityLink.external_identity_id == product.id,
            NutritionExternalIdentityLink.consumption_event_id.is_not(None),
        )
    ).all()
    assert len(links) == 2
    assert len({link.link_role for link in links}) == 2
    assert all(link.link_role.startswith("product_event:") for link in links)


def test_stale_profile_snapshot_binds_event_while_current_stays_newer(db, user):
    connection = _connection(db, user)
    _ingest(db, user, _diary(profile_energy=Decimal("3"), updated_at=datetime(2026, 9, 3, 10, 0)), connection=connection)
    current_id = db.scalar(select(NutritionFoodProfile)).current_snapshot_id
    _ingest(db, user, _diary(profile_energy=Decimal("4"), updated_at=datetime(2026, 9, 1, 10, 0)), connection=connection)
    event = db.scalar(select(NutritionConsumptionEvent).order_by(NutritionConsumptionEvent.revision.desc()))
    assert event.revision == 2
    assert event.food_snapshot_id != current_id
    assert db.scalar(select(NutritionFoodProfile)).current_snapshot_id == current_id


def test_profile_without_provider_timestamp_advances_current(db, user):
    connection = _connection(db, user)
    _ingest(db, user, _diary(profile_energy=Decimal("3"), updated_at=datetime(2026, 9, 3, 10, 0)), connection=connection)
    _ingest(db, user, _diary(profile_energy=Decimal("4"), updated_at=None), connection=connection)
    profile = db.scalar(select(NutritionFoodProfile))
    current = db.scalar(select(NutritionFoodSnapshot).where(NutritionFoodSnapshot.id == profile.current_snapshot_id))
    assert current.provider_updated_at is None


def test_unknown_profile_nutrients_have_no_invented_raw_unit(db, user):
    diary = _diary(simple=False, summary=False)
    profile = replace(diary.product_profiles[0], nutrients=replace(diary.product_profiles[0].nutrients, salt=Decimal("1")))
    diary = replace(diary, product_profiles=(profile,))
    _ingest(db, user, diary)
    fields = db.scalars(select(NutritionFieldObservation).where(NutritionFieldObservation.source_observation_id == db.scalar(select(NutritionFoodSnapshot)).source_observation_id)).all()
    by_path = {field.provider_field_path: field for field in fields}
    assert by_path["nutrients.mystery"].provider_raw_unit is None
    assert by_path["nutrients.salt"].metric_key is None
    assert by_path["nutrients.salt"].canonical_unit is None
    assert by_path["nutrients.salt"].provider_raw_unit == "g"


def test_civil_datetime_is_naive_and_timezone_is_metadata(db, user):
    _ingest(db, user, simple=False, summary=False)
    observation = db.scalar(select(NutritionSourceObservation).where(NutritionSourceObservation.source_namespace == "yazio.consumed_item"))
    event = db.scalar(select(NutritionConsumptionEvent))
    assert observation.provider_civil_datetime == datetime(2026, 9, 1, 12, 34, 56)
    assert observation.provider_civil_datetime.tzinfo is None
    assert observation.provider_timezone == "UTC+02:00"
    assert event.provider_civil_datetime.tzinfo is None


def test_provider_derived_field_roles_lineage_and_provenance(db, user):
    _ingest(db, user, simple=False, summary=False)
    fields = db.scalars(select(NutritionFieldObservation)).all()
    assert {field.observation_role for field in fields} >= {ObservationRole.PROVIDER.value, ObservationRole.DERIVED.value}
    derived = next(field for field in fields if field.observation_role == ObservationRole.DERIVED.value)
    assert derived.derived_from_field_observation_id is not None
    assert derived.lineage_state == LineageState.CONFIRMED.value
    provenance = db.scalars(select(NutritionProvenance)).all()
    assert provenance
    assert all(sum(target is not None for target in (item.consumption_event_id, item.food_snapshot_id, item.serving_observation_id, item.field_observation_id)) == 1 for item in provenance)


def test_repeated_diary_is_idempotent_and_changed_content_revises(db, user):
    connection = _connection(db, user)
    first = _ingest(db, user, _diary(), connection=connection)
    counts = [db.scalar(select(func.count()).select_from(model)) for model in (NutritionSourceObservation, NutritionConsumptionEvent, NutritionFoodSnapshot)]
    second = _ingest(db, user, _diary(), connection=connection)
    assert second.id != first.id
    assert [db.scalar(select(func.count()).select_from(model)) for model in (NutritionSourceObservation, NutritionConsumptionEvent, NutritionFoodSnapshot)] == counts
    _ingest(db, user, _diary(token="different-filtered-value"), connection=connection)
    assert db.scalar(select(func.count()).select_from(NutritionConsumptionEvent)) == counts[1]
    _ingest(db, user, _diary(profile_energy=Decimal("3")), connection=connection)
    assert db.scalar(select(func.count()).select_from(NutritionFoodSnapshot)) == 2
    assert db.scalar(select(func.count()).select_from(NutritionConsumptionEvent)) == 3
    assert db.scalar(select(NutritionConsumptionEvent).order_by(NutritionConsumptionEvent.revision.desc())).revision == 2
def test_older_profile_snapshot_is_history_and_does_not_rewind_current(db, user):
    connection = _connection(db, user)
    _ingest(db, user, _diary(updated_at=datetime(2026, 9, 2, 10, 0)), connection=connection)
    _ingest(db, user, _diary(profile_energy=Decimal("3"), updated_at=datetime(2026, 9, 3, 10, 0)), connection=connection)
    current_id = db.scalar(select(NutritionFoodProfile)).current_snapshot_id
    _ingest(db, user, _diary(updated_at=datetime(2026, 9, 1, 10, 0)), connection=connection)
    current = db.scalar(select(NutritionFoodSnapshot).where(NutritionFoodSnapshot.id == current_id))
    assert db.scalar(select(NutritionFoodProfile)).current_snapshot_id == current_id
    assert current.provider_updated_at == datetime(2026, 9, 3, 10, 0)

def test_locked_profile_refreshes_pointer_before_provider_time_ordering(db, user):
    connection = _connection(db, user)
    _ingest(db, user, _diary(profile_energy=Decimal("2"), updated_at=datetime(2026, 9, 1, 10, 0)), connection=connection)
    first = db.scalar(select(NutritionFoodSnapshot))
    _ingest(db, user, _diary(profile_energy=Decimal("3"), updated_at=datetime(2026, 9, 2, 10, 0)), connection=connection)
    second = db.scalar(select(NutritionFoodSnapshot).where(NutritionFoodSnapshot.id != first.id))
    profile = db.scalar(select(NutritionFoodProfile))
    assert profile.current_snapshot_id == second.id
    db.execute(
        update(NutritionFoodProfile)
        .where(NutritionFoodProfile.id == profile.id)
        .values(current_snapshot_id=first.id)
        .execution_options(synchronize_session=False)
    )
    _ingest(db, user, _diary(profile_energy=Decimal("4"), updated_at=datetime(2026, 9, 1, 12, 0)), connection=connection)
    refreshed = db.scalar(select(NutritionFoodProfile))
    incoming = db.scalar(
        select(NutritionFoodSnapshot).where(NutritionFoodSnapshot.provider_updated_at == datetime(2026, 9, 1, 12, 0))
    )
    assert refreshed.current_snapshot_id == incoming.id


def test_cross_user_source_instance_is_rejected(db, user):
    with pytest.raises(ValueError, match="source_instance_id"):
        ingest_yazio_food_diary(
            db,
            user_id=uuid4(),
            source_instance_id=_connection(db, user).id,
            requested_start=DAY,
            requested_end=DAY,
            diary=_diary(),
        )


def test_caller_owned_transaction_flushes_and_rollback_removes_domain_rows(db, user):
    connection = _connection(db, user)
    _ingest(db, user, _diary(), connection=connection)
    assert db.scalar(select(func.count()).select_from(NutritionIngestionRun)) == 1
    db.rollback()
    assert db.scalar(select(func.count()).select_from(NutritionIngestionRun)) == 0
    assert db.scalar(select(func.count()).select_from(NutritionSourceObservation)) == 0
