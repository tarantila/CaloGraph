from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import ClassVar
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.google_health.client import (
    GoogleHealthClient,
    NutritionDataSource,
    NutritionDataSourceApplication,
    NutritionDataSourceDevice,
    NutritionLog,
    NutritionLogDataPoint,
    NutritionLogInterval,
    NutritionNutrient,
    NutritionQuantity,
    NutritionServing,
)
from app.models import GoogleHealthConnection, HealthSample, User
from app.nutrition.enums import CoverageState, LineageState, ObservationRole, PresenceState
from app.nutrition.models import (
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
from app.services.google_health_nutrition_ingestion import ingest_google_health_nutrition_logs

PROVIDER = "google_health"
DAY = date(2026, 9, 1)
PHYSICAL_START = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
PHYSICAL_END = datetime(2026, 9, 1, 10, 30, tzinfo=UTC)
CIVIL_START = datetime(2026, 9, 1, 12, 0)
CIVIL_END = datetime(2026, 9, 1, 12, 30)


class _JsonResponse:
    status_code = 200
    headers: ClassVar[dict[str, str]] = {"content-type": "application/json"}

    def __init__(self, payload: str) -> None:
        self.payload = payload

    def json(self) -> object:
        return json.loads(self.payload, parse_float=Decimal)


class _JsonTransport:
    def __init__(self, payload: str) -> None:
        self.response = _JsonResponse(payload)

    def get_nutrition_log(self, **kwargs: object) -> _JsonResponse:
        del kwargs
        return self.response


class _Credentials:
    valid = True
    expired = False
    token = "token"


PROVIDER = "google_health"
DAY = date(2026, 9, 1)
PHYSICAL_START = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
PHYSICAL_END = datetime(2026, 9, 1, 10, 30, tzinfo=UTC)
CIVIL_START = datetime(2026, 9, 1, 12, 0)
CIVIL_END = datetime(2026, 9, 1, 12, 30)


_DATA_SOURCE_DEVICE = NutritionDataSourceDevice(
    form_factor="phone",
    manufacturer="Example Manufacturer",
    display_name="Example Device",
)
_DATA_SOURCE_APPLICATION = NutritionDataSourceApplication(
    package_name="com.example.app",
    web_client_id="web-client-id",
    google_web_client_id="google-web-client-id",
)


def _connection(db, user) -> GoogleHealthConnection:
    connection = GoogleHealthConnection(
        user_id=user.id,
        encrypted_refresh_token=b"encrypted-refresh-token",
        granted_scopes=["https://www.googleapis.com/auth/googlehealth.nutrition.readonly"],
        state="active",
    )
    db.add(connection)
    db.flush()
    return connection


def _interval(*, civil: bool = True) -> NutritionLogInterval:
    return NutritionLogInterval(
        start_time=PHYSICAL_START,
        end_time=PHYSICAL_END,
        start_utc_offset="+02:00",
        end_utc_offset="+02:00",
        civil_start_time=CIVIL_START if civil else None,
        civil_end_time=CIVIL_END if civil else None,
    )


def _nutrients() -> tuple[NutritionNutrient, ...]:
    return (
        NutritionNutrient("PROTEIN", NutritionQuantity(30.0, "g")),
        NutritionNutrient("totalCarbohydrate", NutritionQuantity(41.0, "g")),
        NutritionNutrient("totalFat", NutritionQuantity(13.0, "g")),
        NutritionNutrient("DIETARY_FIBER", NutritionQuantity(6.0, "g")),
        NutritionNutrient("SUGAR", NutritionQuantity(8.0, "g")),
        NutritionNutrient("SATURATED_FAT", NutritionQuantity(3.0, "g")),
    )


def _point(
    *,
    name: str | None = "google-log-1",
    food: str | None = "users/me/dataTypes/food/dataPoints/food-1",
    civil: bool = True,
    nutrients: tuple[NutritionNutrient, ...] | None = None,
    serving: NutritionServing | None = None,
    energy: NutritionQuantity | None = NutritionQuantity(500.0, "kcal"),
    energy_from_fat: NutritionQuantity | None = None,
    total_carbohydrate: NutritionQuantity | None = NutritionQuantity(40.0, "g"),
    total_fat: NutritionQuantity | None = NutritionQuantity(12.0, "g"),
    food_display_name: str | None = "Bowl",
) -> NutritionLogDataPoint:
    return NutritionLogDataPoint(
        name=name,
        nutrition_log=NutritionLog(
            interval=_interval(civil=civil),
            nutrients=_nutrients() if nutrients is None else nutrients,
            energy=energy,
            energy_from_fat=energy_from_fat,
            total_carbohydrate=total_carbohydrate,
            total_fat=total_fat,
            serving=serving,
            food=food,
            food_display_name=food_display_name,
        ),
        data_source=NutritionDataSource(
            recording_method="manual",
            platform="android",
            device=_DATA_SOURCE_DEVICE,
            application=_DATA_SOURCE_APPLICATION,
        ),
    )


def _anonymous_direct_point() -> NutritionLogDataPoint:
    return _point(
        name=None,
        food=None,
        serving=None,
        energy=NutritionQuantity(90.0, "kcal"),
        food_display_name=None,
        nutrients=(NutritionNutrient("PROTEIN", NutritionQuantity(4.0, "g")),),
    )


def _ingest(
    db,
    user,
    points,
    *,
    connection=None,
    source_instance_id=None,
    coverage_state=CoverageState.COMPLETE.value,
):
    if connection is None and source_instance_id is None:
        connection = _connection(db, user)
    return ingest_google_health_nutrition_logs(
        db,
        user_id=user.id,
        source_instance_id=connection.id if connection is not None else source_instance_id,
        requested_start=DAY,
        requested_end=DAY,
        data_points=tuple(points),
        coverage_state=coverage_state,
    )


def _rows(db, model):
    return db.scalars(select(model).order_by(model.id)).all()


DOMAIN_MODELS = (
    NutritionIngestionRun,
    NutritionSourceObservation,
    NutritionConsumptionEvent,
    NutritionFieldObservation,
    NutritionServingObservation,
    NutritionProvenance,
    NutritionExternalIdentity,
    NutritionExternalIdentityLink,
    NutritionFoodProfile,
    NutritionFoodSnapshot,
    NutritionSourceTombstone,
    HealthSample,
)


def _domain_counts(db):
    return {model: db.scalar(select(func.count()).select_from(model)) for model in DOMAIN_MODELS}


def _stable_domain_counts(db):
    return {
        model: db.scalar(select(func.count()).select_from(model))
        for model in DOMAIN_MODELS
        if model is not NutritionIngestionRun
    }


_RETRY_SIGNATURE_FIELDS = {
    NutritionSourceObservation: (
        "id",
        "ingestion_run_id",
        "source_instance_id",
        "source_namespace",
        "source_record_id",
        "source_revision",
        "observation_fingerprint",
    ),
    NutritionConsumptionEvent: (
        "id",
        "source_observation_id",
        "source_instance_id",
        "event_kind",
        "logical_event_key",
        "revision",
        "supersedes_event_id",
        "supersedes_revision",
    ),
    NutritionFieldObservation: (
        "id",
        "source_observation_id",
        "provider_field_path",
        "observation_role",
        "metric_key",
        "canonical_value",
        "canonical_unit",
    ),
    NutritionServingObservation: (
        "id",
        "consumption_event_id",
        "food_snapshot_id",
        "serving_scope",
        "profile_serving_id",
        "profile_serving_scope",
    ),
    NutritionProvenance: (
        "id",
        "source_observation_id",
        "consumption_event_id",
        "food_snapshot_id",
        "serving_observation_id",
        "field_observation_id",
        "role",
        "lineage_state",
    ),
    NutritionExternalIdentity: (
        "id",
        "source_instance_id",
        "provider_key",
        "namespace",
        "identity_value",
        "identity_kind",
    ),
    NutritionExternalIdentityLink: (
        "id",
        "external_identity_id",
        "link_role",
        "consumption_event_id",
        "food_profile_id",
        "source_observation_id",
        "link_revision",
        "supersedes_link_id",
        "supersedes_link_revision",
    ),
    NutritionFoodProfile: (
        "id",
        "source_instance_id",
        "current_snapshot_id",
        "profile_status",
    ),
    NutritionFoodSnapshot: (
        "id",
        "food_profile_id",
        "source_observation_id",
        "provider_revision",
        "content_hash",
    ),
    NutritionSourceTombstone: (
        "id",
        "source_instance_id",
        "source_namespace",
        "source_record_id",
        "external_identity_id",
        "source_observation_id",
        "tombstone_kind",
    ),
    HealthSample: (
        "id",
        "external_sample_id",
        "fingerprint",
        "source_type",
        "source_identifier",
        "metric_type",
        "value",
        "unit",
    ),
}


def _domain_signatures(db):
    return {
        model: {tuple(getattr(row, field) for field in fields) for row in _rows(db, model)}
        for model, fields in _RETRY_SIGNATURE_FIELDS.items()
    }


def _metadata_scalars(value):
    if isinstance(value, dict):
        for key, nested in value.items():
            yield from _metadata_scalars(key)
            yield from _metadata_scalars(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _metadata_scalars(nested)
    else:
        yield value


def test_event_kind_semantics_and_stable_unnamed_identity(db, user):
    connection = _connection(db, user)
    _ingest(
        db,
        user,
        (
            _point(name="identified-log", food="users/me/dataTypes/food/dataPoints/food-1"),
            _anonymous_direct_point(),
            _point(
                name="unknown-log",
                food=None,
                nutrients=(),
                energy=None,
                total_carbohydrate=None,
                total_fat=None,
                food_display_name=None,
            ),
        ),
        connection=connection,
    )
    events = _rows(db, NutritionConsumptionEvent)
    assert {event.event_kind for event in events} == {"product", "simple_product", "unknown"}
    product = next(event for event in events if event.event_kind == "product")
    anonymous = next(event for event in events if event.event_kind == "simple_product")
    assert anonymous.logical_event_key is not None
    assert anonymous.provider_metadata.get("external_name") is None

    identity = next(
        identity
        for identity in _rows(db, NutritionExternalIdentity)
        if (
            identity.provider_key == PROVIDER
            and identity.source_instance_id == connection.id
            and identity.identity_value == "users/me/dataTypes/food/dataPoints/food-1"
            and identity.identity_kind in {"product", "food"}
        )
    )
    assert identity.namespace
    assert any(
        link.external_identity_id == identity.id and link.consumption_event_id == product.id
        for link in _rows(db, NutritionExternalIdentityLink)
    )
    runs_before = db.scalar(select(func.count()).select_from(NutritionIngestionRun))
    before_retry_counts = _stable_domain_counts(db)
    before_retry_signatures = _domain_signatures(db)
    _ingest(db, user, (_anonymous_direct_point(),), connection=connection)
    assert db.scalar(select(func.count()).select_from(NutritionIngestionRun)) == runs_before + 1
    assert _stable_domain_counts(db) == before_retry_counts
    assert _domain_signatures(db) == before_retry_signatures


def test_canonical_decimal_metrics_are_not_double_counted(db, user):
    _ingest(db, user, (_point(),))
    fields = _rows(db, NutritionFieldObservation)
    canonical = {
        field.metric_key: field
        for field in fields
        if field.observation_role == ObservationRole.CANONICAL.value
    }
    assert set(canonical) == {
        "dietary_energy_kcal",
        "protein_g",
        "carbohydrates_g",
        "fat_g",
        "fiber_g",
        "sugar_g",
        "saturated_fat_g",
    }
    assert {key: field.canonical_value for key, field in canonical.items()} == {
        "dietary_energy_kcal": Decimal("500"),
        "protein_g": Decimal("30"),
        "carbohydrates_g": Decimal("40"),
        "fat_g": Decimal("12"),
        "fiber_g": Decimal("6"),
        "sugar_g": Decimal("8"),
        "saturated_fat_g": Decimal("3"),
    }
    assert all(isinstance(field.canonical_value, Decimal) for field in canonical.values())
    assert (
        sum(
            field.metric_key == "carbohydrates_g"
            and field.observation_role == ObservationRole.CANONICAL.value
            for field in fields
        )
        == 1
    )
    assert (
        sum(
            field.metric_key == "fat_g"
            and field.observation_role == ObservationRole.CANONICAL.value
            for field in fields
        )
        == 1
    )


def test_duplicate_known_nutrients_keep_one_canonical_observation(db, user):
    _ingest(
        db,
        user,
        (
            _point(
                energy=None,
                total_carbohydrate=None,
                total_fat=None,
                nutrients=(
                    NutritionNutrient("PROTEIN", NutritionQuantity(30.0, "g")),
                    NutritionNutrient("PROTEIN", NutritionQuantity(31.0, "g")),
                ),
            ),
        ),
    )
    fields = _rows(db, NutritionFieldObservation)
    protein_fields = [
        field for field in fields if field.provider_field_path.startswith("nutritionLog.nutrients")
    ]
    canonical_fields = [field for field in protein_fields if field.metric_key == "protein_g"]
    provider_fields = [field for field in protein_fields if field.metric_key is None]
    assert (
        sum(field.observation_role == ObservationRole.CANONICAL.value for field in canonical_fields)
        == 1
    )
    assert (
        sum(field.observation_role == ObservationRole.PROVIDER.value for field in provider_fields)
        == 1
    )
    canonical = next(
        field
        for field in canonical_fields
        if field.observation_role == ObservationRole.CANONICAL.value
    )
    assert canonical.canonical_value == Decimal("30")


def test_high_precision_json_quantity_survives_google_dto_ingestion(db, user):
    raw_value = "8996.632807816541"
    expected = Decimal(raw_value)
    payload = (
        '{"dataPoints":[{"name":"users/me/dataTypes/nutrition-log/dataPoints/precision-1",'
        '"nutritionLog":{"interval":{"startTime":"2026-09-01T10:00:00Z",'
        '"endTime":"2026-09-01T10:30:00Z","startUtcOffset":"0s","endUtcOffset":"0s"},'
        '"energy":{"kcal":' + raw_value + "}}}]}"
    )
    google_client = GoogleHealthClient(_JsonTransport(payload), _Credentials())

    page = google_client.get_nutrition_log_page(page_size=1)
    assert len(page.data_points) == 1
    point = page.data_points[0]
    energy = point.nutrition_log.energy
    assert energy is not None

    _ingest(db, user, page.data_points)
    field = db.scalar(
        select(NutritionFieldObservation).where(
            NutritionFieldObservation.metric_key == "dietary_energy_kcal",
            NutritionFieldObservation.observation_role == ObservationRole.CANONICAL.value,
        )
    )
    assert field is not None
    assert energy.value == expected
    assert field.provider_raw_value_decimal == expected
    assert field.canonical_value == expected


def test_energy_user_provided_unit_does_not_convert_canonical_scalar(db, user):
    _ingest(db, user, (_point(energy=NutritionQuantity(10.0, "KILOJOULE")),))
    energy = db.scalar(
        select(NutritionFieldObservation).where(
            NutritionFieldObservation.metric_key == "dietary_energy_kcal",
            NutritionFieldObservation.observation_role == ObservationRole.CANONICAL.value,
        )
    )
    assert energy is not None
    assert energy.canonical_value == Decimal("10")
    assert energy.canonical_unit == "kcal"
    assert energy.provider_raw_unit == "KILOJOULE"


def test_civil_date_controls_local_date_and_missing_civil_is_partial(db, user):
    _ingest(db, user, (_point(civil=True),))
    event = db.scalar(select(NutritionConsumptionEvent))
    source = db.scalar(select(NutritionSourceObservation))
    assert event.local_date == DAY
    assert source.local_date == DAY
    assert event.provider_civil_datetime == CIVIL_START

    db.rollback()
    db.begin()
    _ingest(db, user, (_point(name="missing-civil", civil=False),))
    missing = db.scalar(
        select(NutritionConsumptionEvent).where(
            NutritionConsumptionEvent.logical_event_key == "missing-civil"
        )
    )
    missing_source = db.scalar(
        select(NutritionSourceObservation).where(
            NutritionSourceObservation.source_record_id == "missing-civil"
        )
    )
    assert missing.local_date is None
    assert missing_source.local_date is None
    assert missing.coverage_state == CoverageState.PARTIAL.value
    missing_fields = [
        field
        for field in _rows(db, NutritionFieldObservation)
        if field.source_observation_id == missing_source.id
    ]
    assert missing_fields
    assert all(field.coverage_state == CoverageState.PARTIAL.value for field in missing_fields)


def test_overlong_direct_dto_name_is_rejected_before_domain_writes(db, user):
    before_counts = _domain_counts(db)
    with pytest.raises(ValueError):
        _ingest(db, user, (_point(name="n" * 256),))
    assert _domain_counts(db) == before_counts


def test_short_semantic_serving_unit_is_persisted_in_valid_unit_columns(db, user):
    serving = NutritionServing(
        food_measurement_unit="g",
        food_measurement_unit_display_name="grams",
        amount=2.0,
    )
    _ingest(db, user, (_point(serving=serving),))
    event = db.scalar(select(NutritionConsumptionEvent))
    observations = _rows(db, NutritionServingObservation)
    fields = _rows(db, NutritionFieldObservation)

    assert len(observations) == 1
    assert observations[0].quantity == Decimal("2")
    assert observations[0].label == "grams"
    assert observations[0].unit == "g"
    assert event.amount == Decimal("2")
    assert event.amount_unit == "g"
    unit_evidence = [
        field for field in fields if "foodMeasurementUnit" in field.provider_field_path
    ]
    assert len(unit_evidence) == 1
    assert unit_evidence[0].provider_raw_value_text == "g"


def test_energy_from_fat_is_provider_evidence_without_canonical_metric(db, user):
    _ingest(
        db,
        user,
        (_point(energy_from_fat=NutritionQuantity(200.0, "kcal")),),
    )
    fields = _rows(db, NutritionFieldObservation)
    energy_from_fat = next(
        field for field in fields if "energyFromFat" in field.provider_field_path
    )
    assert energy_from_fat.observation_role == ObservationRole.PROVIDER.value
    assert energy_from_fat.presence_state == PresenceState.SUPPLIED.value
    assert energy_from_fat.metric_key is None
    assert energy_from_fat.canonical_value is None
    assert energy_from_fat.provider_raw_value_decimal == Decimal("200")

    energy_metrics = [field for field in fields if field.metric_key == "dietary_energy_kcal"]
    assert len(energy_metrics) == 1
    assert energy_metrics[0].provider_field_path.endswith("energy")
    assert energy_metrics[0].observation_role == ObservationRole.CANONICAL.value


def test_serving_resource_reference_is_kept_as_evidence_not_unit(db, user):
    serving = NutritionServing(
        food_measurement_unit="users/me/dataTypes/foodMeasurementUnit/dataPoints/unit-1",
        food_measurement_unit_display_name="1 bowl",
        amount=2.0,
    )
    _ingest(db, user, (_point(serving=serving),))
    event = db.scalar(select(NutritionConsumptionEvent))
    observations = _rows(db, NutritionServingObservation)
    fields = _rows(db, NutritionFieldObservation)
    assert len(observations) == 1
    assert observations[0].quantity == Decimal("2")
    assert observations[0].label == "1 bowl"
    assert observations[0].unit is None
    unit_evidence = [
        field for field in fields if "foodMeasurementUnit" in field.provider_field_path
    ]
    assert len(unit_evidence) == 1
    assert (
        unit_evidence[0].provider_raw_value_text
        == "users/me/dataTypes/foodMeasurementUnit/dataPoints/unit-1"
    )
    assert event.amount_unit is None


def test_unknown_supplied_nutrient_is_presence_evidence_without_canonical_value(db, user):
    _ingest(
        db,
        user,
        (
            _point(
                nutrients=(NutritionNutrient("MYSTERY_NUTRIENT", NutritionQuantity(7.0, "g")),),
                energy=None,
            ),
        ),
    )
    field = db.scalar(
        select(NutritionFieldObservation).where(
            NutritionFieldObservation.provider_field_path.like("%MYSTERY%")
        )
    )
    assert field is not None
    assert field.presence_state == PresenceState.SUPPLIED.value
    assert field.metric_key is None
    assert field.canonical_value is None
    assert field.provider_raw_value_decimal == Decimal("7")
    assert len(field.provider_field_path) <= 255
    assert field.provider_raw_value_text is None or len(field.provider_raw_value_text) <= 4096


def test_metadata_is_allowlisted_and_raw_payload_is_not_persisted(db, user):
    _ingest(db, user, (_point(),))
    sensitive_terms = (
        "raw",
        "payload",
        "token",
        "secret",
        "credential",
        "auth",
        "password",
        "refresh",
        "response",
        "key",
    )
    persisted_metadata_scalars = []
    for row in (
        *_rows(db, NutritionIngestionRun),
        *_rows(db, NutritionSourceObservation),
        *_rows(db, NutritionConsumptionEvent),
        *_rows(db, NutritionFieldObservation),
        *_rows(db, NutritionServingObservation),
        *_rows(db, NutritionProvenance),
        *_rows(db, NutritionExternalIdentity),
        *_rows(db, NutritionExternalIdentityLink),
        *_rows(db, NutritionFoodProfile),
        *_rows(db, NutritionFoodSnapshot),
        *_rows(db, NutritionSourceTombstone),
        *_rows(db, HealthSample),
    ):
        metadata = getattr(row, "provider_metadata", None) or {}
        for scalar in _metadata_scalars(metadata):
            scalar_text = str(scalar).lower()
            persisted_metadata_scalars.append(scalar_text)
            assert not any(term in scalar_text for term in sensitive_terms)
    assert {
        "manual",
        "android",
        "phone",
        "example manufacturer",
        "example device",
        "com.example.app",
        "web-client-id",
        "google-web-client-id",
    } <= set(persisted_metadata_scalars)
    assert all(
        source.payload_hash is None or len(source.payload_hash) == 64
        for source in _rows(db, NutritionSourceObservation)
    )


def test_named_revision_and_retry_are_idempotent(db, user):
    connection = _connection(db, user)
    first = _point(name="revision-log")
    _ingest(db, user, (first,), connection=connection)
    _ingest(db, user, (first,), connection=connection)
    assert db.scalar(select(func.count()).select_from(NutritionConsumptionEvent)) == 1

    changed = _point(name="revision-log", energy=NutritionQuantity(600.0, "kcal"))
    _ingest(db, user, (changed,), connection=connection)
    events = db.scalars(
        select(NutritionConsumptionEvent).order_by(NutritionConsumptionEvent.revision)
    ).all()
    assert [event.revision for event in events] == [1, 2]
    assert events[1].supersedes_event_id == events[0].id
    assert events[1].supersedes_revision == 1


def test_every_domain_target_has_exactly_one_provenance_with_lineage(db, user):
    connection = _connection(db, user)
    _ingest(
        db,
        user,
        (
            _point(
                serving=NutritionServing(amount=1.0, food_measurement_unit_display_name="portion")
            ),
        ),
        connection=connection,
    )
    sources = _rows(db, NutritionSourceObservation)
    provenance = _rows(db, NutritionProvenance)
    assert len(sources) == 1
    assert sources[0].provider_key == PROVIDER
    assert sources[0].source_instance_id == connection.id
    target_columns = (
        "consumption_event_id",
        "food_snapshot_id",
        "serving_observation_id",
        "field_observation_id",
    )
    target_counts = [
        sum(getattr(item, target) is not None for target in target_columns) for item in provenance
    ]
    assert provenance
    assert target_counts == [1] * len(provenance)
    persisted_target_ids = [
        row.id
        for model in (
            NutritionConsumptionEvent,
            NutritionFieldObservation,
            NutritionServingObservation,
            NutritionFoodSnapshot,
        )
        for row in _rows(db, model)
    ]
    provenance_target_ids = [
        getattr(item, target)
        for item in provenance
        for target in target_columns
        if getattr(item, target) is not None
    ]
    assert len(provenance_target_ids) == len(persisted_target_ids)
    assert set(provenance_target_ids) == set(persisted_target_ids)
    assert all(provenance_target_ids.count(target_id) == 1 for target_id in persisted_target_ids)
    assert all(item.source_observation_id == sources[0].id for item in provenance)
    assert all(item.lineage_state in {state.value for state in LineageState} for item in provenance)


def test_food_reference_alone_does_not_create_empty_profile_or_tombstone(db, user):
    food_resource = "users/me/dataTypes/food/dataPoints/food-alone"
    connection = _connection(db, user)
    _ingest(
        db,
        user,
        (
            _point(
                name="food-only",
                food=food_resource,
                nutrients=(),
                energy=None,
                total_carbohydrate=None,
                total_fat=None,
                food_display_name=None,
            ),
        ),
        connection=connection,
    )
    events = _rows(db, NutritionConsumptionEvent)
    assert len(events) == 1
    event = events[0]
    assert event.event_kind == "product"
    identity = next(
        identity
        for identity in _rows(db, NutritionExternalIdentity)
        if (
            identity.provider_key == PROVIDER
            and identity.source_instance_id == connection.id
            and identity.identity_value == food_resource
            and identity.identity_kind in {"product", "food"}
        )
    )
    assert identity.namespace
    assert any(
        link.external_identity_id == identity.id and link.consumption_event_id == event.id
        for link in _rows(db, NutritionExternalIdentityLink)
    )
    assert not _rows(db, NutritionFoodProfile)
    assert not _rows(db, NutritionFoodSnapshot)
    assert not _rows(db, NutritionSourceTombstone)
    assert not _rows(db, HealthSample)


def test_source_and_user_scope_are_enforced(db, user):
    connection = _connection(db, user)
    other = User(username="google-other", password_hash="hash", timezone="UTC")
    db.add(other)
    db.flush()
    before_counts = _domain_counts(db)
    with pytest.raises(ValueError, match=r"source_instance_id|user"):
        _ingest(db, other, (_point(),), connection=connection)
    assert _domain_counts(db) == before_counts

    before_missing_source_counts = _domain_counts(db)
    with pytest.raises(ValueError, match=r"source_instance_id|user"):
        _ingest(db, user, (_point(),), source_instance_id=uuid4())
    assert _domain_counts(db) == before_missing_source_counts


def test_caller_transaction_rollback_removes_all_domain_rows(db, user):
    _ingest(db, user, (_point(),))
    db.rollback()
    for model in (
        NutritionIngestionRun,
        NutritionSourceObservation,
        NutritionConsumptionEvent,
        NutritionFieldObservation,
        NutritionServingObservation,
        NutritionProvenance,
        NutritionExternalIdentity,
        NutritionExternalIdentityLink,
        NutritionFoodProfile,
        NutritionFoodSnapshot,
        NutritionSourceTombstone,
        HealthSample,
    ):
        assert db.scalar(select(func.count()).select_from(model)) == 0
