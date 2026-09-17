import io
from datetime import UTC, datetime
from decimal import Decimal
from xml.etree.ElementTree import ParseError

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.importers.apple_xml import (
    AppleFoodCorrelation,
    AppleFoodNutrient,
    AppleHealthRecord,
)
from app.importers.errors import ImportLimitError
from app.models import HealthSample, ImportBatch, User
from app.nutrition.enums import ObservationRole, PresenceState
from app.nutrition.models import (
    NutritionConsumptionEvent,
    NutritionExternalIdentity,
    NutritionFieldObservation,
    NutritionIngestionRun,
    NutritionSourceObservation,
)
from app.services import import_service
from app.services.apple_health_nutrition_ingestion import (
    create_apple_health_nutrition_run,
    persist_apple_food_correlation,
)
from app.services.import_service import persist_apple_health_stream

_APPLE_XML = b"""<HealthData>
  <Correlation type="HKCorrelationTypeIdentifierFood" sourceName="YAZIO" sourceVersion="9.1"
      startDate="2026-08-17 10:00:00 +0200" endDate="2026-08-17 10:30:00 +0200"
      creationDate="2026-08-17 10:31:00 +0200">
    <MetadataEntry key="HKFoodType" value="Lunch &amp; Bowl" />
    <MetadataEntry key="HKExternalUUID" value="food-123" />
    <Record type="HKQuantityTypeIdentifierDietaryEnergyConsumed" value="600" unit="kcal"
        startDate="2026-08-17 10:00:00 +0200" endDate="2026-08-17 10:30:00 +0200" />
    <Record type="HKQuantityTypeIdentifierDietaryProtein" value="30" unit="g"
        startDate="2026-08-17 10:00:00 +0200" endDate="2026-08-17 10:30:00 +0200" />
    <Record type="HKQuantityTypeIdentifierDietaryCarbohydrates" value="80" unit="g"
        startDate="2026-08-17 10:00:00 +0200" endDate="2026-08-17 10:30:00 +0200" />
    <Record type="HKQuantityTypeIdentifierDietaryFatTotal" value="20" unit="g"
        startDate="2026-08-17 10:00:00 +0200" endDate="2026-08-17 10:30:00 +0200" />
    <Record type="HKQuantityTypeIdentifierDietaryFatSaturated" value="5" unit="g"
        startDate="2026-08-17 10:00:00 +0200" endDate="2026-08-17 10:30:00 +0200" />
    <Record type="HKQuantityTypeIdentifierDietaryFiber" value="7" unit="g"
        startDate="2026-08-17 10:00:00 +0200" endDate="2026-08-17 10:30:00 +0200" />
    <Record type="HKQuantityTypeIdentifierDietarySugar" value="10" unit="g"
        startDate="2026-08-17 10:00:00 +0200" endDate="2026-08-17 10:30:00 +0200" />
    <Record type="HKQuantityTypeIdentifierDietarySodium" value="500" unit="mg"
        startDate="2026-08-17 10:00:00 +0200" endDate="2026-08-17 10:30:00 +0200" />
    <Record type="HKQuantityTypeIdentifierDietaryPhosphorus" value="200" unit="mg"
        startDate="2026-08-17 10:00:00 +0200" endDate="2026-08-17 10:30:00 +0200" />
    <Record type="HKQuantityTypeIdentifierDietaryIron" value="4" unit="mg"
        startDate="2026-08-17 10:00:00 +0200" endDate="2026-08-17 10:30:00 +0200" />
    <Record type="HKQuantityTypeIdentifierDietaryZinc" value="2" unit="mg"
        startDate="2026-08-17 10:00:00 +0200" endDate="2026-08-17 10:00:00 +0200" />
    <Record type="HKQuantityTypeIdentifierDietarySelenium" value="20" unit="ug"
        startDate="2026-08-17 10:00:00 +0200" endDate="2026-08-17 10:30:00 +0200" />
    <Record type="HKQuantityTypeIdentifierDietaryManganese" value="1" unit="mg"
        startDate="2026-08-17 10:00:00 +0200" endDate="2026-08-17 10:30:00 +0200" />
    <Record type="HKQuantityTypeIdentifierDietaryVitaminC" value="0" unit="mg"
        startDate="2026-08-17 10:00:00 +0200" endDate="2026-08-17 10:30:00 +0200" />
    <Record type="HKQuantityTypeIdentifierFutureNutrient" value="3" unit="g"
        startDate="2026-08-17 10:00:00 +0200" endDate="2026-08-17 10:30:00 +0200" />
  </Correlation>
  <Record type="HKQuantityTypeIdentifierDietaryProtein" value="30" unit="g"
      startDate="2026-08-17 10:00:00 +0200" endDate="2026-08-17 10:30:00 +0200" />
</HealthData>"""


def _persist(db: Session, user: User, payload: bytes = _APPLE_XML, *, atomic: bool = True):
    return persist_apple_health_stream(
        db,
        user,
        io.BytesIO(payload),
        "application/xml",
        "apple-test",
        atomic=atomic,
    )


def _nutrient(value: Decimal, *, raw_type: str = "HKQuantityTypeIdentifierDietaryProtein", unit: str = "g"):
    moment = datetime(2026, 8, 17, 10, 0, tzinfo=UTC)
    return AppleFoodNutrient(
        raw_type=raw_type,
        raw_value=str(value),
        value=value,
        unit=unit,
        start_at=moment,
        end_at=moment,
        source_name="Apple",
        source_version="1",
    )


def _direct_correlation(nutrient: AppleFoodNutrient) -> AppleFoodCorrelation:
    moment = datetime(2026, 8, 17, 10, 0, tzinfo=UTC)
    return AppleFoodCorrelation(
        source_name="Apple",
        source_version="1",
        device=None,
        start_at=moment,
        end_at=moment,
        creation_at=moment,
        food_name="Test",
        external_uuid=None,
        metadata=(),
        nutrients=(nutrient,),
    )


def _persist_direct_nutrient(
    db: Session,
    user: User,
    value: Decimal,
    *,
    raw_type: str = "HKQuantityTypeIdentifierDietaryProtein",
    unit: str = "g",
) -> NutritionFieldObservation:
    run = create_apple_health_nutrition_run(db, user_id=user.id)
    persist_apple_food_correlation(
        db,
        user_id=user.id,
        run=run,
        correlation=_direct_correlation(_nutrient(value, raw_type=raw_type, unit=unit)),
        timezone=user.timezone,
    )
    field = db.scalar(select(NutritionFieldObservation))
    assert field is not None
    return field

def test_food_correlation_creates_one_event_with_direct_canonical_fields(db: Session, user: User):
    _persist(db, user)

    events = db.scalars(select(NutritionConsumptionEvent)).all()
    sources = db.scalars(select(NutritionSourceObservation)).all()
    fields = db.scalars(select(NutritionFieldObservation)).all()
    identities = db.scalars(select(NutritionExternalIdentity)).all()

    assert len(events) == 1
    assert len(sources) == 1
    assert events[0].provider_key == "apple_health"
    assert events[0].provider_metadata["food_name"] == "Lunch & Bowl"
    assert events[0].provider_metadata["source_name"] == "YAZIO"
    assert events[0].canonical_start_at is not None
    assert len(fields) == 15
    assert {field.metric_key for field in fields if field.metric_key is not None} >= {
        "dietary_energy_kcal",
        "protein_g",
        "carbohydrates_g",
        "fat_g",
        "saturated_fat_g",
        "fiber_g",
        "sugar_g",
        "sodium_mg",
        "phosphorus_mg",
        "iron_mg",
        "zinc_mg",
        "selenium_ug",
        "manganese_mg",
        "vitamin_c_mg",
    }
    vitamin_c = next(field for field in fields if field.metric_key == "vitamin_c_mg")
    assert vitamin_c.canonical_value == Decimal("0")
    assert vitamin_c.presence_state == PresenceState.EXPLICIT_ZERO.value
    assert vitamin_c.observation_role == ObservationRole.CANONICAL.value
    unknown = next(field for field in fields if field.metric_key is None)
    assert unknown.provider_raw_value_decimal == Decimal("3")
    assert len(identities) == 1
    assert identities[0].provider_key == "apple_health"
    assert identities[0].namespace == "apple_health.external_uuid"
    assert identities[0].identity_value == "food-123"


def test_food_correlation_reimport_is_idempotent_and_does_not_double_count(db: Session, user: User):
    _persist(db, user)
    _persist(db, user)

    assert db.scalar(select(func.count()).select_from(NutritionConsumptionEvent)) == 1
    assert db.scalar(select(func.count()).select_from(NutritionFieldObservation)) == 15
    assert db.scalar(select(func.count()).select_from(NutritionExternalIdentity)) == 1
    assert db.scalar(select(func.count()).select_from(HealthSample)) == 14


def test_food_correlation_same_external_uuid_is_user_scoped(db: Session, user: User):
    other = User(username="other", password_hash="test", timezone="Europe/Berlin")
    db.add(other)
    db.flush()

    _persist(db, user)
    _persist(db, other)

    assert db.scalar(select(func.count()).select_from(NutritionConsumptionEvent)) == 2
    assert db.scalar(select(func.count()).select_from(NutritionExternalIdentity)) == 2
    assert {
        event.user_id for event in db.scalars(select(NutritionConsumptionEvent)).all()
    } == {user.id, other.id}

def test_empty_external_uuid_uses_safe_fingerprint_identity(db: Session, user: User):
    payload = _APPLE_XML.replace(b'value="food-123"', b'value=""')

    _persist(db, user, payload)

    event = db.scalar(select(NutritionConsumptionEvent))
    identity = db.scalar(select(NutritionExternalIdentity))
    assert event is not None
    assert event.logical_event_key.startswith("anonymous:")
    assert identity is not None
    assert identity.namespace == "apple_health.food_correlation"


def test_food_correlation_respects_record_limit(
    db: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "max_import_records", 1)

    with pytest.raises(ImportLimitError):
        _persist(db, user)

    assert db.scalar(select(func.count()).select_from(NutritionConsumptionEvent)) == 0
    assert db.scalar(select(func.count()).select_from(NutritionIngestionRun)) == 0


def test_food_correlation_atomic_failure_rolls_back_canonical_rows(db: Session, user: User):
    malformed = _APPLE_XML.replace(b"</HealthData>", b"<Record")

    with pytest.raises(ParseError):
        _persist(db, user, malformed, atomic=True)

    assert db.scalar(select(func.count()).select_from(NutritionConsumptionEvent)) == 0
    assert db.scalar(select(func.count()).select_from(NutritionFieldObservation)) == 0
    assert db.scalar(select(func.count()).select_from(NutritionIngestionRun)) == 0


def test_food_correlation_without_nutrients_is_not_complete(db: Session, user: User):
    payload = (
        b'<HealthData><Correlation type="HKCorrelationTypeIdentifierFood" sourceName="App" '
        b'startDate="2026-08-17 10:00:00 +0200" endDate="2026-08-17 10:30:00 +0200" />'
        b"</HealthData>"
    )
    _persist(db, user, payload)

    event = db.scalar(select(NutritionConsumptionEvent))
    run = db.scalar(select(NutritionIngestionRun))
    assert event is not None
    assert event.coverage_state == "partial"
    assert event.presence_state == PresenceState.MISSING.value
    assert run is not None
    assert run.coverage_state == "partial"


def test_food_correlation_atomic_database_failure_preserves_error(
    db: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
):
    def fail_sample_batch(*args: object, **kwargs: object) -> tuple[int, int, int]:
        del args, kwargs
        raise SQLAlchemyError("synthetic database failure")

    monkeypatch.setattr(import_service, "_persist_sample_batch", fail_sample_batch)

    with pytest.raises(SQLAlchemyError, match="synthetic database failure"):
        _persist(db, user)

    assert db.scalar(select(func.count()).select_from(NutritionConsumptionEvent)) == 0
    assert db.scalar(select(func.count()).select_from(NutritionIngestionRun)) == 0
    assert db.scalar(select(func.count()).select_from(ImportBatch)) == 0


@pytest.mark.parametrize(
    "value",
    [
        Decimal("999999999999.999999999999"),
        Decimal("0"),
        Decimal("12.345"),
    ],
)
def test_food_correlation_accepts_database_safe_decimal_values(
    db: Session,
    user: User,
    value: Decimal,
):
    field = _persist_direct_nutrient(db, user, value)

    assert field.provider_raw_value_decimal is not None
    assert field.observation_role in {
        ObservationRole.CANONICAL.value,
        ObservationRole.PROVIDER.value,
    }
    if value == Decimal("999999999999.999999999999"):
        assert field.canonical_value is None
        assert field.metric_key is None
    else:
        assert field.canonical_value == value
        assert field.observation_role == ObservationRole.CANONICAL.value
    if value != Decimal("999999999999.999999999999"):
        assert field.provider_raw_value_decimal == value

@pytest.mark.parametrize(
    "value",
    [
        Decimal("1000000000000"),
        Decimal("1E+100"),
        Decimal("9" * 120),
        Decimal("0.1234567890123"),
        Decimal("-1"),
        Decimal("NaN"),
        Decimal("Infinity"),
    ],
)
def test_food_correlation_rejects_database_unsafe_provider_decimals(
    db: Session,
    user: User,
    value: Decimal,
):
    field = _persist_direct_nutrient(db, user, value)

    assert field.provider_raw_value_decimal is None
    assert field.canonical_value is None
    assert field.metric_key is None
    assert field.observation_role == ObservationRole.PROVIDER.value


def test_unknown_food_nutrient_rejects_database_unsafe_decimal(
    db: Session,
    user: User,
):
    field = _persist_direct_nutrient(
        db,
        user,
        Decimal("1E+100"),
        raw_type="HKQuantityTypeIdentifierFutureNutrient",
        unit="g",
    )

    assert field.provider_raw_value_decimal is None
    assert field.canonical_value is None
    assert field.metric_key is None
    assert field.observation_role == ObservationRole.PROVIDER.value


def test_food_correlation_rejects_canonical_unit_conversion_overflow(
    db: Session,
    user: User,
):
    field = _persist_direct_nutrient(
        db,
        user,
        Decimal("2000000000"),
        raw_type="HKQuantityTypeIdentifierDietaryVitaminC",
        unit="g",
    )

    assert field.provider_raw_value_decimal == Decimal("2000000000")
    assert field.canonical_value is None
    assert field.metric_key is None
    assert field.observation_role == ObservationRole.PROVIDER.value



def test_food_correlation_xml_unit_conversion_overflow_is_provider_only(
    db: Session,
    user: User,
):
    payload = b"""<HealthData>
      <Correlation type="HKCorrelationTypeIdentifierFood" sourceName="Apple"
          startDate="2026-08-17 10:00:00 +0000" endDate="2026-08-17 10:30:00 +0000">
        <Record type="HKQuantityTypeIdentifierDietaryVitaminC" value="2000000000"
            unit="g" startDate="2026-08-17 10:00:00 +0000"
            endDate="2026-08-17 10:30:00 +0000" />
      </Correlation>
    </HealthData>"""

    summary = _persist(db, user, payload)
    field = db.scalar(select(NutritionFieldObservation))

    assert summary.status == "completed"
    assert field is not None
    assert field.provider_raw_value_decimal == Decimal("2000000000")
    assert field.canonical_value is None
    assert field.metric_key is None
    assert field.observation_role == ObservationRole.PROVIDER.value

def test_atomic_food_import_keeps_unsafe_nutrient_out_of_decimal_columns(
    db: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
):
    correlation = _direct_correlation(_nutrient(Decimal("1E+100")))

    def records(*args: object, **kwargs: object):
        del args, kwargs
        return iter((AppleHealthRecord(food_correlation=correlation),))

    monkeypatch.setattr(import_service, "iter_apple_health_xml", records)

    summary = _persist(db, user, payload=b"", atomic=True)
    field = db.scalar(select(NutritionFieldObservation))

    assert summary.status == "completed"
    assert field is not None
    assert field.provider_raw_value_decimal is None
    assert field.canonical_value is None
    assert db.scalar(select(func.count()).select_from(NutritionConsumptionEvent)) == 1
