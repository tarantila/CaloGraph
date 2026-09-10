from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import DBAPIError, IntegrityError

import app.nutrition.projection.orchestration as orchestration
from app.database import SessionLocal
from app.models import User, YazioConnection
from app.nutrition.models import (
    NutritionConsumptionEvent,
    NutritionDailyProjection,
    NutritionDailyProjectionFact,
    NutritionDailyProjectionLineage,
    NutritionFieldObservation,
    NutritionFoodSnapshot,
    NutritionSourceObservation,
)
from app.nutrition.projection import ProjectionPersistenceStatus
from app.nutrition.projection.orchestration import (
    NutritionProjectionConcurrencyError,
    NutritionProjectionTransactionError,
    rebuild_nutrition_day,
)
from app.nutrition.resolution.metrics import CANONICAL_METRICS
from app.nutrition.resolution.reasons import EvidenceKind
from app.services.yazio_nutrition_ingestion import ingest_yazio_food_diary
from app.services.yazio_provider import (
    YazioConsumedProduct,
    YazioConsumedSimpleProduct,
    YazioDailyNutrientSummary,
    YazioFoodDiary,
    YazioNutrientValues,
    YazioProductProfile,
)
from app.source_priority.application import create_policy_with_rules
from app.source_priority.contracts import PriorityRuleSpec

DAY = date(2026, 9, 11)
POLICY_AT = datetime(2026, 9, 11, 12, tzinfo=UTC)


def _connection(db, user) -> YazioConnection:
    connection = YazioConnection(
        user_id=user.id,
        encrypted_email=b"encrypted-email",
        encrypted_password=b"encrypted-password",
        source_identifier="b6-test-source",
    )
    db.add(connection)
    db.flush()
    return connection


def _diary(
    *,
    nutrients: YazioNutrientValues | None = None,
    include_summary: bool = True,
    consumed_products: tuple[YazioConsumedProduct, ...] = (),
    consumed_simple_products: tuple[YazioConsumedSimpleProduct, ...] = (),
    product_profiles: tuple[YazioProductProfile, ...] = (),
) -> YazioFoodDiary:
    nutrient_values = nutrients or YazioNutrientValues(
        energy=Decimal("2100"),
        protein=Decimal("120"),
        carb=Decimal("230"),
        fat=Decimal("70"),
        fiber=Decimal("30"),
        sugar=Decimal("50"),
        saturated_fat=Decimal("20"),
    )
    summaries = (
        (
            YazioDailyNutrientSummary(
                local_date=DAY,
                nutrients=nutrient_values,
                energy_goal=None,
            ),
        )
        if include_summary
        else ()
    )
    return YazioFoodDiary(
        requested_start_day=DAY,
        requested_end_day=DAY,
        consumed_products=consumed_products,
        consumed_simple_products=consumed_simple_products,
        product_profiles=product_profiles,
        daily_summaries=summaries,
    )


def _ingest(
    db,
    user,
    connection: YazioConnection,
    *,
    diary: YazioFoodDiary | None = None,
) -> None:
    ingest_yazio_food_diary(
        db,
        user_id=user.id,
        source_instance_id=connection.id,
        requested_start=DAY,
        requested_end=DAY,
        diary=diary or _diary(),
    )
    db.commit()


def _policy(
    db,
    user,
    *,
    provider_key: str = "yazio",
    metric_key: str | None = None,
):
    return create_policy_with_rules(
        db,
        user.id,
        1,
        datetime(2026, 1, 1, tzinfo=UTC),
        (PriorityRuleSpec("nutrition", metric_key, provider_key, 1),),
    )


def _source_contribution(source: NutritionSourceObservation) -> SimpleNamespace:
    return SimpleNamespace(
        evidence_id=source.id,
        source_observation_id=source.id,
        metric_key="protein_g",
        evidence_kind=EvidenceKind.SOURCE_OBSERVATION,
    )


def _load_source_tokens(db, user, connection, source: NutritionSourceObservation):
    return orchestration._load_relevant_tokens(
        db,
        user_id=user.id,
        local_date=DAY,
        source_instance_id=connection.id,
        contributions=(_source_contribution(source),),
    )


def test_b6_event_source_with_null_date_fails_closed(db, user) -> None:
    connection = _connection(db, user)
    simple_product = YazioConsumedSimpleProduct(
        consumed_item_id="b6-scope-event",
        amount=Decimal("1"),
        provider_civil_datetime=datetime(2026, 9, 11, 12),
        local_date=DAY,
        daytime="lunch",
        nutrients=YazioNutrientValues(protein=Decimal("20")),
        serving=None,
        serving_quantity=None,
    )
    _ingest(
        db,
        user,
        connection,
        diary=_diary(include_summary=False, consumed_simple_products=(simple_product,)),
    )
    source = db.scalar(
        select(NutritionSourceObservation).where(
            NutritionSourceObservation.user_id == user.id,
            NutritionSourceObservation.source_namespace == "yazio.simple_product",
        )
    )
    assert source is not None
    source.local_date = None
    db.flush()

    with pytest.raises(ValueError, match="date/provider scope"):
        _load_source_tokens(db, user, connection, source)


def test_b6_summary_source_with_null_date_fails_closed(db, user) -> None:
    connection = _connection(db, user)
    _ingest(db, user, connection)
    source = db.scalar(
        select(NutritionSourceObservation).where(
            NutritionSourceObservation.user_id == user.id,
            NutritionSourceObservation.source_namespace == "yazio.daily_summary",
        )
    )
    assert source is not None
    source.local_date = None
    db.flush()

    with pytest.raises(ValueError, match="date/provider scope"):
        _load_source_tokens(db, user, connection, source)


def test_b6_unbound_global_profile_source_fails_closed(db, user) -> None:
    connection = _connection(db, user)
    _ingest(db, user, connection)
    source = db.scalar(
        select(NutritionSourceObservation).where(
            NutritionSourceObservation.user_id == user.id,
            NutritionSourceObservation.source_namespace == "yazio.daily_summary",
        )
    )
    assert source is not None
    source.observation_kind = "product_profile"
    source.local_date = None
    db.flush()

    with pytest.raises(ValueError, match="date/provider scope"):
        _load_source_tokens(db, user, connection, source)


def test_b6_global_reference_requires_provider_and_source_binding(db, user) -> None:
    connection = _connection(db, user)
    _ingest(db, user, connection)
    source = db.scalar(
        select(NutritionSourceObservation).where(
            NutritionSourceObservation.user_id == user.id,
            NutritionSourceObservation.source_namespace == "yazio.daily_summary",
        )
    )
    assert source is not None

    source.provider_key = "other-provider"
    db.flush()
    with pytest.raises(ValueError, match="date/provider scope"):
        _load_source_tokens(db, user, connection, source)
    db.rollback()

    source = db.scalar(
        select(NutritionSourceObservation).where(
            NutritionSourceObservation.user_id == user.id,
            NutritionSourceObservation.source_namespace == "yazio.daily_summary",
        )
    )
    assert source is not None
    source.source_instance_id = uuid4()
    db.flush()
    with pytest.raises(ValueError, match="date/provider scope"):
        _load_source_tokens(db, user, connection, source)


def test_b6_global_reference_from_other_user_fails_closed(db, user) -> None:
    connection = _connection(db, user)
    _ingest(db, user, connection)
    source = db.scalar(
        select(NutritionSourceObservation).where(
            NutritionSourceObservation.user_id == user.id,
            NutritionSourceObservation.source_namespace == "yazio.daily_summary",
        )
    )
    assert source is not None
    other_user = User(
        username="b6-other-user",
        password_hash="hash",
        timezone="UTC",
    )
    db.add(other_user)
    db.flush()

    with pytest.raises(ValueError, match="user scope"):
        orchestration._load_relevant_tokens(
            db,
            user_id=other_user.id,
            local_date=DAY,
            source_instance_id=connection.id,
            contributions=(_source_contribution(source),),
        )


def test_b6_builds_one_complete_daily_projection_from_one_policy_snapshot(db, user) -> None:
    connection = _connection(db, user)
    _ingest(db, user, connection)
    policy = _policy(db, user)
    db.commit()

    result = rebuild_nutrition_day(
        db,
        user_id=user.id,
        local_date=DAY,
        policy_at=POLICY_AT,
    )

    assert result.status is ProjectionPersistenceStatus.CREATED
    assert result.created is True
    projections = db.scalars(
        select(NutritionDailyProjection).where(
            NutritionDailyProjection.user_id == user.id,
            NutritionDailyProjection.local_date == DAY,
        )
    ).all()
    assert len(projections) == 1
    facts = db.scalars(
        select(NutritionDailyProjectionFact).where(
            NutritionDailyProjectionFact.user_id == user.id,
            NutritionDailyProjectionFact.projection_id == projections[0].id,
        )
    ).all()
    assert {fact.metric_key for fact in facts} == set(CANONICAL_METRICS)
    assert projections[0].priority_policy_id == policy.policy_id


def test_b6_uses_one_policy_at_and_collects_all_seven_metrics(
    db,
    user,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection(db, user)
    _ingest(db, user, connection)
    _policy(db, user)
    db.commit()
    policy_calls: list[datetime] = []
    metric_calls: list[str] = []
    original_policy = orchestration.get_effective_policy_snapshot
    original_collect = orchestration.collect_provider_candidates

    def policy_spy(db, user_id, policy_at):
        policy_calls.append(policy_at)
        return original_policy(db, user_id, policy_at)

    def collect_spy(db, **kwargs):
        metric_calls.append(kwargs["metric_key"])
        return original_collect(db, **kwargs)

    monkeypatch.setattr(orchestration, "get_effective_policy_snapshot", policy_spy)
    monkeypatch.setattr(orchestration, "collect_provider_candidates", collect_spy)

    result = rebuild_nutrition_day(db, user_id=user.id, local_date=DAY, policy_at=POLICY_AT)

    assert result.status is ProjectionPersistenceStatus.CREATED
    assert policy_calls == [POLICY_AT]
    assert metric_calls == list(CANONICAL_METRICS)


def test_b6_identical_rebuild_is_unchanged(db, user) -> None:
    connection = _connection(db, user)
    _ingest(db, user, connection)
    _policy(db, user)
    db.commit()

    first = rebuild_nutrition_day(db, user_id=user.id, local_date=DAY, policy_at=POLICY_AT)
    second = rebuild_nutrition_day(db, user_id=user.id, local_date=DAY, policy_at=POLICY_AT)

    assert first.status is ProjectionPersistenceStatus.CREATED
    assert second.status is ProjectionPersistenceStatus.UNCHANGED
    assert len(db.scalars(select(NutritionDailyProjection)).all()) == 1


def test_b6_unrelated_date_change_does_not_change_watermark(db, user) -> None:
    connection = _connection(db, user)
    _ingest(db, user, connection)
    _policy(db, user)
    db.commit()

    first = rebuild_nutrition_day(db, user_id=user.id, local_date=DAY, policy_at=POLICY_AT)
    unrelated_day = DAY + timedelta(days=1)
    unrelated_diary = YazioFoodDiary(
        requested_start_day=unrelated_day,
        requested_end_day=unrelated_day,
        consumed_products=(),
        consumed_simple_products=(),
        product_profiles=(),
        daily_summaries=(
            YazioDailyNutrientSummary(
                local_date=unrelated_day,
                nutrients=YazioNutrientValues(protein=Decimal("999")),
                energy_goal=None,
            ),
        ),
    )
    ingest_yazio_food_diary(
        db,
        user_id=user.id,
        source_instance_id=connection.id,
        requested_start=unrelated_day,
        requested_end=unrelated_day,
        diary=unrelated_diary,
    )
    db.commit()

    second = rebuild_nutrition_day(db, user_id=user.id, local_date=DAY, policy_at=POLICY_AT)

    assert first.status is ProjectionPersistenceStatus.CREATED
    assert second.status is ProjectionPersistenceStatus.UNCHANGED
    assert second.input_watermark == first.input_watermark




def test_b6_policy_missing_is_typed_no_mutation(db, user) -> None:
    connection = _connection(db, user)
    _ingest(db, user, connection)
    db.commit()

    result = rebuild_nutrition_day(db, user_id=user.id, local_date=DAY, policy_at=POLICY_AT)

    assert result.status is ProjectionPersistenceStatus.POLICY_MISSING
    assert result.created is False
    assert db.scalars(select(NutritionDailyProjection)).all() == []


def test_b6_no_value_day_persists_explicitly_unknown_facts(db, user) -> None:
    connection = _connection(db, user)
    _ingest(db, user, connection, diary=_diary(include_summary=False))
    _policy(db, user)
    db.commit()

    result = rebuild_nutrition_day(db, user_id=user.id, local_date=DAY, policy_at=POLICY_AT)

    assert result.status is ProjectionPersistenceStatus.CREATED
    facts = db.scalars(select(NutritionDailyProjectionFact)).all()
    assert len(facts) == len(CANONICAL_METRICS)
    assert all(fact.value is None for fact in facts)
    assert {fact.presence_state for fact in facts} == {"unknown"}


def test_b6_explicit_zero_remains_explicit_zero(db, user) -> None:
    connection = _connection(db, user)
    _ingest(
        db,
        user,
        connection,
        diary=_diary(
            nutrients=YazioNutrientValues(
                energy=Decimal("0"),
                protein=Decimal("0"),
                carb=Decimal("0"),
                fat=Decimal("0"),
                fiber=Decimal("0"),
                sugar=Decimal("0"),
                saturated_fat=Decimal("0"),
            )
        ),
    )
    _policy(db, user)
    db.commit()

    rebuild_nutrition_day(db, user_id=user.id, local_date=DAY, policy_at=POLICY_AT)

    facts = db.scalars(select(NutritionDailyProjectionFact)).all()
    assert len(facts) == len(CANONICAL_METRICS)
    assert all(fact.value == Decimal("0") for fact in facts)
    assert {fact.presence_state for fact in facts} == {"explicit_zero"}


def test_b6_unconfigured_provider_does_not_enter_manifest_lineage(db, user) -> None:
    connection = _connection(db, user)
    _ingest(db, user, connection)
    _policy(db, user, provider_key="google_health")
    db.commit()

    result = rebuild_nutrition_day(db, user_id=user.id, local_date=DAY, policy_at=POLICY_AT)

    assert result.status is ProjectionPersistenceStatus.CREATED
    assert db.scalars(select(NutritionDailyProjectionLineage)).all() == []


def test_b6_metric_specific_rule_limits_manifest_to_that_metric(db, user) -> None:
    connection = _connection(db, user)
    _ingest(db, user, connection)
    _policy(db, user, metric_key="protein_g")
    db.commit()

    result = rebuild_nutrition_day(db, user_id=user.id, local_date=DAY, policy_at=POLICY_AT)

    assert result.status is ProjectionPersistenceStatus.CREATED
    projections = db.scalars(select(NutritionDailyProjection)).all()
    lineage = db.scalars(
        select(NutritionDailyProjectionLineage).where(
            NutritionDailyProjectionLineage.user_id == user.id,
        )
    ).all()
    assert len(projections) == 1
    assert len(lineage) == 1
    protein_fact = db.scalar(
        select(NutritionDailyProjectionFact).where(
            NutritionDailyProjectionFact.projection_id == projections[0].id,
            NutritionDailyProjectionFact.metric_key == "protein_g",
        )
    )
    assert protein_fact is not None


def test_b6_relevant_source_revision_creates_next_version(db, user) -> None:
    connection = _connection(db, user)
    _ingest(db, user, connection)
    _policy(db, user)
    db.commit()

    first = rebuild_nutrition_day(db, user_id=user.id, local_date=DAY, policy_at=POLICY_AT)
    source = db.scalar(
        select(NutritionSourceObservation).where(
            NutritionSourceObservation.user_id == user.id,
            NutritionSourceObservation.provider_key == "yazio",
            NutritionSourceObservation.local_date == DAY,
        )
    )
    assert source is not None
    source.observation_fingerprint = "b" * 64
    db.commit()

    second = rebuild_nutrition_day(db, user_id=user.id, local_date=DAY, policy_at=POLICY_AT)

    assert first.status is ProjectionPersistenceStatus.CREATED
    assert second.status is ProjectionPersistenceStatus.CREATED
    assert second.projection_version == 2

def test_b6_policy_change_creates_next_version(db, user) -> None:
    connection = _connection(db, user)
    _ingest(db, user, connection)
    _policy(db, user)
    db.commit()

    first = rebuild_nutrition_day(db, user_id=user.id, local_date=DAY, policy_at=POLICY_AT)
    create_policy_with_rules(
        db,
        user.id,
        2,
        datetime(2026, 1, 15, tzinfo=UTC),
        (PriorityRuleSpec("nutrition", None, "yazio", 1),),
    )
    db.commit()

    second = rebuild_nutrition_day(
        db,
        user_id=user.id,
        local_date=DAY,
        policy_at=datetime(2026, 2, 1, tzinfo=UTC),
    )

    assert first.status is ProjectionPersistenceStatus.CREATED
    assert second.status is ProjectionPersistenceStatus.CREATED
    assert second.projection_version == 2


def test_b6_manifest_candidate_mismatch_fails_before_persistence(
    db,
    user,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection(db, user)
    _ingest(db, user, connection)
    _policy(db, user)
    db.commit()
    original_build_manifest = orchestration._build_manifest

    def missing_tokens(db, **kwargs):
        manifest = original_build_manifest(db, **kwargs)
        return replace(manifest, technical_evidence=())

    monkeypatch.setattr(orchestration, "_build_manifest", missing_tokens)

    with pytest.raises(ValueError, match="technical evidence token"):
        rebuild_nutrition_day(db, user_id=user.id, local_date=DAY, policy_at=POLICY_AT)

    assert db.scalars(select(NutritionDailyProjection)).all() == []


def test_b6_rejects_an_already_active_input_transaction(db, user) -> None:
    with pytest.raises(NutritionProjectionTransactionError):
        rebuild_nutrition_day(db, user_id=user.id, local_date=DAY, policy_at=POLICY_AT)

    db.rollback()


def test_b6_exposes_bounded_concurrency_error_type() -> None:
    assert issubclass(NutritionProjectionConcurrencyError, RuntimeError)


def test_b6_retries_only_postgres_serialization_and_projection_version_errors() -> None:
    db = SimpleNamespace(
        get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    )
    serialization = SimpleNamespace(
        sqlstate="40001",
        pgcode=None,
        diag=SimpleNamespace(constraint_name=None),
    )
    deadlock = SimpleNamespace(
        sqlstate=None,
        pgcode="40P01",
        diag=SimpleNamespace(constraint_name=None),
    )
    exact_constraint = SimpleNamespace(
        sqlstate=None,
        pgcode=None,
        diag=SimpleNamespace(constraint_name="uq_nutrition_projections_user_date_version"),
    )
    arbitrary_constraint = SimpleNamespace(
        sqlstate=None,
        pgcode=None,
        diag=SimpleNamespace(constraint_name="some_other_constraint"),
    )

    assert orchestration._is_retryable_database_error(db, DBAPIError("sql", {}, serialization))
    assert orchestration._is_retryable_database_error(db, DBAPIError("sql", {}, deadlock))
    assert orchestration._is_retryable_database_error(db, IntegrityError("sql", {}, exact_constraint))
    assert not orchestration._is_retryable_database_error(db, IntegrityError("sql", {}, arbitrary_constraint))


def test_b6_exhausts_exactly_three_whole_transaction_attempts(
    db,
    user,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = user.id
    db.rollback()
    attempts = 0

    def fail_attempt(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise RuntimeError("synthetic retryable failure")

    monkeypatch.setattr(orchestration, "_run_attempt", fail_attempt)
    monkeypatch.setattr(orchestration, "_is_retryable_database_error", lambda db, error: True)

    with pytest.raises(NutritionProjectionConcurrencyError):
        rebuild_nutrition_day(db, user_id=user_id, local_date=DAY, policy_at=POLICY_AT)

    assert attempts == 3
    assert not db.in_transaction()


def test_b6_simple_product_without_profile_remains_projectable(db, user) -> None:
    connection = _connection(db, user)
    simple_product = YazioConsumedSimpleProduct(
        consumed_item_id="b6-simple",
        amount=Decimal("1"),
        provider_civil_datetime=datetime(2026, 9, 11, 12),
        local_date=DAY,
        daytime="lunch",
        nutrients=YazioNutrientValues(protein=Decimal("20")),
        serving=None,
        serving_quantity=None,
        name="B6 simple",
    )
    _ingest(
        db,
        user,
        connection,
        diary=_diary(
            include_summary=False,
            consumed_simple_products=(simple_product,),
        ),
    )
    _policy(db, user)
    db.commit()

    first = rebuild_nutrition_day(db, user_id=user.id, local_date=DAY, policy_at=POLICY_AT)
    with SessionLocal() as second_db:
        second = rebuild_nutrition_day(
            second_db,
            user_id=user.id,
            local_date=DAY,
            policy_at=POLICY_AT,
        )

    assert first.status is ProjectionPersistenceStatus.CREATED
    assert second.status is ProjectionPersistenceStatus.UNCHANGED

def test_b6_product_event_uses_bound_global_profile_snapshot(
    db,
    user,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection(db, user)
    product = YazioConsumedProduct(
        consumed_item_id="b6-event",
        product_id="b6-product",
        amount=Decimal("1"),
        provider_civil_datetime=datetime(2026, 9, 11, 12),
        local_date=DAY,
        daytime="lunch",
        serving=None,
        serving_quantity=None,
    )
    profile = YazioProductProfile(
        product_id="b6-product",
        name="B6 product",
        producer="B6 producer",
        category="meal",
        base_unit="g",
        nutrients=YazioNutrientValues(protein=Decimal("20")),
        servings=(),
        eans=(),
        language="de",
        countries=("DE",),
        updated_at=datetime(2026, 9, 11, 11),
        is_verified=True,
        is_private=False,
        is_deleted=False,
    )
    _ingest(
        db,
        user,
        connection,
        diary=_diary(
            include_summary=False,
            consumed_products=(product,),
            product_profiles=(profile,),
        ),
    )
    _policy(db, user)
    db.commit()
    captured: dict[str, object] = {}
    original_persist = orchestration.persist_daily_projection

    def persist_spy(db, *, build_input):
        captured["build_input"] = build_input
        return original_persist(db, build_input=build_input)

    monkeypatch.setattr(orchestration, "persist_daily_projection", persist_spy)

    first = rebuild_nutrition_day(db, user_id=user.id, local_date=DAY, policy_at=POLICY_AT)

    assert first.status is ProjectionPersistenceStatus.CREATED
    event = db.scalar(
        select(NutritionConsumptionEvent).where(
            NutritionConsumptionEvent.user_id == user.id,
            NutritionConsumptionEvent.local_date == DAY,
        )
    )
    assert event is not None
    assert event.food_snapshot_id is not None
    snapshot = db.scalar(
        select(NutritionFoodSnapshot).where(
            NutritionFoodSnapshot.id == event.food_snapshot_id,
            NutritionFoodSnapshot.user_id == user.id,
        )
    )
    assert snapshot is not None
    profile_source = db.scalar(
        select(NutritionSourceObservation).where(
            NutritionSourceObservation.id == snapshot.source_observation_id,
            NutritionSourceObservation.user_id == user.id,
        )
    )
    assert profile_source is not None
    assert profile_source.local_date is None
    manifest = captured["build_input"].input_manifest
    assert any(
        token.source_observation_id == profile_source.id
        for token in manifest.technical_evidence
        if isinstance(token, orchestration.SourceObservationToken)
    )
    lineage_sources = {
        lineage.source_observation_id
        for lineage in db.scalars(select(NutritionDailyProjectionLineage)).all()
    }
    assert profile_source.id not in lineage_sources
    with SessionLocal() as second_db:
        second = rebuild_nutrition_day(
            second_db,
            user_id=user.id,
            local_date=DAY,
            policy_at=POLICY_AT,
        )
    assert second.status is ProjectionPersistenceStatus.UNCHANGED
    db.execute(
        delete(NutritionFieldObservation).where(
            NutritionFieldObservation.user_id == user.id,
            NutritionFieldObservation.derived_from_field_observation_id.is_not(None),
        )
    )
    db.execute(delete(NutritionFieldObservation).where(NutritionFieldObservation.user_id == user.id))
    db.execute(delete(User).where(User.id == user.id))
    db.commit()
