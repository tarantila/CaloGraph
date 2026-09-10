from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import select

from app.models import YazioConnection
from app.nutrition.enums import (
    CoverageState,
    LineageState,
    ObservationKind,
    ObservationRole,
    PresenceState,
    ProjectionGranularity,
    ResolutionState,
)
from app.nutrition.models import (
    NutritionConsumptionEvent,
    NutritionExternalIdentity,
    NutritionFieldObservation,
    NutritionFoodSnapshot,
    NutritionSourceObservation,
    NutritionSourceTombstone,
)
from app.nutrition.resolution import ReasonCode
from app.services.yazio_nutrition_ingestion import ingest_yazio_food_diary
from app.services.yazio_nutrition_resolution import resolve_yazio_day, resolve_yazio_metric
from app.services.yazio_provider import (
    YazioConsumedProduct,
    YazioConsumedSimpleProduct,
    YazioDailyNutrientSummary,
    YazioFoodDiary,
    YazioNutrientValues,
    YazioProductProfile,
)

PROVIDER = "yazio"
DAY = date(2026, 9, 1)
NEXT_DAY = date(2026, 9, 2)
METRIC = "protein_g"


def _connection(db, user, *, source_identifier: str = "b2-source") -> YazioConnection:
    connection = YazioConnection(
        user_id=user.id,
        encrypted_email=b"encrypted-email",
        encrypted_password=b"encrypted-password",
        source_identifier=source_identifier,
    )
    db.add(connection)
    db.flush()
    return connection


def _profile(product_id: str, protein: Decimal | None, *, deleted: bool = False) -> YazioProductProfile:
    return YazioProductProfile(
        product_id=product_id,
        name=f"Product {product_id}",
        producer="Producer",
        category="Category",
        base_unit="g",
        nutrients=YazioNutrientValues(energy=Decimal("10"), protein=protein),
        servings=(),
        eans=(),
        language="en",
        countries=("DE",),
        updated_at=datetime(2026, 8, 31, 10, 0),
        is_verified=True,
        is_private=False,
        is_deleted=deleted,
    )


def _product(event_id: str, product_id: str, local_date: date = DAY, amount: Decimal = Decimal("1")) -> YazioConsumedProduct:
    return YazioConsumedProduct(
        consumed_item_id=event_id,
        product_id=product_id,
        amount=amount,
        provider_civil_datetime=datetime.combine(local_date, datetime.min.time()),
        local_date=local_date,
        daytime="lunch",
        serving=None,
        serving_quantity=None,
    )


def _simple(event_id: str, protein: Decimal | None, local_date: date = DAY) -> YazioConsumedSimpleProduct:
    return YazioConsumedSimpleProduct(
        consumed_item_id=event_id,
        amount=Decimal("1"),
        provider_civil_datetime=datetime.combine(local_date, datetime.min.time()),
        local_date=local_date,
        daytime="breakfast",
        nutrients=YazioNutrientValues(protein=protein),
        serving=None,
        serving_quantity=None,
        name="Simple",
    )


def _diary(
    *,
    products: tuple[YazioConsumedProduct, ...] = (),
    simple_products: tuple[YazioConsumedSimpleProduct, ...] = (),
    profiles: tuple[YazioProductProfile, ...] = (),
    summary_protein: Decimal | None = None,
    summary_date: date = DAY,
) -> YazioFoodDiary:
    summaries = (
        YazioDailyNutrientSummary(
            local_date=summary_date,
            nutrients=YazioNutrientValues(protein=summary_protein),
            energy_goal=None,
        ),
    ) if summary_protein is not None else ()
    return YazioFoodDiary(
        requested_start_day=summary_date,
        requested_end_day=summary_date,
        consumed_products=products,
        consumed_simple_products=simple_products,
        product_profiles=profiles,
        daily_summaries=summaries,
    )


def _ingest(db, user, connection, diary: YazioFoodDiary) -> None:
    ingest_yazio_food_diary(
        db,
        user_id=user.id,
        source_instance_id=connection.id,
        requested_start=diary.requested_start_day,
        requested_end=diary.requested_end_day,
        diary=diary,
    )


def _tombstone(
    db,
    user,
    connection,
    *,
    source_namespace: str,
    source_record_id: str | None = None,
    source_observation_id: UUID | None = None,
    external_identity_id: UUID | None = None,
    kind: str = "deleted",
) -> NutritionSourceTombstone:
    tombstone = NutritionSourceTombstone(
        user_id=user.id,
        provider_key=PROVIDER,
        source_instance_id=connection.id,
        source_namespace=source_namespace,
        source_record_id=source_record_id,
        source_observation_id=source_observation_id,
        external_identity_id=external_identity_id,
        tombstone_kind=kind,
    )
    db.add(tombstone)
    db.flush()
    return tombstone


def _event(db, user, event_id: str = "event-1") -> NutritionConsumptionEvent:
    return db.scalar(
        select(NutritionConsumptionEvent).where(
            NutritionConsumptionEvent.user_id == user.id,
            NutritionConsumptionEvent.provider_key == PROVIDER,
            NutritionConsumptionEvent.logical_event_key == event_id,
        )
    )


def _event_field(db, event: NutritionConsumptionEvent, metric: str = METRIC) -> NutritionFieldObservation:
    return db.scalar(
        select(NutritionFieldObservation).where(
            NutritionFieldObservation.user_id == event.user_id,
            NutritionFieldObservation.source_observation_id == event.source_observation_id,
            NutritionFieldObservation.metric_key == metric,
        ).order_by(NutritionFieldObservation.id)
    )


def test_product_derived_uncertainty_falls_back_to_summary(db, user):
    connection = _connection(db, user)
    _ingest(
        db,
        user,
        connection,
        _diary(
            products=(_product("event-product", "product-1"),),
            profiles=(_profile("product-1", Decimal("20")),),
            summary_protein=Decimal("20"),
        ),
    )

    result = resolve_yazio_metric(
        db, user_id=user.id, source_instance_id=connection.id, local_date=DAY, metric_key=METRIC
    )

    assert result.value == Decimal("20")
    assert result.selected_granularity is ProjectionGranularity.SUMMARY
    assert result.reason_code is ReasonCode.SUMMARY_FALLBACK_UNCERTAIN_LINEAGE


def test_simple_product_confirmed_event_wins_over_summary(db, user):
    connection = _connection(db, user)
    _ingest(
        db,
        user,
        connection,
        _diary(simple_products=(_simple("simple-1", Decimal("20")),), summary_protein=Decimal("20")),
    )

    result = resolve_yazio_metric(
        db, user_id=user.id, source_instance_id=connection.id, local_date=DAY, metric_key=METRIC
    )

    assert result.value == Decimal("20")
    assert result.selected_granularity is ProjectionGranularity.EVENT
    assert result.reason_code is ReasonCode.EVENT_COMPLETE_CONFIRMED
    assert result.lineage_state is LineageState.CONFIRMED
    event = _event(db, user, "simple-1")
    event_field = _event_field(db, event)
    summary_source = db.scalar(
        select(NutritionSourceObservation).where(
            NutritionSourceObservation.user_id == user.id,
            NutritionSourceObservation.source_instance_id == connection.id,
            NutritionSourceObservation.local_date == DAY,
            NutritionSourceObservation.source_namespace == "yazio.daily_summary",
        )
    )
    assert event_field is not None
    assert summary_source is not None
    assert result.source_lineage[0].source_observation_id == event_field.source_observation_id
    assert summary_source.id in {
        item.source_observation_id for item in result.diagnostic_evidence
    }


def test_confirmed_simple_plus_uncertain_product_aggregates_but_summary_wins(db, user):
    connection = _connection(db, user)
    _ingest(
        db,
        user,
        connection,
        _diary(
            products=(_product("product-event", "product-1"),),
            simple_products=(_simple("simple-event", Decimal("10")),),
            profiles=(_profile("product-1", Decimal("20")),),
            summary_protein=Decimal("30"),
        ),
    )

    result = resolve_yazio_metric(
        db, user_id=user.id, source_instance_id=connection.id, local_date=DAY, metric_key=METRIC
    )

    assert result.value == Decimal("30")
    assert result.selected_granularity is ProjectionGranularity.SUMMARY
    assert result.lineage_state is LineageState.CONFIRMED
    assert result.parity_diagnostic is not None


def test_missing_event_field_creates_partial_coverage(db, user):
    connection = _connection(db, user)
    _ingest(
        db,
        user,
        connection,
        _diary(
            products=(_product("present", "product-present"), _product("missing", "product-missing")),
            profiles=(_profile("product-present", Decimal("20")), _profile("product-missing", None)),
        ),
    )

    result = resolve_yazio_metric(
        db, user_id=user.id, source_instance_id=connection.id, local_date=DAY, metric_key=METRIC
    )

    assert result.value == Decimal("20")
    assert result.selected_granularity is ProjectionGranularity.PARTIAL_EVENT
    assert result.coverage_state is CoverageState.PARTIAL
    assert result.reason_code is ReasonCode.PARTIAL_EVENTS_NO_SUMMARY


def test_explicit_zero_is_complete_and_zero_only_is_not_missing(db, user):
    connection = _connection(db, user)
    _ingest(
        db,
        user,
        connection,
        _diary(simple_products=(_simple("zero-a", Decimal("0")), _simple("zero-b", Decimal("0")))),
    )

    result = resolve_yazio_metric(
        db, user_id=user.id, source_instance_id=connection.id, local_date=DAY, metric_key=METRIC
    )

    assert result.value == Decimal("0")
    assert result.presence_state is PresenceState.EXPLICIT_ZERO
    assert result.coverage_state is CoverageState.COMPLETE


def test_event_and_summary_are_never_added(db, user):
    connection = _connection(db, user)
    _ingest(
        db,
        user,
        connection,
        _diary(simple_products=(_simple("simple-100", Decimal("100")),), summary_protein=Decimal("100")),
    )

    result = resolve_yazio_metric(
        db, user_id=user.id, source_instance_id=connection.id, local_date=DAY, metric_key=METRIC
    )

    assert result.value == Decimal("100")


def test_current_event_revision_replaces_old_revision(db, user):
    connection = _connection(db, user)
    _ingest(
        db,
        user,
        connection,
        _diary(products=(_product("revisioned", "product-1", amount=Decimal("1")),), profiles=(_profile("product-1", Decimal("10")),)),
    )
    _ingest(
        db,
        user,
        connection,
        _diary(products=(_product("revisioned", "product-1", amount=Decimal("2")),), profiles=(_profile("product-1", Decimal("10")),)),
    )

    result = resolve_yazio_metric(
        db, user_id=user.id, source_instance_id=connection.id, local_date=DAY, metric_key=METRIC
    )

    assert result.value == Decimal("20")
    assert len(result.source_lineage) == 1


def test_tombstoned_current_revision_does_not_fall_back_to_old_revision(db, user):
    connection = _connection(db, user)
    _ingest(
        db,
        user,
        connection,
        _diary(
            products=(_product("revisioned", "product-1", amount=Decimal("1")),),
            profiles=(_profile("product-1", Decimal("10")),),
            summary_protein=Decimal("99"),
        ),
    )
    _ingest(
        db,
        user,
        connection,
        _diary(
            products=(_product("revisioned", "product-1", amount=Decimal("2")),),
            profiles=(_profile("product-1", Decimal("10")),),
            summary_protein=Decimal("99"),
        ),
    )
    current = _event(db, user, "revisioned")
    current_source = db.get(NutritionSourceObservation, current.source_observation_id)
    _tombstone(
        db,
        user,
        connection,
        source_namespace=current_source.source_namespace,
        source_record_id=current_source.source_record_id,
        source_observation_id=current_source.id,
    )

    result = resolve_yazio_metric(
        db, user_id=user.id, source_instance_id=connection.id, local_date=DAY, metric_key=METRIC
    )

    assert result.value == Decimal("99")
    assert result.selected_granularity is ProjectionGranularity.SUMMARY
    assert result.reason_code is ReasonCode.SUMMARY_FALLBACK_UNCERTAIN_LINEAGE


def test_tombstoning_only_old_revision_keeps_newer_revision(db, user):
    connection = _connection(db, user)
    _ingest(
        db,
        user,
        connection,
        _diary(products=(_product("revisioned", "product-1", amount=Decimal("1")),), profiles=(_profile("product-1", Decimal("10")),)),
    )
    old = _event(db, user, "revisioned")
    old_source = db.get(NutritionSourceObservation, old.source_observation_id)
    _ingest(
        db,
        user,
        connection,
        _diary(products=(_product("revisioned", "product-1", amount=Decimal("2")),), profiles=(_profile("product-1", Decimal("10")),)),
    )
    _tombstone(
        db,
        user,
        connection,
        source_namespace=old_source.source_namespace,
        source_record_id=old_source.source_record_id,
        source_observation_id=old_source.id,
    )

    result = resolve_yazio_metric(
        db, user_id=user.id, source_instance_id=connection.id, local_date=DAY, metric_key=METRIC
    )

    assert result.value == Decimal("20")


def test_product_snapshot_delete_does_not_invalidate_historical_binding(db, user):
    connection = _connection(db, user)
    _ingest(
        db,
        user,
        connection,
        _diary(products=(_product("product-event", "product-1"),), profiles=(_profile("product-1", Decimal("20")),)),
    )
    snapshot = db.scalar(select(NutritionFoodSnapshot))
    snapshot.is_deleted = True
    db.flush()

    result = resolve_yazio_metric(
        db, user_id=user.id, source_instance_id=connection.id, local_date=DAY, metric_key=METRIC
    )

    assert result.value == Decimal("20")
    assert result.selected_granularity is ProjectionGranularity.EVENT
    assert result.lineage_state is LineageState.UNCERTAIN


def test_product_identity_tombstone_does_not_invalidate_historical_event(db, user):
    connection = _connection(db, user)
    _ingest(
        db,
        user,
        connection,
        _diary(products=(_product("product-event", "product-1"),), profiles=(_profile("product-1", Decimal("20")),)),
    )
    identity = db.scalar(
        select(NutritionExternalIdentity).where(
            NutritionExternalIdentity.user_id == user.id,
            NutritionExternalIdentity.namespace == "yazio.product",
        )
    )
    _tombstone(
        db,
        user,
        connection,
        source_namespace="yazio.product",
        external_identity_id=identity.id,
    )

    result = resolve_yazio_metric(
        db, user_id=user.id, source_instance_id=connection.id, local_date=DAY, metric_key=METRIC
    )

    assert result.value == Decimal("20")


def test_tombstoned_historical_product_field_falls_back_to_summary(db, user):
    connection = _connection(db, user)
    _ingest(
        db,
        user,
        connection,
        _diary(
            products=(_product("product-event", "product-1"),),
            profiles=(_profile("product-1", Decimal("20")),),
            summary_protein=Decimal("20"),
        ),
    )
    event = _event(db, user, "product-event")
    field = _event_field(db, event)
    derived_parent = db.get(NutritionFieldObservation, field.derived_from_field_observation_id)
    parent_source = db.get(NutritionSourceObservation, derived_parent.source_observation_id)
    _tombstone(
        db,
        user,
        connection,
        source_namespace=parent_source.source_namespace,
        source_record_id=parent_source.source_record_id,
        source_observation_id=parent_source.id,
    )

    result = resolve_yazio_metric(
        db, user_id=user.id, source_instance_id=connection.id, local_date=DAY, metric_key=METRIC
    )

    assert result.value == Decimal("20")
    assert result.selected_granularity is ProjectionGranularity.SUMMARY


def test_simple_product_ignores_unrelated_product_tombstone(db, user):
    connection = _connection(db, user)
    _ingest(db, user, connection, _diary(simple_products=(_simple("simple-event", Decimal("20")),)))
    _tombstone(db, user, connection, source_namespace="yazio.product", source_record_id="unrelated-product")

    result = resolve_yazio_metric(
        db, user_id=user.id, source_instance_id=connection.id, local_date=DAY, metric_key=METRIC
    )

    assert result.value == Decimal("20")
    assert result.selected_granularity is ProjectionGranularity.EVENT


def test_duplicate_event_fields_fall_back_to_summary(db, user):
    connection = _connection(db, user)
    _ingest(
        db,
        user,
        connection,
        _diary(simple_products=(_simple("simple-event", Decimal("20")),), summary_protein=Decimal("20")),
    )
    event = _event(db, user, "simple-event")
    db.add(
        NutritionFieldObservation(
            user_id=user.id,
            source_observation_id=event.source_observation_id,
            provider_field_path="duplicate.protein",
            provider_raw_value_decimal=Decimal("20"),
            provider_raw_unit="g",
            metric_key=METRIC,
            canonical_value=Decimal("20"),
            canonical_unit="g",
            observation_role=ObservationRole.PROVIDER.value,
            presence_state=PresenceState.SUPPLIED.value,
            coverage_state=CoverageState.COMPLETE.value,
            resolution_state=ResolutionState.RESOLVED.value,
            lineage_state=LineageState.CONFIRMED.value,
        )
    )
    db.flush()

    result = resolve_yazio_metric(
        db, user_id=user.id, source_instance_id=connection.id, local_date=DAY, metric_key=METRIC
    )

    assert result.value == Decimal("20")
    assert result.selected_granularity is ProjectionGranularity.SUMMARY
    assert result.reason_code is ReasonCode.SUMMARY_FALLBACK_DUPLICATE_CANDIDATE


def test_duplicate_summary_candidates_do_not_use_db_order(db, user):
    connection = _connection(db, user)
    _ingest(db, user, connection, _diary(summary_protein=Decimal("20")))
    summary = db.scalar(
        select(NutritionSourceObservation).where(
            NutritionSourceObservation.user_id == user.id,
            NutritionSourceObservation.observation_kind == ObservationKind.DAILY_SUMMARY.value,
        )
    )
    duplicate = NutritionSourceObservation(
        user_id=user.id,
        ingestion_run_id=summary.ingestion_run_id,
        provider_key=PROVIDER,
        source_instance_id=connection.id,
        source_namespace="yazio.daily_summary",
        source_record_id="duplicate-summary",
        source_revision=1,
        observation_fingerprint="d" * 64,
        observation_kind=ObservationKind.DAILY_SUMMARY.value,
        local_date=DAY,
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
        lineage_state=LineageState.CONFIRMED.value,
    )
    db.add(duplicate)
    db.flush()
    db.add(
        NutritionFieldObservation(
            user_id=user.id,
            source_observation_id=duplicate.id,
            provider_field_path="nutrients.protein",
            provider_raw_value_decimal=Decimal("20"),
            provider_raw_unit="g",
            metric_key=METRIC,
            canonical_value=Decimal("20"),
            canonical_unit="g",
            observation_role=ObservationRole.PROVIDER.value,
            presence_state=PresenceState.SUPPLIED.value,
            coverage_state=CoverageState.COMPLETE.value,
            resolution_state=ResolutionState.RESOLVED.value,
            lineage_state=LineageState.CONFIRMED.value,
        )
    )
    db.flush()

    result = resolve_yazio_metric(
        db, user_id=user.id, source_instance_id=connection.id, local_date=DAY, metric_key=METRIC
    )

    assert result.value is None
    assert result.reason_code is ReasonCode.SUMMARY_UNUSABLE


def test_cross_user_and_cross_date_events_are_isolated(db, user):
    other = type(user)(username="other-b2", password_hash="hash", timezone="UTC")
    db.add(other)
    db.flush()
    first_connection = _connection(db, user, source_identifier="first")
    other_connection = _connection(db, other, source_identifier="other")
    _ingest(db, user, first_connection, _diary(simple_products=(_simple("today", Decimal("20")),)))
    _ingest(
        db,
        user,
        first_connection,
        _diary(simple_products=(_simple("tomorrow", Decimal("99"), NEXT_DAY),), summary_date=NEXT_DAY),
    )
    _ingest(db, other, other_connection, _diary(simple_products=(_simple("foreign", Decimal("77")),)))

    result = resolve_yazio_metric(
        db, user_id=user.id, source_instance_id=first_connection.id, local_date=DAY, metric_key=METRIC
    )

    assert result.value == Decimal("20")

def test_foreign_source_instance_is_rejected_closed(db, user):
    _connection(db, user)
    foreign_user = type(user)(username="foreign-b2", password_hash="hash", timezone="UTC")
    db.add(foreign_user)
    db.flush()
    foreign = _connection(db, foreign_user, source_identifier="foreign")

    with pytest.raises(ValueError, match="source_instance"):
        resolve_yazio_metric(
            db, user_id=user.id, source_instance_id=foreign.id, local_date=DAY, metric_key=METRIC
        )


def test_unsupported_metric_returns_unsupported_candidate(db, user):
    connection = _connection(db, user)

    result = resolve_yazio_metric(
        db, user_id=user.id, source_instance_id=connection.id, local_date=DAY, metric_key="salt"
    )

    assert result.metric_key == "salt"
    assert result.value is None
    assert result.reason_code is ReasonCode.UNSUPPORTED_METRIC
    assert result.presence_state is PresenceState.UNSUPPORTED


def test_day_resolution_returns_one_candidate_per_canonical_metric(db, user):
    connection = _connection(db, user)
    _ingest(db, user, connection, _diary(simple_products=(_simple("event", Decimal("20")),)))

    result = resolve_yazio_day(
        db, user_id=user.id, source_instance_id=connection.id, local_date=DAY
    )

    assert set(result) == {
        "dietary_energy_kcal",
        "protein_g",
        "carbohydrates_g",
        "fat_g",
        "fiber_g",
        "sugar_g",
        "saturated_fat_g",
    }
    assert all(candidate.provider_key == PROVIDER for candidate in result.values())
