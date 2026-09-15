from __future__ import annotations

from dataclasses import fields
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.analytics.nutrition_projection import (
    CanonicalNutritionDay,
    CanonicalNutritionFact,
    NutritionProjectionReadError,
    NutritionProjectionReadState,
    read_canonical_nutrition_day,
)
from app.models import NutritionDailyProjectionFact, User
from app.nutrition.enums import (
    CoverageState,
    LineageState,
    PresenceState,
    ProjectionGranularity,
    ProjectionStatus,
    ResolutionState,
)
from app.nutrition.models import NutritionDailyProjection
from app.nutrition.repositories import (
    create_projection,
    create_projection_fact,
    set_projection_head,
)
from app.nutrition.resolution.metrics import CANONICAL_METRICS
from app.source_priority.application import create_policy_with_rules
from app.source_priority.contracts import PriorityRuleSpec

LOCAL_DATE = date(2026, 9, 11)
ALGORITHM = "nutrition-daily-v1"
WATERMARK = "nutrition-watermark-for-reader-contract"


def _policy(
    db,
    user: User,
    *,
    version: int = 1,
    effective_from: datetime | None = None,
):
    return create_policy_with_rules(
        db,
        user.id,
        version=version,
        effective_from=effective_from or datetime(2026, version, 1, tzinfo=UTC),
        rules=(
            PriorityRuleSpec(
                data_area="nutrition",
                metric_key=None,
                provider_key="yazio",
                priority_rank=1,
            ),
        ),
    )


def _ready_projection(
    db,
    user: User,
    *,
    values: dict[str, Decimal | None] | None = None,
    fact_overrides: dict[str, dict[str, object]] | None = None,
    status: ProjectionStatus = ProjectionStatus.READY,
    version: int = 1,
    local_date: date = LOCAL_DATE,
) -> NutritionDailyProjection:
    policy = _policy(db, user, version=version)
    projection = create_projection(
        db,
        user_id=user.id,
        local_date=local_date,
        projection_version=version,
        projection_algorithm_version=ALGORITHM,
        priority_policy_id=policy.policy_id,
        input_watermark=WATERMARK,
        projection_status=status.value,
    )
    values = values or {metric_key: Decimal("10.25") for metric_key in CANONICAL_METRICS}
    fact_overrides = fact_overrides or {}
    for metric_key, definition in CANONICAL_METRICS.items():
        value = values.get(metric_key, Decimal("10.25"))
        override = dict(fact_overrides.get(metric_key, {}))
        if value is None:
            defaults: dict[str, object] = {
                "value": None,
                "unit": definition.canonical_unit,
                "selected_provider_key": None,
                "selected_granularity": None,
                "presence_state": PresenceState.UNKNOWN.value,
                "coverage_state": CoverageState.UNKNOWN.value,
                "resolution_state": ResolutionState.UNRESOLVED.value,
                "lineage_state": LineageState.UNKNOWN.value,
            }
        else:
            defaults = {
                "value": value,
                "unit": definition.canonical_unit,
                "selected_provider_key": "yazio",
                "selected_granularity": ProjectionGranularity.SUMMARY.value,
                "presence_state": PresenceState.SUPPLIED.value,
                "coverage_state": CoverageState.COMPLETE.value,
                "resolution_state": ResolutionState.RESOLVED.value,
                "lineage_state": LineageState.CONFIRMED.value,
            }
        defaults.update(override)
        create_projection_fact(
            db,
            user_id=user.id,
            projection_id=projection.id,
            metric_key=metric_key,
            **defaults,
        )
    set_projection_head(db, user.id, local_date, projection.id)
    db.commit()
    return projection


def _other_user(db) -> User:
    other = User(username="reader-contract-other", password_hash="synthetic-password-hash")
    db.add(other)
    db.flush()
    return other


def test_missing_projection_head_is_not_projected_and_has_no_identity_or_facts(db, user):
    day = read_canonical_nutrition_day(db, user.id, LOCAL_DATE)

    assert day.user_id == user.id
    assert day.local_date == LOCAL_DATE
    assert day.state is NutritionProjectionReadState.NOT_PROJECTED
    assert day.facts == ()
    assert day.projection_id is None
    assert day.projection_version is None
    assert day.projection_status is None
    assert day.has_primary_evidence is False
    assert day.has_any_nutrition_value is False
    assert day.calorie_value_available is False
    assert day.calorie_coverage_complete is False
    assert day.calorie_resolution_resolved is False
    assert day.calorie_usable is False


def test_ready_projection_returns_exactly_registry_metrics_in_order_with_identity(db, user):
    projection = _ready_projection(db, user)

    day = read_canonical_nutrition_day(db, user.id, LOCAL_DATE)

    assert day.user_id == user.id
    assert day.local_date == LOCAL_DATE
    assert day.state is NutritionProjectionReadState.READY
    assert day.projection_id == projection.id
    assert day.projection_version == projection.projection_version == 1
    assert day.projection_status is ProjectionStatus.READY
    assert type(day.facts) is tuple
    assert tuple(fact.metric_key for fact in day.facts) == tuple(CANONICAL_METRICS)
    assert len(day.facts) == 7
    assert all(fact.value == Decimal("10.25") for fact in day.facts)
    assert all(type(fact.value) is Decimal for fact in day.facts)
    assert tuple(fact.unit for fact in day.facts) == tuple(
        definition.canonical_unit for definition in CANONICAL_METRICS.values()
    )

    for fact in day.facts:
        assert fact.presence_state is PresenceState.SUPPLIED
        assert fact.coverage_state is CoverageState.COMPLETE
        assert fact.resolution_state is ResolutionState.RESOLVED
        assert fact.lineage_state is LineageState.CONFIRMED
        assert fact.provider_key == "yazio"
        assert fact.granularity is ProjectionGranularity.SUMMARY


def test_ready_with_seven_no_value_facts_is_distinct_from_missing_head(db, user):
    projection = _ready_projection(
        db,
        user,
        values={metric_key: None for metric_key in CANONICAL_METRICS},
    )

    day = read_canonical_nutrition_day(db, user.id, LOCAL_DATE)

    assert day.state is NutritionProjectionReadState.READY
    assert day.projection_id == projection.id
    assert len(day.facts) == 7
    assert all(fact.value is None for fact in day.facts)
    assert day.has_primary_evidence is False
    assert day.has_any_nutrition_value is False
    assert day.calorie_value_available is False
    assert day.calorie_usable is False


def test_explicit_zero_is_preserved_as_decimal_value_and_primary_evidence(db, user):
    projection = _ready_projection(
        db,
        user,
        values={
            **{metric_key: None for metric_key in CANONICAL_METRICS},
            "protein_g": Decimal("0"),
        },
        fact_overrides={"protein_g": {"presence_state": PresenceState.EXPLICIT_ZERO.value}},
    )

    day = read_canonical_nutrition_day(db, user.id, LOCAL_DATE)
    fact = next(fact for fact in day.facts if fact.metric_key == "protein_g")

    assert day.projection_id == projection.id
    assert fact.value == Decimal("0")
    assert type(fact.value) is Decimal
    assert fact.presence_state is PresenceState.EXPLICIT_ZERO
    assert day.has_primary_evidence is True
    assert day.has_any_nutrition_value is True


def test_partial_coverage_unresolved_resolution_and_uncertain_lineage_are_typed_and_preserved(
    db, user
):
    _ready_projection(
        db,
        user,
        fact_overrides={
            "dietary_energy_kcal": {
                "coverage_state": CoverageState.PARTIAL.value,
                "resolution_state": ResolutionState.UNRESOLVED.value,
                "lineage_state": LineageState.UNCERTAIN.value,
            },
        },
    )

    day = read_canonical_nutrition_day(db, user.id, LOCAL_DATE)
    calorie = next(fact for fact in day.facts if fact.metric_key == "dietary_energy_kcal")

    assert calorie.coverage_state is CoverageState.PARTIAL
    assert calorie.resolution_state is ResolutionState.UNRESOLVED
    assert calorie.lineage_state is LineageState.UNCERTAIN


def test_mixed_metric_providers_are_preserved_without_a_day_level_provider(db, user):
    _ready_projection(
        db,
        user,
        fact_overrides={
            "dietary_energy_kcal": {"selected_provider_key": "google_health"},
            "protein_g": {"selected_provider_key": "yazio"},
            "fat_g": {"selected_provider_key": "google_health"},
        },
    )

    day = read_canonical_nutrition_day(db, user.id, LOCAL_DATE)
    providers = {fact.metric_key: fact.provider_key for fact in day.facts}

    assert providers["dietary_energy_kcal"] == "google_health"
    assert providers["protein_g"] == "yazio"
    assert providers["fat_g"] == "google_health"
    assert "provider_key" not in {field.name for field in fields(CanonicalNutritionDay)}


@pytest.mark.parametrize("boundary", ("missing", "extra", "wrong_unit", "non_ready"))
def test_structural_projection_boundaries_fail_closed(db, user, boundary):
    if boundary == "missing":
        projection = _ready_projection(db, user)
        fact = (
            db.query(NutritionDailyProjectionFact)
            .filter_by(
                projection_id=projection.id,
                metric_key="protein_g",
            )
            .one()
        )
        db.delete(fact)
        db.commit()
    elif boundary == "extra":
        projection = _ready_projection(db, user)
        create_projection_fact(
            db,
            user_id=user.id,
            projection_id=projection.id,
            metric_key="salt",
            value=Decimal("1"),
            unit="g",
            selected_provider_key="yazio",
            selected_granularity=ProjectionGranularity.SUMMARY.value,
            presence_state=PresenceState.SUPPLIED.value,
            coverage_state=CoverageState.COMPLETE.value,
            resolution_state=ResolutionState.RESOLVED.value,
            lineage_state=LineageState.CONFIRMED.value,
        )
        db.commit()
    elif boundary == "wrong_unit":
        projection = _ready_projection(db, user)
        fact = (
            db.query(NutritionDailyProjectionFact)
            .filter_by(
                projection_id=projection.id,
                metric_key="protein_g",
            )
            .one()
        )
        fact.unit = "kcal"
        db.commit()
    else:
        _ready_projection(db, user, status=ProjectionStatus.FAILED)

    with pytest.raises(NutritionProjectionReadError):
        read_canonical_nutrition_day(db, user.id, LOCAL_DATE)


@pytest.mark.parametrize(
    "field_name",
    (
        "presence_state",
        "coverage_state",
        "resolution_state",
        "lineage_state",
        "selected_granularity",
    ),
)
def test_unknown_persisted_enum_values_fail_closed(db, user, field_name):
    projection = _ready_projection(db, user)
    fact = (
        db.query(NutritionDailyProjectionFact)
        .filter_by(
            projection_id=projection.id,
            metric_key="protein_g",
        )
        .one()
    )
    setattr(fact, field_name, "not-a-known-enum-value")

    with db.no_autoflush, pytest.raises(NutritionProjectionReadError):
        read_canonical_nutrition_day(db, user.id, LOCAL_DATE)


@pytest.mark.parametrize(
    "invalid_value", (Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity"))
)
def test_non_finite_persisted_decimal_values_fail_closed(db, user, invalid_value):
    projection = _ready_projection(db, user)
    fact = (
        db.query(NutritionDailyProjectionFact)
        .filter_by(
            projection_id=projection.id,
            metric_key="dietary_energy_kcal",
        )
        .one()
    )
    fact.value = invalid_value

    with db.no_autoflush, pytest.raises(NutritionProjectionReadError):
        read_canonical_nutrition_day(db, user.id, LOCAL_DATE)


def test_wrong_user_and_wrong_date_never_return_another_day(db, user):
    projection = _ready_projection(db, user)
    other_user = _other_user(db)
    db.commit()

    wrong_user = read_canonical_nutrition_day(db, other_user.id, LOCAL_DATE)
    wrong_date = read_canonical_nutrition_day(db, user.id, date(2026, 9, 12))

    assert projection.id not in {wrong_user.projection_id, wrong_date.projection_id}
    assert wrong_user.state is NutritionProjectionReadState.NOT_PROJECTED
    assert wrong_date.state is NutritionProjectionReadState.NOT_PROJECTED
    assert wrong_user.facts == wrong_date.facts == ()


def test_facts_from_another_projection_scope_are_not_returned(db, user):
    first = _ready_projection(
        db,
        user,
        version=1,
        values={metric_key: Decimal("10.25") for metric_key in CANONICAL_METRICS},
    )
    second = _ready_projection(
        db,
        user,
        version=2,
        values={metric_key: Decimal("20.50") for metric_key in CANONICAL_METRICS},
    )
    assert first.id != second.id
    set_projection_head(db, user.id, LOCAL_DATE, first.id)
    db.commit()

    day = read_canonical_nutrition_day(db, user.id, LOCAL_DATE)

    assert day.projection_id == first.id
    assert len(day.facts) == 7
    assert all(fact.value == Decimal("10.25") for fact in day.facts)


def test_projection_fact_query_excludes_wrong_user_rows(db, user):
    projection = _ready_projection(db, user)
    other_user = _other_user(db)
    db.commit()

    connection = db.connection()
    connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
    db.add(
        NutritionDailyProjectionFact(
            user_id=other_user.id,
            projection_id=projection.id,
            metric_key="salt",
            value=Decimal("999"),
            unit="g",
            selected_provider_key="other",
            selected_granularity=ProjectionGranularity.SUMMARY.value,
            presence_state=PresenceState.SUPPLIED.value,
            coverage_state=CoverageState.COMPLETE.value,
            resolution_state=ResolutionState.RESOLVED.value,
            lineage_state=LineageState.CONFIRMED.value,
        )
    )
    db.flush()
    db.commit()
    db.connection().exec_driver_sql("PRAGMA foreign_keys=ON")

    day = read_canonical_nutrition_day(db, user.id, LOCAL_DATE)

    assert day.state is NutritionProjectionReadState.READY
    assert len(day.facts) == len(CANONICAL_METRICS)
    assert tuple(fact.metric_key for fact in day.facts) == tuple(CANONICAL_METRICS)


def test_projection_and_fact_scope_cannot_cross_users(db, user):
    other = _other_user(db)
    requested = _ready_projection(
        db,
        user,
        values={metric_key: Decimal("10.25") for metric_key in CANONICAL_METRICS},
    )
    _ready_projection(
        db,
        other,
        values={metric_key: Decimal("20.50") for metric_key in CANONICAL_METRICS},
    )

    day = read_canonical_nutrition_day(db, user.id, LOCAL_DATE)

    assert day.state is NutritionProjectionReadState.READY
    assert day.projection_id == requested.id
    assert tuple(fact.value for fact in day.facts) == (Decimal("10.25"),) * 7


def test_decimal_precision_and_identity_are_preserved_without_float_conversion(db, user):
    exact = Decimal("1234.567890123456")
    _ready_projection(db, user, values={"dietary_energy_kcal": exact})

    day = read_canonical_nutrition_day(db, user.id, LOCAL_DATE)
    calorie = next(fact for fact in day.facts if fact.metric_key == "dietary_energy_kcal")

    assert type(calorie.value) is Decimal
    assert calorie.value == exact


def test_returned_contract_exposes_no_source_lineage_or_metadata_fields(db, user):
    _ready_projection(db, user)

    day_fields = {field.name for field in fields(CanonicalNutritionDay)}
    fact_fields = {field.name for field in fields(CanonicalNutritionFact)}

    assert day_fields == {
        "user_id",
        "local_date",
        "state",
        "projection_id",
        "projection_version",
        "projection_status",
        "facts",
        "has_primary_evidence",
        "has_any_nutrition_value",
        "calorie_value_available",
        "calorie_coverage_complete",
        "calorie_resolution_resolved",
        "calorie_usable",
    }
    assert not day_fields & {
        "source_observation_id",
        "source_instance_id",
        "lineage_id",
        "diagnostic_metadata",
        "lineage_metadata",
    }
    assert fact_fields == {
        "metric_key",
        "unit",
        "value",
        "presence_state",
        "coverage_state",
        "resolution_state",
        "lineage_state",
        "provider_key",
        "granularity",
    }
    assert not fact_fields & {
        "source_observation_id",
        "source_instance_id",
        "lineage_id",
        "diagnostic_metadata",
        "lineage_metadata",
    }


def test_all_derived_flags_are_true_for_complete_resolved_calorie_evidence(db, user):
    _ready_projection(db, user)

    day = read_canonical_nutrition_day(db, user.id, LOCAL_DATE)

    assert day.has_primary_evidence is True
    assert day.has_any_nutrition_value is True
    assert day.calorie_value_available is True
    assert day.calorie_coverage_complete is True
    assert day.calorie_resolution_resolved is True
    assert day.calorie_usable is True


@pytest.mark.parametrize(
    ("coverage_state", "resolution_state"),
    (
        (CoverageState.PARTIAL, ResolutionState.RESOLVED),
        (CoverageState.COMPLETE, ResolutionState.UNRESOLVED),
    ),
)
def test_calorie_usable_requires_value_complete_coverage_and_resolved_resolution(
    db,
    user,
    coverage_state,
    resolution_state,
):
    _ready_projection(
        db,
        user,
        fact_overrides={
            "dietary_energy_kcal": {
                "coverage_state": coverage_state.value,
                "resolution_state": resolution_state.value,
            },
        },
    )

    day = read_canonical_nutrition_day(db, user.id, LOCAL_DATE)

    assert day.calorie_value_available is True
    assert day.calorie_coverage_complete is (coverage_state is CoverageState.COMPLETE)
    assert day.calorie_resolution_resolved is (resolution_state is ResolutionState.RESOLVED)
    assert day.calorie_usable is False


def test_uncertain_lineage_does_not_control_calorie_usability(db, user):
    _ready_projection(
        db,
        user,
        fact_overrides={"dietary_energy_kcal": {"lineage_state": LineageState.UNCERTAIN.value}},
    )

    day = read_canonical_nutrition_day(db, user.id, LOCAL_DATE)
    calorie = next(fact for fact in day.facts if fact.metric_key == "dietary_energy_kcal")

    assert calorie.lineage_state is LineageState.UNCERTAIN
    assert day.calorie_value_available is True
    assert day.calorie_coverage_complete is True
    assert day.calorie_resolution_resolved is True
    assert day.calorie_usable is True


def test_read_contract_is_user_scoped_even_when_projection_ids_are_known(db, user):
    other = _other_user(db)
    _ready_projection(db, other)
    db.commit()

    day = read_canonical_nutrition_day(db, user.id, LOCAL_DATE)

    assert day.state is NutritionProjectionReadState.NOT_PROJECTED
    assert day.projection_id is None
    assert day.facts == ()
