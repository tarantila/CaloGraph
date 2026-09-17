from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import GoogleHealthConnection, User, YazioConnection
from app.nutrition.enums import (
    CoverageState,
    LineageState,
    ObservationKind,
    ObservationRole,
    PresenceState,
    ResolutionState,
)
from app.nutrition.models import (
    NutritionConsumptionEvent,
    NutritionFieldObservation,
    NutritionIngestionRun,
    NutritionSourceObservation,
    NutritionSourceTombstone,
)
from app.nutrition.resolution.discovery import (
    NutritionProviderDiscovery,
    NutritionProviderIdentity,
    NutritionProviderMetadataSet,
    discover_nutrition_provider_metadata,
    discover_nutrition_providers,
)
from app.nutrition.resolution.metrics import canonical_unit

DAY = date(2026, 9, 1)
NEXT_DAY = date(2026, 9, 2)
OBSERVED_AT = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
LATEST_OBSERVED_AT = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def _yazio_connection(db: Session, user: User) -> YazioConnection:
    connection = YazioConnection(
        user_id=user.id,
        encrypted_email=b"encrypted-email",
        encrypted_password=b"encrypted-password",
        source_identifier=f"source-{user.id}",
    )
    db.add(connection)
    db.flush()
    return connection


def _google_connection(db: Session, user: User) -> GoogleHealthConnection:
    connection = GoogleHealthConnection(
        user_id=user.id,
        encrypted_refresh_token=b"encrypted-refresh-token",
        granted_scopes=["nutrition.readonly"],
    )
    db.add(connection)
    db.flush()
    return connection


def _run(
    db: Session,
    user: User,
    *,
    provider_key: str,
    source_instance_id,
    status: str = "completed",
    coverage_state: str = CoverageState.COMPLETE.value,
) -> NutritionIngestionRun:
    run = NutritionIngestionRun(
        user_id=user.id,
        provider_key=provider_key,
        source_instance_id=source_instance_id,
        connector_variant="test",
        requested_start_date=DAY,
        requested_end_date=NEXT_DAY,
        covered_start_date=DAY,
        covered_end_date=NEXT_DAY,
        status=status,
        coverage_state=coverage_state,
    )
    db.add(run)
    db.flush()
    return run


def _source(
    db: Session,
    user: User,
    run: NutritionIngestionRun,
    *,
    source_instance_id,
    provider_key: str,
    namespace: str,
    record_id: str | None,
    observation_kind: str,
    local_date: date | None,
    revision: int = 1,
    presence_state: str = PresenceState.SUPPLIED.value,
    coverage_state: str = CoverageState.COMPLETE.value,
    resolution_state: str = ResolutionState.RESOLVED.value,
    lineage_state: str = LineageState.CONFIRMED.value,
    observed_at: datetime = OBSERVED_AT,
) -> NutritionSourceObservation:
    fingerprint = sha256(f"{provider_key}:{namespace}:{record_id}:{revision}".encode()).hexdigest()
    source = NutritionSourceObservation(
        user_id=user.id,
        ingestion_run_id=run.id,
        provider_key=provider_key,
        source_instance_id=source_instance_id,
        connector_variant="test",
        observation_kind=observation_kind,
        source_namespace=namespace,
        source_record_id=record_id,
        source_revision=revision,
        observation_fingerprint=fingerprint,
        local_date=local_date,
        presence_state=presence_state,
        coverage_state=coverage_state,
        resolution_state=resolution_state,
        lineage_state=lineage_state,
        observed_at=observed_at,
    )
    db.add(source)
    db.flush()
    return source


def _field(
    db: Session,
    source: NutritionSourceObservation,
    *,
    metric_key: str = "iron_mg",
    value: Decimal = Decimal("7"),
    role: str = ObservationRole.PROVIDER.value,
) -> NutritionFieldObservation:
    field = NutritionFieldObservation(
        user_id=source.user_id,
        source_observation_id=source.id,
        provider_field_path=f"nutrition.{metric_key}",
        provider_raw_value_decimal=value,
        provider_raw_unit=canonical_unit(metric_key),
        metric_key=metric_key,
        canonical_value=value,
        canonical_unit=canonical_unit(metric_key),
        observation_role=role,
        presence_state=(
            PresenceState.EXPLICIT_ZERO.value if value == Decimal("0") else PresenceState.SUPPLIED.value
        ),
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
        lineage_state=LineageState.CONFIRMED.value,
    )
    db.add(field)
    db.flush()
    return field


def test_global_discovery_includes_valid_provider_only_evidence_but_range_does_not(
    db: Session, user: User
) -> None:
    connection = _yazio_connection(db, user)
    run = _run(db, user, provider_key="yazio", source_instance_id=connection.id)
    _source(
        db,
        user,
        run,
        source_instance_id=connection.id,
        provider_key="yazio",
        namespace="yazio.product",
        record_id="profile-1",
        observation_kind=ObservationKind.PRODUCT_PROFILE.value,
        local_date=None,
    )

    assert discover_nutrition_providers(db, user_id=user.id) == NutritionProviderDiscovery(
        providers=(NutritionProviderIdentity(provider_key="yazio"),),
    )
    assert discover_nutrition_provider_metadata(
        db, user_id=user.id, start=DAY, end=NEXT_DAY
    ) == NutritionProviderMetadataSet(providers=())


def test_range_metadata_requires_local_date_and_valid_micronutrient_field(
    db: Session, user: User
) -> None:
    connection = _yazio_connection(db, user)
    run = _run(db, user, provider_key="yazio", source_instance_id=connection.id)
    source = _source(
        db,
        user,
        run,
        source_instance_id=connection.id,
        provider_key="yazio",
        namespace="yazio.daily_summary",
        record_id="summary-1",
        observation_kind=ObservationKind.DAILY_SUMMARY.value,
        local_date=DAY,
    )
    _field(db, source)
    latest_source = _source(
        db,
        user,
        run,
        source_instance_id=connection.id,
        provider_key="yazio",
        namespace="yazio.daily_summary",
        record_id="summary-2",
        observation_kind=ObservationKind.DAILY_SUMMARY.value,
        local_date=DAY,
        observed_at=LATEST_OBSERVED_AT,
    )
    _field(db, latest_source)

    result = discover_nutrition_provider_metadata(db, user_id=user.id, start=DAY, end=DAY)

    assert result.providers[0].provider_key == "yazio"
    assert result.providers[0].latest_evidence_observed_at == LATEST_OBSERVED_AT



def test_discovery_excludes_superseded_invalid_and_tombstoned_evidence(
    db: Session, user: User
) -> None:
    connection = _yazio_connection(db, user)
    run = _run(db, user, provider_key="yazio", source_instance_id=connection.id)
    _source(
        db,
        user,
        run,
        source_instance_id=connection.id,
        provider_key="yazio",
        namespace="yazio.product",
        record_id="invalid-current",
        observation_kind=ObservationKind.PRODUCT_PROFILE.value,
        local_date=None,
        resolution_state=ResolutionState.UNRESOLVED.value,
    )
    tombstoned = _source(
        db,
        user,
        run,
        source_instance_id=connection.id,
        provider_key="yazio",
        namespace="yazio.product",
        record_id="deleted",
        observation_kind=ObservationKind.PRODUCT_PROFILE.value,
        local_date=None,
    )
    db.add(
        NutritionSourceTombstone(
            user_id=user.id,
            provider_key="yazio",
            source_instance_id=connection.id,
            source_namespace=tombstoned.source_namespace,
            source_record_id=tombstoned.source_record_id,
            source_observation_id=tombstoned.id,
            tombstone_kind="deleted",
        )
    )
    _source(
        db,
        user,
        run,
        source_instance_id=connection.id,
        provider_key="yazio",
        namespace="yazio.product",
        record_id="revised",
        observation_kind=ObservationKind.PRODUCT_PROFILE.value,
        local_date=None,
        revision=1,
    )
    _source(
        db,
        user,
        run,
        source_instance_id=connection.id,
        provider_key="yazio",
        namespace="yazio.product",
        record_id="revised",
        observation_kind=ObservationKind.PRODUCT_PROFILE.value,
        local_date=None,
        revision=2,
        resolution_state=ResolutionState.UNRESOLVED.value,
    )
    db.flush()

    assert discover_nutrition_providers(db, user_id=user.id).providers == ()


def test_discovery_excludes_events_with_broken_revision_chains(db: Session, user: User) -> None:
    connection = _yazio_connection(db, user)
    run = _run(db, user, provider_key="yazio", source_instance_id=connection.id)
    source = _source(
        db,
        user,
        run,
        source_instance_id=connection.id,
        provider_key="yazio",
        namespace="yazio.simple_product",
        record_id="event-2",
        observation_kind=ObservationKind.CONSUMPTION_EVENT.value,
        local_date=DAY,
    )
    event = NutritionConsumptionEvent(
        user_id=user.id,
        source_observation_id=source.id,
        provider_key="yazio",
        source_instance_id=connection.id,
        event_kind="simple_product",
        logical_event_key="event-2",
        revision=2,
        local_date=DAY,
        amount=Decimal("1"),
        amount_unit="serving",
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
        lineage_state=LineageState.CONFIRMED.value,
    )
    db.add(event)
    db.flush()
    _field(db, source, role=ObservationRole.PROVIDER.value)

    assert discover_nutrition_providers(db, user_id=user.id).providers == ()
    assert discover_nutrition_provider_metadata(
        db, user_id=user.id, start=DAY, end=DAY
    ).providers == ()


def test_discovery_requires_owned_source_instances_and_collapses_provider_instances(
    db: Session, user: User
) -> None:
    connection = _yazio_connection(db, user)
    run = _run(db, user, provider_key="yazio", source_instance_id=connection.id)
    _source(
        db,
        user,
        run,
        source_instance_id=connection.id,
        provider_key="yazio",
        namespace="yazio.product",
        record_id="owned",
        observation_kind=ObservationKind.PRODUCT_PROFILE.value,
        local_date=None,
    )
    _source(
        db,
        user,
        run,
        source_instance_id=uuid4(),
        provider_key="yazio",
        namespace="yazio.product",
        record_id="forged",
        observation_kind=ObservationKind.PRODUCT_PROFILE.value,
        local_date=None,
    )

    result = discover_nutrition_providers(db, user_id=user.id)

    assert result.providers == (NutritionProviderIdentity("yazio"),)
    assert not hasattr(result.providers[0], "source_instance_ids")


def test_nutrient_registry_is_discovery_boundary_without_source_priority_crossover(
    db: Session, user: User
) -> None:
    connection = _google_connection(db, user)
    run = _run(db, user, provider_key="google_health", source_instance_id=connection.id)
    source = _source(
        db,
        user,
        run,
        source_instance_id=connection.id,
        provider_key="google_health",
        namespace="google_health.nutrition_log",
        record_id="google-1",
        observation_kind=ObservationKind.CONSUMPTION_EVENT.value,
        local_date=DAY,
    )
    event = NutritionConsumptionEvent(
        user_id=user.id,
        source_observation_id=source.id,
        provider_key="google_health",
        source_instance_id=connection.id,
        event_kind="simple_product",
        logical_event_key="google-1",
        revision=1,
        local_date=DAY,
        amount=Decimal("1"),
        amount_unit="serving",
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
        lineage_state=LineageState.CONFIRMED.value,
    )
    db.add(event)
    db.flush()
    _field(db, source, role=ObservationRole.PROVIDER.value)
    assert discover_nutrition_provider_metadata(
        db, user_id=user.id, start=DAY, end=DAY
    ).providers == ()

    _field(db, source, role=ObservationRole.CANONICAL.value)
    result = discover_nutrition_provider_metadata(db, user_id=user.id, start=DAY, end=DAY)

    assert tuple(item.provider_key for item in result.providers) == ("google_health",)
    assert not hasattr(result.providers[0], "priority_rank")
    assert db.scalar(select(NutritionConsumptionEvent.id)) == event.id
