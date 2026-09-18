from __future__ import annotations

import io
from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analytics.micronutrient_shadow import read_canonical_micronutrient_period
from app.models import User
from app.nutrition.enums import CoverageState, LineageState, PresenceState, ResolutionState
from app.nutrition.models import (
    NutritionConsumptionEvent,
    NutritionFieldObservation,
    NutritionSourceObservation,
    NutritionSourceTombstone,
)
from app.nutrition.resolution import (
    CANONICAL_NUTRITION_METRICS,
    DAILY_PROJECTION_METRICS,
    NUTRIENT_READ_PROVIDER_RESOLVERS,
    PROVIDER_RESOLVERS,
    resolve_daily_nutrient,
    resolve_daily_nutrients,
)
from app.nutrition.resolution.discovery import discover_nutrition_provider_metadata
from app.nutrition.resolution.read_context import NutritionEvidenceIndex
from app.services.apple_health_nutrition_ingestion import apple_health_source_instance_id
from app.services.import_service import persist_apple_health_stream

DAY = date(2026, 8, 17)
NEXT_DAY = date(2026, 8, 18)
SOURCE_ID = apple_health_source_instance_id(UUID("11111111-1111-1111-1111-111111111111"))


def _record(type_: str, value: str, unit: str, day: date = DAY) -> str:
    return (
        f'<Record type="{type_}" value="{value}" unit="{unit}" '
        f'startDate="{day.isoformat()} 10:00:00 +0000" '
        f'endDate="{day.isoformat()} 10:30:00 +0000" />'
    )


def _correlation(key: str, records: str, day: date = DAY) -> str:
    return (
        f'<Correlation type="HKCorrelationTypeIdentifierFood" sourceName="N3" '
        f'startDate="{day.isoformat()} 10:00:00 +0000" '
        f'endDate="{day.isoformat()} 10:30:00 +0000">'
        f'<MetadataEntry key="HKExternalUUID" value="{key}" />'
        f'{records}</Correlation>'
    )


def _payload(*correlations: str, legacy_records: str = "") -> bytes:
    return f"<HealthData>{''.join(correlations)}{legacy_records}</HealthData>".encode()


def _persist(db: Session, user: User, payload: bytes) -> None:
    persist_apple_health_stream(
        db,
        user,
        io.BytesIO(payload),
        "application/xml",
        "n3-test",
        atomic=True,
    )


def _resolve(db: Session, user: User, metric_key: str, local_date: date = DAY):
    return resolve_daily_nutrient(
        db,
        provider_key="apple_health",
        user_id=user.id,
        source_instance_id=apple_health_source_instance_id(user.id),
        local_date=local_date,
        metric_key=metric_key,
    )


def test_apple_resolver_sums_multiple_food_events_with_decimal_exactness(db: Session, user: User):
    _persist(
        db,
        user,
        _payload(
            _correlation(
                "meal-1",
                _record("HKQuantityTypeIdentifierDietaryProtein", "20", "g"),
            ),
            _correlation(
                "meal-2",
                _record("HKQuantityTypeIdentifierDietaryProtein", "0.333", "g"),
            ),
        ),
    )

    candidate = _resolve(db, user, "protein_g")

    assert candidate.value == Decimal("20.333")
    assert candidate.unit == "g"
    assert candidate.coverage_state is CoverageState.PARTIAL
    assert candidate.resolution_state is ResolutionState.RESOLVED
    assert candidate.lineage_state is LineageState.CONFIRMED


@pytest.mark.parametrize(
    ("metric_key", "type_", "value", "unit", "expected_unit"),
    (
        ("dietary_energy_kcal", "HKQuantityTypeIdentifierDietaryEnergyConsumed", "600", "kcal", "kcal"),
        ("protein_g", "HKQuantityTypeIdentifierDietaryProtein", "30", "g", "g"),
        ("carbohydrates_g", "HKQuantityTypeIdentifierDietaryCarbohydrates", "80", "g", "g"),
        ("fat_g", "HKQuantityTypeIdentifierDietaryFatTotal", "20", "g", "g"),
        ("sodium_mg", "HKQuantityTypeIdentifierDietarySodium", "500", "mg", "mg"),
        ("vitamin_c_mg", "HKQuantityTypeIdentifierDietaryVitaminC", "4", "mg", "mg"),
    ),
)
def test_apple_resolver_reads_multiple_canonical_nutrients(
    db: Session,
    user: User,
    metric_key: str,
    type_: str,
    value: str,
    unit: str,
    expected_unit: str,
):
    _persist(db, user, _payload(_correlation("meal", _record(type_, value, unit))))

    candidate = _resolve(db, user, metric_key)

    assert candidate.value == Decimal(value)
    assert candidate.unit == expected_unit
    assert candidate.metric_key == metric_key


def test_apple_explicit_zero_is_not_missing(db: Session, user: User):
    _persist(
        db,
        user,
        _payload(
            _correlation(
                "zero",
                _record("HKQuantityTypeIdentifierDietaryVitaminC", "0", "mg"),
            )
        ),
    )

    candidate = _resolve(db, user, "vitamin_c_mg")

    assert candidate.value == Decimal("0")
    assert candidate.presence_state is PresenceState.EXPLICIT_ZERO


def test_apple_missing_metric_is_not_zero(db: Session, user: User):
    _persist(
        db,
        user,
        _payload(
            _correlation(
                "protein-only",
                _record("HKQuantityTypeIdentifierDietaryProtein", "20", "g"),
            )
        ),
    )

    candidate = _resolve(db, user, "sodium_mg")

    assert candidate.value is None
    assert candidate.presence_state is PresenceState.MISSING


def test_apple_unknown_provider_field_is_ignored(db: Session, user: User):
    _persist(
        db,
        user,
        _payload(
            _correlation(
                "unknown",
                _record("HKQuantityTypeIdentifierFutureNutrient", "9", "g"),
            )
        ),
    )

    candidate = _resolve(db, user, "protein_g")

    assert candidate.value is None
    assert candidate.presence_state is PresenceState.MISSING
    assert all(item.metric_key == "protein_g" for item in candidate.source_lineage)


def test_apple_wrong_unit_is_not_summed(db: Session, user: User):
    _persist(
        db,
        user,
        _payload(_correlation("bad-unit", _record("HKQuantityTypeIdentifierDietaryProtein", "20", "g"))),
    )
    field = db.scalar(select(NutritionFieldObservation))
    assert field is not None
    field.canonical_unit = "mg"
    db.flush()

    candidate = _resolve(db, user, "protein_g")

    assert candidate.value is None
    assert candidate.presence_state is PresenceState.MISSING
    assert candidate.resolution_state is ResolutionState.UNRESOLVED


def test_apple_cross_user_isolation(db: Session, user: User):
    other = User(username="apple-other", password_hash="hash", timezone="UTC")
    db.add(other)
    db.flush()
    _persist(db, user, _payload(_correlation("owned", _record("HKQuantityTypeIdentifierDietaryProtein", "20", "g"))))

    candidate = _resolve(db, other, "protein_g")

    assert candidate.value is None
    assert candidate.user_id == other.id


def test_apple_forged_source_instance_fails_closed(db: Session, user: User):
    with pytest.raises(ValueError, match="source_instance_id"):
        resolve_daily_nutrient(
            db,
            provider_key="apple_health",
            user_id=user.id,
            source_instance_id=SOURCE_ID,
            local_date=DAY,
            metric_key="protein_g",
        )


def test_apple_other_day_is_excluded(db: Session, user: User):
    _persist(
        db,
        user,
        _payload(
            _correlation("day-1", _record("HKQuantityTypeIdentifierDietaryProtein", "20", "g"), DAY),
            _correlation("day-2", _record("HKQuantityTypeIdentifierDietaryProtein", "30", "g"), NEXT_DAY),
        ),
    )

    candidate = _resolve(db, user, "protein_g", DAY)

    assert candidate.value == Decimal("20")


def test_apple_top_level_legacy_record_is_not_double_counted(db: Session, user: User):
    _persist(
        db,
        user,
        _payload(
            _correlation("meal", _record("HKQuantityTypeIdentifierDietaryProtein", "20", "g")),
            legacy_records=_record("HKQuantityTypeIdentifierDietaryProtein", "20", "g"),
        ),
    )

    candidate = _resolve(db, user, "protein_g")

    assert candidate.value == Decimal("20")
    assert db.scalar(select(func.count()).select_from(NutritionConsumptionEvent)) == 1


def test_apple_uncertain_contributor_propagates_lineage(db: Session, user: User):
    _persist(db, user, _payload(_correlation("uncertain", _record("HKQuantityTypeIdentifierDietaryProtein", "20", "g"))))
    field = db.scalar(select(NutritionFieldObservation))
    assert field is not None
    field.lineage_state = LineageState.UNCERTAIN.value
    db.flush()

    candidate = _resolve(db, user, "protein_g")

    assert candidate.value == Decimal("20")
    assert candidate.lineage_state is LineageState.UNCERTAIN


def test_apple_duplicate_fields_are_not_silently_selected(db: Session, user: User):
    _persist(db, user, _payload(_correlation("duplicate", _record("HKQuantityTypeIdentifierDietaryProtein", "20", "g"))))
    source = db.scalar(select(NutritionSourceObservation))
    assert source is not None
    db.add(
        NutritionFieldObservation(
            user_id=user.id,
            source_observation_id=source.id,
            provider_field_path="duplicate.protein",
            provider_raw_value_decimal=Decimal("21"),
            provider_raw_unit="g",
            metric_key="protein_g",
            canonical_value=Decimal("21"),
            canonical_unit="g",
            observation_role="canonical",
            presence_state=PresenceState.SUPPLIED.value,
            coverage_state=CoverageState.PARTIAL.value,
            resolution_state=ResolutionState.RESOLVED.value,
            lineage_state=LineageState.CONFIRMED.value,
        )
    )
    db.flush()

    candidate = _resolve(db, user, "protein_g")

    assert candidate.value is None
    assert candidate.resolution_state is ResolutionState.DUPLICATE_CANDIDATE


def test_apple_source_tombstone_removes_current_event(db: Session, user: User):
    _persist(db, user, _payload(_correlation("deleted", _record("HKQuantityTypeIdentifierDietaryProtein", "20", "g"))))
    source = db.scalar(select(NutritionSourceObservation))
    assert source is not None
    db.add(
        NutritionSourceTombstone(
            user_id=user.id,
            provider_key="apple_health",
            source_instance_id=source.source_instance_id,
            source_namespace=source.source_namespace,
            source_record_id=source.source_record_id,
            source_observation_id=source.id,
            tombstone_kind="deleted",
        )
    )
    db.flush()

    candidate = _resolve(db, user, "protein_g")

    assert candidate.value is None
    assert candidate.presence_state is PresenceState.MISSING


def test_apple_revised_event_only_resolves_current_date(db: Session, user: User):
    _persist(db, user, _payload(_correlation("moved", _record("HKQuantityTypeIdentifierDietaryProtein", "20", "g"), DAY)))
    _persist(db, user, _payload(_correlation("moved", _record("HKQuantityTypeIdentifierDietaryProtein", "30", "g"), NEXT_DAY)))

    old_day = _resolve(db, user, "protein_g", DAY)
    new_day = _resolve(db, user, "protein_g", NEXT_DAY)

    assert old_day.value is None
    assert new_day.value == Decimal("30")


def test_apple_resolver_is_read_only(db: Session, user: User):
    _persist(db, user, _payload(_correlation("readonly", _record("HKQuantityTypeIdentifierDietaryProtein", "20", "g"))))
    before = {
        model: db.scalar(select(func.count()).select_from(model))
        for model in (NutritionSourceObservation, NutritionConsumptionEvent, NutritionFieldObservation)
    }

    _resolve(db, user, "protein_g")

    after = {
        model: db.scalar(select(func.count()).select_from(model))
        for model in (NutritionSourceObservation, NutritionConsumptionEvent, NutritionFieldObservation)
    }
    assert after == before


def test_daily_reader_returns_full_catalog_in_registry_order(db: Session, user: User):
    candidates = resolve_daily_nutrients(
        db,
        provider_key="apple_health",
        user_id=user.id,
        source_instance_id=apple_health_source_instance_id(user.id),
        local_date=DAY,
    )

    assert tuple(candidate.metric_key for candidate in candidates) == tuple(CANONICAL_NUTRITION_METRICS)
    assert len(candidates) == len(CANONICAL_NUTRITION_METRICS)


def test_daily_projection_metric_set_remains_exactly_seven():
    assert len(DAILY_PROJECTION_METRICS) == 7
    assert tuple(DAILY_PROJECTION_METRICS) == (
        "dietary_energy_kcal",
        "protein_g",
        "carbohydrates_g",
        "fat_g",
        "fiber_g",
        "sugar_g",
        "saturated_fat_g",
    )


@pytest.mark.parametrize("metric_key", ["salt", "unknown_metric"])
def test_daily_reader_rejects_unsupported_metric(metric_key: str, db: Session, user: User):
    with pytest.raises(ValueError, match="metric"):
        _resolve(db, user, metric_key)


def test_daily_reader_rejects_duplicate_metric_keys(db: Session, user: User):
    with pytest.raises(ValueError, match="duplicate"):
        resolve_daily_nutrients(
            db,
            provider_key="apple_health",
            user_id=user.id,
            source_instance_id=apple_health_source_instance_id(user.id),
            local_date=DAY,
            metric_keys=("protein_g", "protein_g"),
        )


def test_daily_reader_rejects_unknown_provider(db: Session, user: User):
    with pytest.raises(LookupError, match="provider"):
        resolve_daily_nutrient(
            db,
            provider_key="not-a-provider",
            user_id=user.id,
            source_instance_id=apple_health_source_instance_id(user.id),
            local_date=DAY,
            metric_key="protein_g",
        )

def test_nutrient_reader_registry_keeps_apple_out_of_projection():
    assert set(PROVIDER_RESOLVERS) == {"google_health", "yazio"}
    assert set(NUTRIENT_READ_PROVIDER_RESOLVERS) == {"apple_health", "google_health", "yazio"}

def test_daily_reader_rejects_non_string_metric_keys(db: Session, user: User):
    with pytest.raises(ValueError, match="metric"):
        resolve_daily_nutrient(
            db,
            provider_key="apple_health",
            user_id=user.id,
            source_instance_id=SOURCE_ID,
            local_date=DAY,
            metric_key=None,
        )

    with pytest.raises(ValueError, match="metric"):
        resolve_daily_nutrients(
            db,
            provider_key="apple_health",
            user_id=user.id,
            source_instance_id=SOURCE_ID,
            local_date=DAY,
            metric_keys=(["protein_g"],),
        )
def test_apple_period_reuses_revision_validation_across_metrics(
    monkeypatch, db: Session, user: User
):
    import app.services.apple_health_nutrition_resolution as apple_resolution

    _persist(
        db,
        user,
        _payload(
            _correlation(
                "reuse-revision",
                _record("HKQuantityTypeIdentifierDietaryProtein", "20", "g")
                + _record("HKQuantityTypeIdentifierDietaryIron", "4", "mg"),
            )
        ),
    )
    calls = 0
    original = apple_resolution._revision_chain_is_valid

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(apple_resolution, "_revision_chain_is_valid", counted)

    result = apple_resolution.resolve_apple_health_period(
        db,
        user_id=user.id,
        source_instance_id=apple_health_source_instance_id(user.id),
        start=DAY,
        end=DAY,
        metric_keys=("protein_g", "iron_mg"),
    )

    assert result[DAY]["protein_g"].value == Decimal("20")
    assert result[DAY]["iron_mg"].value == Decimal("4")
    assert calls == 1


def test_apple_read_context_excludes_non_micronutrient_only_evidence(
    db: Session, user: User
) -> None:
    _persist(
        db,
        user,
        _payload(
            _correlation(
                "metadata-primary-only",
                _record("HKQuantityTypeIdentifierDietaryEnergyConsumed", "600", "kcal"),
            )
        ),
    )
    context = NutritionEvidenceIndex(
        user_id=user.id,
        provider_key="apple_health",
        source_instance_id=apple_health_source_instance_id(user.id),
        start=DAY,
        end=DAY,
    )

    read_canonical_micronutrient_period(
        db,
        user_id=user.id,
        provider_key="apple_health",
        source_instance_id=apple_health_source_instance_id(user.id),
        start=DAY,
        end=DAY,
        read_context=context,
    )
    metadata = discover_nutrition_provider_metadata(
        db,
        user_id=user.id,
        start=DAY,
        end=DAY,
        provider_key="apple_health",
        read_context=context,
    )

    assert metadata.providers == ()
