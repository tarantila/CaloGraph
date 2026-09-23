from __future__ import annotations

import hashlib
import os
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from app.auth.security import hash_password
from app.database import SessionLocal
from app.google_health.constants import GOOGLE_HEALTH_REQUIRED_SCOPES
from app.models import (
    GoogleHealthConnection,
    HealthSample,
    ImportBatch,
    User,
    UserOnboarding,
    UserProviderPriority,
    YazioConnection,
)
from app.nutrition.enums import (
    ConsumptionEventKind,
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
    NutritionFoodProfile,
    NutritionFoodSnapshot,
    NutritionIngestionRun,
    NutritionSourceObservation,
)
from app.provider_preferences import (
    ACTIVITY_ENERGY_DATA_AREA,
    NUTRITION_DATA_AREA,
    WEIGHT_DATA_AREA,
)
from app.services.credential_crypto import encrypt_credential

TEST_USERNAME = "verification-e2e-user"
OTHER_USERNAME = "verification-e2e-other-user"
TEST_PASSWORD = "verification-e2e-local-passphrase"
BERLIN = ZoneInfo("Europe/Berlin")


def _fingerprint(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def _verification_date() -> date:
    configured = os.getenv("E2E_VERIFICATION_DATE")
    if configured:
        return date.fromisoformat(configured)
    return datetime.now(BERLIN).date()


def _google_connection(user_id: UUID) -> GoogleHealthConnection:
    return GoogleHealthConnection(
        user_id=user_id,
        client_id="synthetic-google-client",
        encrypted_client_secret=encrypt_credential("synthetic-google-client-secret"),
        encrypted_refresh_token=encrypt_credential("synthetic-google-refresh-token"),
        granted_scopes=sorted(GOOGLE_HEALTH_REQUIRED_SCOPES),
        state="active",
        sync_state="completed",
        last_success_at=_now(),
    )


def _yazio_connection(user_id: UUID) -> YazioConnection:
    return YazioConnection(
        user_id=user_id,
        encrypted_email=encrypt_credential("synthetic-yazio@example.invalid"),
        encrypted_password=encrypt_credential("synthetic-yazio-password"),
        source_identifier="synthetic-yazio-source",
        sync_enabled=False,
        historical_sync_state="completed",
        last_success_at=_now(),
    )


def _priorities(user_id: UUID) -> list[UserProviderPriority]:
    now = _now()
    return [
        UserProviderPriority(
            user_id=user_id,
            data_area=area,
            priority=priority,
            provider_key=provider,
            created_at=now,
            updated_at=now,
        )
        for area in (NUTRITION_DATA_AREA, ACTIVITY_ENERGY_DATA_AREA, WEIGHT_DATA_AREA)
        for priority, provider in enumerate(("google_health", "yazio", "apple_health"), start=1)
    ]


def _sample(
    *,
    user_id: UUID,
    batch_id: UUID,
    source_type: str,
    source_identifier: str,
    metric_type: str,
    value: Decimal,
    day: date,
    ordinal: str,
) -> HealthSample:
    start = datetime.combine(day, time(12, 0), tzinfo=BERLIN)
    return HealthSample(
        user_id=user_id,
        import_batch_id=batch_id,
        external_sample_id=f"synthetic-{ordinal}",
        fingerprint=_fingerprint(f"sample:{user_id}:{ordinal}"),
        source_type=source_type,
        source_name="Synthetic verification fixture",
        source_identifier=source_identifier,
        metric_type=metric_type,
        value=value,
        unit="kcal" if metric_type == "active_energy_kcal" else "kg",
        original_value=value,
        original_unit="kcal" if metric_type == "active_energy_kcal" else "kg",
        start_at=start,
        end_at=start + timedelta(minutes=1),
        local_date=day,
        timezone="Europe/Berlin",
    )


def _field(
    *,
    user_id: UUID,
    source_id: UUID,
    metric: str,
    value: Decimal,
    unit: str,
    ordinal: int,
) -> NutritionFieldObservation:
    return NutritionFieldObservation(
        user_id=user_id,
        source_observation_id=source_id,
        provider_field_path=f"synthetic.{metric}.{ordinal}",
        provider_raw_value_decimal=value,
        provider_raw_unit=unit,
        metric_key=metric,
        canonical_value=value,
        canonical_unit=unit,
        observation_role=ObservationRole.CANONICAL.value,
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
        lineage_state=LineageState.CONFIRMED.value,
    )


def _seed_nutrition(
    db, user_id: UUID, google_id: UUID, day: date, *, other: bool = False
) -> None:
    run_id = uuid4()
    now = _now()
    db.add(
        NutritionIngestionRun(
            id=run_id,
            user_id=user_id,
            provider_key="google_health",
            source_instance_id=google_id,
            connector_variant="synthetic-verification",
            requested_start_date=day,
            requested_end_date=day,
            covered_start_date=day,
            covered_end_date=day,
            status="completed",
            coverage_state=CoverageState.COMPLETE.value,
            provider_metadata={"fixture": True},
            started_at=now,
            finished_at=now,
            created_at=now,
        )
    )
    names = (
        "Synthetic Snapshot Food" if not other else "Other User Food",
        "Synthetic Metadata Food",
        None,
        "Synthetic Dinner Food",
        "Synthetic Unknown Meal Food",
        None,
    )
    datetimes = (time(0, 0), time(8, 15), time(10, 30), time(18, 45), time(20, 5), time(0, 0))
    meal_types = ("BREAKFAST", "LUNCH", "BRUNCH", "DINNER", "UNKNOWN_MEAL", None)
    metadata_names = (None, "Synthetic Metadata Food", None, None, "Synthetic Unknown Meal Food", "Synthetic Snack Food")
    metadata_meals = (None, None, None, None, None, "SNACK")
    amounts = ((Decimal("1"), None), (Decimal("250"), "g"), (None, None), (Decimal("1"), "portion"), (Decimal("90"), "g"), (Decimal("1"), "portion"))
    for index in range(6):
        source_id = uuid4()
        event_id = uuid4()
        civil = datetime.combine(day, datetimes[index])
        canonical = datetime.combine(day, datetimes[index], tzinfo=BERLIN)
        metadata = {"fixture": True}
        if metadata_names[index] is not None:
            metadata["food_display_name"] = metadata_names[index]
        if metadata_meals[index] is not None:
            metadata["meal_type"] = metadata_meals[index]
        observation = NutritionSourceObservation(
            id=source_id,
            user_id=user_id,
            ingestion_run_id=run_id,
            provider_key="google_health",
            source_instance_id=google_id,
            connector_variant="synthetic-verification",
            observation_kind=ObservationKind.CONSUMPTION_EVENT.value,
            source_namespace="synthetic.google.health",
            source_record_id=f"synthetic-event-{index + 1}",
            source_revision=1,
            observation_fingerprint=_fingerprint(f"observation:{user_id}:{index}"),
            provider_civil_datetime=civil,
            provider_timezone="7200s",
            canonical_start_at=canonical,
            canonical_end_at=canonical + timedelta(minutes=1),
            local_date=day,
            timezone_source="provider",
            time_confidence="exact",
            presence_state=PresenceState.SUPPLIED.value,
            coverage_state=CoverageState.COMPLETE.value,
            resolution_state=ResolutionState.RESOLVED.value,
            lineage_state=LineageState.CONFIRMED.value,
            payload_hash=_fingerprint(f"payload:{user_id}:{index}"),
            provider_metadata=metadata,
            observed_at=now,
            created_at=now,
        )
        db.add(observation)
        db.flush()
        snapshot_id: UUID | None = None
        if index in (0, 3):
            profile_id = uuid4()
            snapshot_id = uuid4()
            db.add(
                NutritionFoodProfile(
                    id=profile_id,
                    user_id=user_id,
                    provider_key="google_health",
                    source_instance_id=google_id,
                    profile_status="active",
                    created_at=now,
                    updated_at=now,
                )
            )
            db.add(
                NutritionFoodSnapshot(
                    id=snapshot_id,
                    user_id=user_id,
                    food_profile_id=profile_id,
                    source_observation_id=source_id,
                    provider_revision="synthetic-v1",
                    content_hash=_fingerprint(f"snapshot:{user_id}:{index}"),
                    name=names[index],
                    producer="Synthetic fixture",
                    category="synthetic",
                    base_unit="portion",
                    language="de",
                    is_verified=False,
                    is_private=True,
                    is_deleted=False,
                    provider_updated_at=civil,
                    provider_metadata={"fixture": True},
                    created_at=now,
                )
            )
            db.flush()
        amount, unit = amounts[index]
        db.add(
            NutritionConsumptionEvent(
                id=event_id,
                user_id=user_id,
                source_observation_id=source_id,
                provider_key="google_health",
                source_instance_id=google_id,
                event_kind=ConsumptionEventKind.PRODUCT.value,
                logical_event_key=f"synthetic-event-{index + 1}",
                revision=1,
                food_snapshot_id=snapshot_id,
                provider_civil_datetime=civil,
                provider_timezone="7200s",
                canonical_start_at=canonical,
                canonical_end_at=canonical + timedelta(minutes=1),
                local_date=day,
                daytime=meal_types[index],
                amount=amount,
                amount_unit=unit,
                presence_state=PresenceState.SUPPLIED.value,
                coverage_state=CoverageState.COMPLETE.value,
                resolution_state=ResolutionState.RESOLVED.value,
                lineage_state=LineageState.CONFIRMED.value,
                provider_metadata={"fixture": True},
                created_at=now,
            )
        )
        values = (Decimal("500") + index * Decimal("10"), Decimal("30") + index, Decimal("55") + index, Decimal("18") + index)
        for metric, value, unit_name in zip(
            ("dietary_energy_kcal", "protein_g", "carbohydrates_g", "fat_g"),
            values,
            ("kcal", "g", "g", "g"),
            strict=True,
        ):
            db.add(_field(user_id=user_id, source_id=source_id, metric=metric, value=value, unit=unit_name, ordinal=index))
    db.flush()
    for profile in db.query(NutritionFoodProfile).filter(NutritionFoodProfile.user_id == user_id).all():
        snapshot = db.query(NutritionFoodSnapshot).filter(NutritionFoodSnapshot.food_profile_id == profile.id).first()
        if snapshot is not None:
            profile.current_snapshot_id = snapshot.id


def _seed_user(db, username: str, *, day: date, with_data: bool) -> User:
    existing = db.query(User).filter(User.username == username).first()
    if existing is not None:
        db.delete(existing)
        db.flush()
    user = User(
        username=username,
        password_hash=hash_password(TEST_PASSWORD),
        language="de",
        timezone="Europe/Berlin",
        is_active=True,
        is_admin=False,
        raw_payload_retention_days=0,
    )
    db.add(user)
    db.flush()
    db.add(UserOnboarding(user_id=user.id, current_step="completed", completed_at=_now()))
    google = _google_connection(user.id)
    yazio = _yazio_connection(user.id)
    db.add_all([google, yazio, *_priorities(user.id)])
    db.flush()
    if with_data:
        _seed_nutrition(db, user.id, google.id, day, other=username == OTHER_USERNAME)
        google_batch = ImportBatch(user_id=user.id, source_type="google_health", status="completed", finished_at=_now())
        yazio_batch = ImportBatch(user_id=user.id, source_type="yazio", status="completed", finished_at=_now())
        db.add_all([google_batch, yazio_batch])
        db.flush()
        db.add_all(
            [
                _sample(user_id=user.id, batch_id=google_batch.id, source_type="google_health_activity_v4", source_identifier=str(google.id), metric_type="active_energy_kcal", value=Decimal("420"), day=day, ordinal="google-activity-today"),
                _sample(user_id=user.id, batch_id=google_batch.id, source_type="google_health_activity_v4", source_identifier=str(google.id), metric_type="active_energy_kcal", value=Decimal("380"), day=day - timedelta(days=1), ordinal="google-activity-yesterday"),
                _sample(user_id=user.id, batch_id=google_batch.id, source_type="google_health_weight_v4", source_identifier=str(google.id), metric_type="weight_kg", value=Decimal("72.4"), day=day, ordinal="google-weight-today"),
                _sample(user_id=user.id, batch_id=yazio_batch.id, source_type="yazio_export_v1", source_identifier=str(yazio.id), metric_type="active_energy_kcal", value=Decimal("111"), day=day, ordinal="yazio-activity-one"),
                _sample(user_id=user.id, batch_id=yazio_batch.id, source_type="yazio_export_v1", source_identifier=str(yazio.id), metric_type="active_energy_kcal", value=Decimal("222"), day=day, ordinal="yazio-activity-two"),
            ]
        )
    return user


def main() -> None:
    day = _verification_date()
    with SessionLocal() as db:
        _seed_user(db, TEST_USERNAME, day=day, with_data=True)
        _seed_user(db, OTHER_USERNAME, day=day, with_data=True)
        db.commit()
    print("Synthetic verification fixtures seeded in the isolated database.")


if __name__ == "__main__":
    main()
