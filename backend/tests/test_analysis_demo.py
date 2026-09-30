"""Behavioral contract for the disposable, seed-owned Analysis demo account."""

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.micronutrients import MICRONUTRIENT_BY_METRIC
from app.models import (
    ApiToken,
    GoogleHealthConnection,
    HealthSample,
    ImportBatch,
    NutritionTarget,
    User,
    UserAnalysisPreference,
    UserProviderPreference,
    UserUsageDay,
    WithingsConnection,
    YazioConnection,
)
from app.nutrition.enums import CoverageState, ObservationKind, PresenceState, ResolutionState
from app.nutrition.models import (
    NutritionConsumptionEvent,
    NutritionDailyProjection,
    NutritionDailyProjectionFact,
    NutritionDailyProjectionLineage,
    NutritionExternalIdentity,
    NutritionExternalIdentityLink,
    NutritionFieldObservation,
    NutritionFoodProfile,
    NutritionFoodSnapshot,
    NutritionIngestionRun,
    NutritionProjectionHead,
    NutritionSourceObservation,
)
from app.services.analysis_demo import (
    ANALYSIS_DEMO_SOURCE_TYPE,
    ANALYSIS_DEMO_USERNAME,
    AnalysisDemoOwnershipError,
    AnalysisDemoUnavailable,
    demo_source_instance_id,
    demo_user_id,
    remove_analysis_demo,
    seed_analysis_demo,
)
from app.source_priority.models import SourcePriorityPolicy

TODAY = date(2026, 9, 29)
PASSWORD = "synthetic-analysis-demo-password"
SELECTED_KEYS = (
    "weight_kg",
    "dietary_energy_kcal",
    "vitamin_a_ug",
    "active_energy_kcal",
)


def _rows(db: Session, model: type, user_id: UUID) -> list:
    return list(db.scalars(select(model).where(model.user_id == user_id)))


def _utc_timestamp(value: datetime | None) -> datetime | None:
    """Compare persisted SQLite timestamps by instant, not driver tzinfo."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _preferences(db: Session, user_id: UUID) -> tuple[tuple, ...]:
    return tuple(
        (row.analysis_key, row.position, _utc_timestamp(row.created_at), _utc_timestamp(row.updated_at))
        for row in sorted(_rows(db, UserAnalysisPreference, user_id), key=lambda item: item.position)
    )


def _samples(db: Session, user_id: UUID) -> tuple[tuple, ...]:
    return tuple(
        sorted(
            (
                row.id,
                row.import_batch_id,
                row.external_sample_id,
                row.fingerprint,
                row.source_type,
                row.source_identifier,
                row.metric_type,
                row.local_date,
                row.value,
                row.unit,
                _utc_timestamp(row.start_at),
                _utc_timestamp(row.end_at),
            )
            for row in _rows(db, HealthSample, user_id)
        )
    )


def _batches(db: Session, user_id: UUID) -> tuple[tuple, ...]:
    return tuple(
        (
            row.id,
            row.source_type,
            row.client_identifier,
            row.connector_variant,
            row.status,
            _utc_timestamp(row.started_at),
            _utc_timestamp(row.finished_at),
            row.received,
            row.inserted,
            row.updated,
            row.skipped,
            row.failed,
        )
        for row in _rows(db, ImportBatch, user_id)
    )


def _account_snapshot(db: Session, user_id: UUID) -> tuple:
    account = db.get(User, user_id)
    assert account is not None
    return (
        account.id,
        account.username,
        account.password_hash,
        account.is_admin,
        account.is_active,
        _preferences(db, user_id),
        _batches(db, user_id),
        _samples(db, user_id),
        tuple(
            sorted(
                (row.id, row.valid_from, row.valid_to, row.calories_kcal, row.protein_g)
                for row in _rows(db, NutritionTarget, user_id)
            )
        ),
        tuple(
            (row.id, row.source_identifier, row.sync_enabled, row.historical_sync_state)
            for row in _rows(db, YazioConnection, user_id)
        ),
        tuple(
            (row.id, row.client_id, row.state, row.sync_state)
            for row in _rows(db, GoogleHealthConnection, user_id)
        ),
        tuple(
            (row.id, row.client_id, row.withings_user_id, row.state, row.sync_state)
            for row in _rows(db, WithingsConnection, user_id)
        ),
        tuple((row.id, row.token_hash) for row in _rows(db, ApiToken, user_id)),
    )


def _real_user_snapshot(db: Session, user: User) -> tuple:
    return _account_snapshot(db, user.id)


def _add_real_user_data(db: Session, user: User) -> None:
    batch = ImportBatch(user_id=user.id, source_type="apple_health_xml", status="completed")
    db.add(batch)
    db.flush()
    db.add_all(
        [
            HealthSample(
                user_id=user.id,
                import_batch_id=batch.id,
                external_sample_id="real-weight-2026-09-28",
                fingerprint="real-weight-sample-fingerprint",
                source_type="apple_health_xml",
                source_identifier="real-user-device",
                metric_type="weight_kg",
                value=Decimal("71.2"),
                unit="kg",
                original_value=Decimal("71.2"),
                original_unit="kg",
                start_at=datetime(2026, 9, 28, 12, tzinfo=UTC),
                end_at=datetime(2026, 9, 28, 12, tzinfo=UTC),
                local_date=date(2026, 9, 28),
                timezone="Europe/Berlin",
            ),
            UserAnalysisPreference(user_id=user.id, analysis_key="protein_g", position=0),
            UserAnalysisPreference(user_id=user.id, analysis_key="weight_kg", position=1),
        ]
    )
    db.commit()


def _seed(db: Session) -> UUID:
    return seed_analysis_demo(db, password=PASSWORD, today=TODAY)


def test_seed_requires_an_existing_real_account(db: Session) -> None:
    with pytest.raises(AnalysisDemoOwnershipError):
        _seed(db)
    assert list(db.scalars(select(User))) == []
    assert list(db.scalars(select(ImportBatch))) == []


def test_seed_uses_dedicated_stable_non_admin_identity_and_single_completed_anchor(
    db: Session, user: User
) -> None:
    seeded_id = _seed(db)
    account = db.get(User, seeded_id)
    assert account is not None
    assert seeded_id == demo_user_id()
    assert seeded_id.version == 5
    assert seeded_id != user.id
    assert account.username == ANALYSIS_DEMO_USERNAME == "calograph-analysis-demo"
    assert account.is_admin is False
    assert account.is_active is True
    assert account.password_hash != PASSWORD
    assert demo_source_instance_id(seeded_id).version == 5
    assert demo_source_instance_id(seeded_id) == demo_source_instance_id(seeded_id)

    batches = _rows(db, ImportBatch, seeded_id)
    assert len(batches) == 1
    assert batches[0].status == "completed"
    assert batches[0].client_identifier == "analysis-demo:v2:2026-09-29"
    assert batches[0].connector_variant == "analysis-demo-v2"
    assert batches[0].source_type == ANALYSIS_DEMO_SOURCE_TYPE == "analysis_demo_offline"
    samples = _rows(db, HealthSample, seeded_id)
    assert samples
    assert all(row.import_batch_id == batches[0].id for row in samples)
    assert all(row.source_type == ANALYSIS_DEMO_SOURCE_TYPE for row in samples)
    assert all(row.source_identifier == str(demo_source_instance_id(seeded_id)) for row in samples)
    assert len({row.external_sample_id for row in samples}) == len(samples)
    assert len({row.fingerprint for row in samples}) == len(samples)
    assert _rows(db, NutritionTarget, seeded_id) == []
    assert _rows(db, ApiToken, seeded_id) == []
    assert _rows(db, YazioConnection, seeded_id) == []
    assert _rows(db, GoogleHealthConnection, seeded_id) == []
    assert _rows(db, WithingsConnection, seeded_id) == []


def test_seed_keeps_nutrition_out_of_legacy_scalar_health_samples(db: Session, user: User) -> None:
    seeded_id = _seed(db)
    assert {sample.metric_type for sample in _rows(db, HealthSample, seeded_id)} == {
        "weight_kg",
        "active_energy_kcal",
    }
    assert tuple(key for key, *_ in _preferences(db, seeded_id)) == SELECTED_KEYS


def test_seed_persists_exact_ordered_analysis_selection(db: Session, user: User) -> None:
    seeded_id = _seed(db)
    assert tuple((key, position) for key, position, *_ in _preferences(db, seeded_id)) == tuple(
        (key, position) for position, key in enumerate(SELECTED_KEYS)
    )


def test_samples_cover_last_seven_thirty_and_ninety_days_with_zero_distinct_from_absent(
    db: Session, user: User
) -> None:
    seeded_id = _seed(db)
    samples = _rows(db, HealthSample, seeded_id)
    weight = {row.local_date: row.value for row in samples if row.metric_type == "weight_kg"}
    activity = {row.local_date: row.value for row in samples if row.metric_type == "active_energy_kcal"}
    window = {TODAY - timedelta(days=offset) for offset in range(90)}
    assert weight and activity
    assert set(weight) <= window
    assert set(activity) <= window
    assert min(weight) == TODAY - timedelta(days=89)
    assert max(weight) == TODAY
    assert window - set(weight)  # Weight measurements are sparse, not fabricated every day.
    for days in (7, 30, 90):
        recent = {TODAY - timedelta(days=offset) for offset in range(days)}
        assert set(weight) & recent
        assert set(activity) & recent
    assert min(activity) <= TODAY - timedelta(days=80)
    assert any(value == Decimal("0") for value in activity.values())
    assert window - set(activity)  # No sample is different from a persisted zero sample.
    assert all(value > 0 for value in weight.values())


def test_repeat_with_new_today_and_password_preserves_rows_values_and_anchor(
    db: Session, user: User
) -> None:
    seeded_id = _seed(db)
    before = _account_snapshot(db, seeded_id)
    assert seed_analysis_demo(
        db, password="different-password-must-not-replace-original", today=TODAY + timedelta(days=31)
    ) == seeded_id
    assert _account_snapshot(db, seeded_id) == before
    assert len(_rows(db, ImportBatch, seeded_id)) == 1
    assert tuple((key, position) for key, position, *_ in _preferences(db, seeded_id)) == tuple(
        (key, position) for position, key in enumerate(SELECTED_KEYS)
    )


def test_repeat_seed_preserves_demo_after_authenticated_usage_day_is_recorded(
    db: Session, user: User
) -> None:
    seeded_id = _seed(db)
    db.add(UserUsageDay(user_id=seeded_id, activity_date=TODAY))
    db.commit()
    account_before = _account_snapshot(db, seeded_id)
    nutrition_before = _nutrition_snapshot(db, seeded_id)
    usage_days_before = tuple(
        (row.id, row.activity_date, _utc_timestamp(row.created_at))
        for row in _rows(db, UserUsageDay, seeded_id)
    )

    assert seed_analysis_demo(db, password=PASSWORD, today=TODAY) == seeded_id
    assert _account_snapshot(db, seeded_id) == account_before
    assert _nutrition_snapshot(db, seeded_id) == nutrition_before
    assert tuple(
        (row.id, row.activity_date, _utc_timestamp(row.created_at))
        for row in _rows(db, UserUsageDay, seeded_id)
    ) == usage_days_before


def test_remove_demo_after_authenticated_usage_day_is_recorded(db: Session, user: User) -> None:
    seeded_id = _seed(db)
    db.add(UserUsageDay(user_id=seeded_id, activity_date=TODAY))
    db.commit()

    assert remove_analysis_demo(db) is True
    assert db.get(User, seeded_id) is None
    assert _rows(db, UserUsageDay, seeded_id) == []
    assert remove_analysis_demo(db) is False


def test_repeat_seed_preserves_demo_after_legacy_provider_preferences_table_is_dropped(
    db: Session, user: User
) -> None:
    _add_real_user_data(db, user)
    seeded_id = _seed(db)
    # Model Alembic revision 20260918_0031's removal of the legacy table.
    db.execute(text("DROP TABLE user_provider_preferences"))
    db.commit()
    account_before = _account_snapshot(db, seeded_id)
    nutrition_before = _nutrition_snapshot(db, seeded_id)

    assert seed_analysis_demo(db, password=PASSWORD, today=TODAY) == seeded_id
    assert _account_snapshot(db, seeded_id) == account_before
    assert _nutrition_snapshot(db, seeded_id) == nutrition_before


@pytest.mark.parametrize("operation", ["seed", "remove"])
def test_modified_seed_sample_refuses_without_replacing_or_deleting_it(
    db: Session, user: User, operation: str
) -> None:
    seeded_id = _seed(db)
    altered = _rows(db, HealthSample, seeded_id)[0]
    altered.value = Decimal("999")
    db.commit()
    before = _account_snapshot(db, seeded_id)
    with pytest.raises(AnalysisDemoOwnershipError):
        if operation == "seed":
            seed_analysis_demo(db, password=PASSWORD, today=TODAY + timedelta(days=1))
        else:
            remove_analysis_demo(db)
    assert _account_snapshot(db, seeded_id) == before


def test_same_name_with_different_id_is_refused_without_mutation(db: Session, user: User) -> None:
    collision = User(username=ANALYSIS_DEMO_USERNAME, password_hash=user.password_hash)
    db.add(collision)
    db.commit()
    before = _account_snapshot(db, collision.id)
    for operation in (_seed, remove_analysis_demo):
        with pytest.raises(AnalysisDemoOwnershipError):
            operation(db)
        assert _account_snapshot(db, collision.id) == before
    assert db.get(User, demo_user_id()) is None


def test_production_refuses_initial_creation(db: Session, user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    before = _real_user_snapshot(db, user)
    monkeypatch.setattr(settings, "environment", "production")
    with pytest.raises(AnalysisDemoUnavailable):
        _seed(db)
    assert db.get(User, demo_user_id()) is None
    assert _real_user_snapshot(db, user) == before


@pytest.mark.parametrize("operation", ["seed", "remove"])
def test_production_rejects_operation_without_mutation(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    seeded_id = _seed(db)
    before = _account_snapshot(db, seeded_id)
    monkeypatch.setattr(settings, "environment", "production")
    with pytest.raises(AnalysisDemoUnavailable):
        if operation == "seed":
            seed_analysis_demo(db, password=PASSWORD, today=TODAY + timedelta(days=1))
        else:
            remove_analysis_demo(db)
    assert _account_snapshot(db, seeded_id) == before


@pytest.mark.parametrize("mismatch", ["missing", "extra", "reordered", "unknown"])
@pytest.mark.parametrize("operation", ["seed", "remove"])
def test_changed_seed_owned_preferences_refuse_without_mutation(
    db: Session, user: User, mismatch: str, operation: str
) -> None:
    seeded_id = _seed(db)
    preferences = {row.analysis_key: row for row in _rows(db, UserAnalysisPreference, seeded_id)}
    if mismatch == "missing":
        db.delete(preferences["vitamin_a_ug"])
    elif mismatch == "extra":
        db.add(UserAnalysisPreference(user_id=seeded_id, analysis_key="protein_g", position=4))
    elif mismatch == "unknown":
        preferences["vitamin_a_ug"].analysis_key = "unknown_analysis_key"
    else:
        preferences["weight_kg"].position = 4
        db.flush()
        preferences["dietary_energy_kcal"].position = 0
        db.flush()
        preferences["weight_kg"].position = 1
    db.commit()
    before = _account_snapshot(db, seeded_id)
    with pytest.raises(AnalysisDemoOwnershipError):
        if operation == "seed":
            seed_analysis_demo(db, password=PASSWORD, today=TODAY + timedelta(days=1))
        else:
            remove_analysis_demo(db)
    assert _account_snapshot(db, seeded_id) == before


def _inject_foreign_ownership(db: Session, seeded_id: UUID, conflict: str) -> None:
    if conflict == "health":
        own_batch = _rows(db, ImportBatch, seeded_id)[0]
        db.add(
            HealthSample(
                user_id=seeded_id,
                import_batch_id=own_batch.id,
                external_sample_id="foreign-watch-weight",
                fingerprint="foreign-watch-weight-fingerprint",
                source_type="apple_health_xml",
                source_identifier="foreign-watch",
                metric_type="weight_kg",
                value=Decimal("75"),
                unit="kg",
                original_value=Decimal("75"),
                original_unit="kg",
                start_at=datetime(2026, 9, 29, 12, tzinfo=UTC),
                end_at=datetime(2026, 9, 29, 12, tzinfo=UTC),
                local_date=TODAY,
                timezone="Europe/Berlin",
            )
        )
    elif conflict == "provider_yazio":
        db.add(
            YazioConnection(
                user_id=seeded_id,
                encrypted_email=b"synthetic-email",
                encrypted_password=b"synthetic-password",
                source_identifier="foreign-provider-account",
            )
        )
    elif conflict == "provider_google":
        db.add(
            GoogleHealthConnection(
                user_id=seeded_id,
                client_id="foreign-google-client",
                state="active",
                sync_state="idle",
            )
        )
    elif conflict == "provider_withings":
        db.add(
            WithingsConnection(
                user_id=seeded_id,
                client_id="foreign-withings-client",
                withings_user_id="foreign-withings-user",
                state="active",
                sync_state="idle",
            )
        )
    elif conflict == "target":
        db.add(
            NutritionTarget(
                user_id=seeded_id,
                valid_from=TODAY,
                calories_kcal=Decimal("2100"),
                protein_g=Decimal("100"),
            )
        )
    else:
        db.add(
            ApiToken(
                user_id=seeded_id,
                label="foreign-account-token",
                token_prefix="foreign-demo",
                token_hash="f" * 64,
            )
        )
    db.commit()


@pytest.mark.parametrize(
    "conflict",
    ["health", "provider_yazio", "provider_google", "provider_withings", "target", "token"],
)
@pytest.mark.parametrize("operation", ["seed", "remove"])
def test_foreign_ownership_refuses_seed_or_removal_without_mutation(
    db: Session, user: User, conflict: str, operation: str
) -> None:
    seeded_id = _seed(db)
    _inject_foreign_ownership(db, seeded_id, conflict)
    before = _account_snapshot(db, seeded_id)
    with pytest.raises(AnalysisDemoOwnershipError):
        if operation == "seed":
            seed_analysis_demo(db, password=PASSWORD, today=TODAY + timedelta(days=2))
        else:
            remove_analysis_demo(db)
    assert _account_snapshot(db, seeded_id) == before


def test_remove_once_then_noop_preserves_real_users_rows(db: Session, user: User) -> None:
    _add_real_user_data(db, user)
    real_before = _real_user_snapshot(db, user)
    seeded_id = _seed(db)
    assert _real_user_snapshot(db, user) == real_before
    assert _rows(db, UserAnalysisPreference, seeded_id)
    assert remove_analysis_demo(db) is True
    assert db.get(User, seeded_id) is None
    assert _rows(db, HealthSample, seeded_id) == []
    assert _rows(db, ImportBatch, seeded_id) == []
    assert _rows(db, UserAnalysisPreference, seeded_id) == []
    assert _real_user_snapshot(db, user) == real_before
    assert remove_analysis_demo(db) is False
    assert _real_user_snapshot(db, user) == real_before
    assert {row.id for row in db.scalars(select(User))} == {user.id}
    assert {row.user_id for row in db.scalars(select(HealthSample))} == {user.id}
    assert {row.user_id for row in db.scalars(select(ImportBatch))} == {user.id}
    assert {row.user_id for row in db.scalars(select(UserAnalysisPreference))} == {user.id}
    assert {row.user_id for row in db.scalars(select(NutritionTarget))} == {user.id}


def test_remove_demo_after_legacy_provider_preferences_table_is_dropped(
    db: Session, user: User
) -> None:
    _add_real_user_data(db, user)
    real_before = _real_user_snapshot(db, user)
    seeded_id = _seed(db)
    db.execute(text("DROP TABLE user_provider_preferences"))
    db.commit()

    assert remove_analysis_demo(db) is True
    assert db.get(User, seeded_id) is None
    assert _rows(db, HealthSample, seeded_id) == []
    assert _rows(db, ImportBatch, seeded_id) == []
    assert _rows(db, UserAnalysisPreference, seeded_id) == []
    assert all(_rows(db, model, seeded_id) == [] for model in _NUTRITION_MODELS)
    assert _real_user_snapshot(db, user) == real_before
    assert remove_analysis_demo(db) is False
    assert _real_user_snapshot(db, user) == real_before


def test_legacy_provider_preference_refuses_reseed_and_removal_without_mutation(
    db: Session, user: User
) -> None:
    seeded_id = _seed(db)
    db.add(
        UserProviderPreference(user_id=seeded_id, data_area="nutrition", provider_key="legacy")
    )
    db.commit()
    account_before = _account_snapshot(db, seeded_id)
    nutrition_before = _nutrition_snapshot(db, seeded_id)

    with pytest.raises(AnalysisDemoOwnershipError):
        seed_analysis_demo(db, password=PASSWORD, today=TODAY)
    assert _account_snapshot(db, seeded_id) == account_before
    assert _nutrition_snapshot(db, seeded_id) == nutrition_before

    with pytest.raises(AnalysisDemoOwnershipError):
        remove_analysis_demo(db)
    assert _account_snapshot(db, seeded_id) == account_before
    assert _nutrition_snapshot(db, seeded_id) == nutrition_before


def test_unrelated_account_cannot_occupy_deterministic_id(db: Session, user: User) -> None:
    collision = User(id=demo_user_id(), username=f"ordinary-{uuid4()}", password_hash=user.password_hash)
    db.add(collision)
    db.commit()
    before = _account_snapshot(db, collision.id)
    with pytest.raises(AnalysisDemoOwnershipError):
        _seed(db)
    assert _account_snapshot(db, collision.id) == before
    with pytest.raises(AnalysisDemoOwnershipError):
        remove_analysis_demo(db)
    assert _account_snapshot(db, collision.id) == before


_NUTRITION_MODELS = (
    NutritionIngestionRun,
    NutritionSourceObservation,
    NutritionExternalIdentity,
    NutritionExternalIdentityLink,
    NutritionFoodProfile,
    NutritionFoodSnapshot,
    NutritionConsumptionEvent,
    NutritionFieldObservation,
    NutritionDailyProjection,
    NutritionDailyProjectionFact,
    NutritionDailyProjectionLineage,
    NutritionProjectionHead,
)
_SEVEN_METRICS = {
    "dietary_energy_kcal",
    "protein_g",
    "carbohydrates_g",
    "fat_g",
    "fiber_g",
    "sugar_g",
    "saturated_fat_g",
}


def _nutrition_snapshot(db: Session, user_id: UUID) -> tuple:
    """Compare persisted identity, ownership, fields and projections across replay/refusal."""
    return tuple(
        (
            model.__tablename__,
            tuple(
                sorted(
                    (
                        tuple(
                            _utc_timestamp(value) if isinstance(value, datetime) else value
                            for value in (
                                getattr(row, column.name) for column in model.__table__.columns
                            )
                        )
                        for row in _rows(db, model, user_id)
                    ),
                    key=repr,
                )
            ),
        )
        for model in _NUTRITION_MODELS
    )


def _interrupt_demo_projection_after_first_day(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> tuple[UUID, Callable[..., object]]:
    import app.services.analysis_demo_nutrition as nutrition

    real_rebuild = nutrition.rebuild_nutrition_day
    attempts = 0

    def fail_second_day(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise RuntimeError("interrupted demo projection")
        return real_rebuild(*args, **kwargs)

    monkeypatch.setattr(nutrition, "rebuild_nutrition_day", fail_second_day)
    with pytest.raises(RuntimeError, match="interrupted demo projection"):
        _seed(db)
    return demo_user_id(), real_rebuild


def test_initial_demo_evidence_commit_is_atomic_with_the_account(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.services.analysis_demo_nutrition as nutrition

    before = _nutrition_snapshot(db, user.id)

    def fail_before_first_source(*_args, **_kwargs):
        raise RuntimeError("source persistence interrupted")

    monkeypatch.setattr(nutrition, "get_or_create_source_observation", fail_before_first_source)
    with pytest.raises(RuntimeError, match="source persistence interrupted"):
        _seed(db)
    assert db.get(User, demo_user_id()) is None
    assert not _rows(db, NutritionIngestionRun, demo_user_id())
    assert _nutrition_snapshot(db, user.id) == before


def test_interrupted_demo_projection_resumes_without_replacing_evidence(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    demo_id, real_rebuild = _interrupt_demo_projection_after_first_day(db, monkeypatch)
    run = _rows(db, NutritionIngestionRun, demo_id)
    assert len(run) == 1 and run[0].status == "running"
    assert len(_rows(db, NutritionDailyProjection, demo_id)) == 1
    event_ids = {row.id for row in _rows(db, NutritionConsumptionEvent, demo_id)}
    source_ids = {row.id for row in _rows(db, NutritionSourceObservation, demo_id)}

    import app.services.analysis_demo_nutrition as nutrition

    monkeypatch.setattr(nutrition, "rebuild_nutrition_day", real_rebuild)
    assert seed_analysis_demo(
        db, password="different-synthetic-password", today=TODAY + timedelta(days=1)
    ) == demo_id
    assert _rows(db, NutritionIngestionRun, demo_id)[0].status == "completed"
    assert len(_rows(db, NutritionDailyProjection, demo_id)) == 84
    assert {row.id for row in _rows(db, NutritionConsumptionEvent, demo_id)} == event_ids
    assert {row.id for row in _rows(db, NutritionSourceObservation, demo_id)} == source_ids


def test_interrupted_demo_projection_can_be_removed_safely(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    demo_id, _real_rebuild = _interrupt_demo_projection_after_first_day(db, monkeypatch)
    before = _nutrition_snapshot(db, user.id)
    assert remove_analysis_demo(db) is True
    assert db.get(User, demo_id) is None
    assert _nutrition_snapshot(db, demo_id) == tuple(
        (table, ()) for table, _rows_before in before
    )
    assert _nutrition_snapshot(db, user.id) == before


def test_interrupted_demo_projection_refuses_recovery_and_removal_after_tampering(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    demo_id, _real_rebuild = _interrupt_demo_projection_after_first_day(db, monkeypatch)
    fact = _rows(db, NutritionDailyProjectionFact, demo_id)[0]
    fact.value += Decimal("1")
    db.commit()
    before = _nutrition_snapshot(db, demo_id)
    with pytest.raises(AnalysisDemoOwnershipError):
        seed_analysis_demo(db, password=PASSWORD, today=TODAY + timedelta(days=1))
    assert _nutrition_snapshot(db, demo_id) == before
    with pytest.raises(AnalysisDemoOwnershipError):
        remove_analysis_demo(db)
    assert _nutrition_snapshot(db, demo_id) == before


def test_seed_persists_named_meals_with_exact_breakfast_observation(
    db: Session, user: User
) -> None:
    seeded_id = _seed(db)
    start = TODAY - timedelta(days=89)
    fixed_day = start + timedelta(days=10)
    events = _rows(db, NutritionConsumptionEvent, seeded_id)
    snapshots = {row.id: row for row in _rows(db, NutritionFoodSnapshot, seeded_id)}
    observations = {row.id: row for row in _rows(db, NutritionSourceObservation, seeded_id)}
    day_events = [event for event in events if event.local_date == fixed_day]
    assert len(day_events) >= 3
    assert len({snapshots[event.food_snapshot_id].name for event in day_events}) >= 3
    assert {"breakfast", "lunch", "dinner", "snack"} <= {
        event.daytime for event in events
    }
    assert all(event.food_snapshot_id in snapshots for event in events)
    assert all(snapshots[event.food_snapshot_id].name for event in events)
    assert len({event.local_date for event in events}) >= 75
    assert len({snapshots[event.food_snapshot_id].name for event in events}) >= 4
    assert any(
        event.local_date != fixed_day
        and snapshots[event.food_snapshot_id].name != "Haferflocken mit Beeren"
        for event in events
    )

    breakfast = next(
        event
        for event in day_events
        if snapshots[event.food_snapshot_id].name == "Haferflocken mit Beeren"
    )
    assert breakfast.daytime == "breakfast"
    assert breakfast.provider_key == "analysis_demo"
    assert breakfast.source_instance_id == demo_source_instance_id(seeded_id)
    assert breakfast.provider_timezone == "Europe/Berlin"
    assert observations[breakfast.source_observation_id].local_date == fixed_day
    assert observations[breakfast.source_observation_id].provider_timezone == "Europe/Berlin"
    assert any(
        event.daytime == "lunch" and snapshots[event.food_snapshot_id].name == "Linsensalat"
        for event in day_events
    )
    fields = {
        field.metric_key: field.canonical_value
        for field in db.scalars(
            select(NutritionFieldObservation).where(
                NutritionFieldObservation.user_id == seeded_id,
                NutritionFieldObservation.source_observation_id == breakfast.source_observation_id,
            )
        )
    }
    assert {key: fields[key] for key in (
        "dietary_energy_kcal", "protein_g", "carbohydrates_g", "fat_g"
    )} == {
        "dietary_energy_kcal": Decimal("420"),
        "protein_g": Decimal("18"),
        "carbohydrates_g": Decimal("62"),
        "fat_g": Decimal("12"),
    }


def test_seed_records_seven_canonical_metrics_and_supported_micronutrients(
    db: Session, user: User
) -> None:
    seeded_id = _seed(db)
    fields = _rows(db, NutritionFieldObservation, seeded_id)
    assert fields
    keys = {field.metric_key for field in fields}
    assert keys >= _SEVEN_METRICS
    micro_keys = keys - _SEVEN_METRICS
    assert micro_keys
    assert micro_keys <= set(MICRONUTRIENT_BY_METRIC)
    expected_micros = {
        "vitamin_a_ug",
        "vitamin_c_mg",
        "vitamin_d_ug",
        "folate_ug",
        "calcium_mg",
        "iron_mg",
        "potassium_mg",
        "magnesium_mg",
    } & set(MICRONUTRIENT_BY_METRIC)
    assert expected_micros <= micro_keys
    assert all(field.canonical_value is not None for field in fields)
    assert all(field.canonical_unit for field in fields)
    assert all(field.user_id == seeded_id for field in fields)
    assert not {"steps", "water_ml", "body_fat_percent"} & keys


def test_repeat_seed_preserves_canonical_evidence_and_projection_identity(
    db: Session, user: User
) -> None:
    seeded_id = _seed(db)
    first = _nutrition_snapshot(db, seeded_id)
    assert len(_rows(db, NutritionIngestionRun, seeded_id)) == 1
    assert _rows(db, NutritionConsumptionEvent, seeded_id)
    assert _rows(db, NutritionFieldObservation, seeded_id)
    assert _rows(db, NutritionFoodSnapshot, seeded_id)
    assert _rows(db, NutritionDailyProjection, seeded_id)
    assert _rows(db, NutritionProjectionHead, seeded_id)
    assert _rows(db, NutritionDailyProjectionFact, seeded_id)
    assert seed_analysis_demo(db, password="different-synthetic-password", today=TODAY + timedelta(days=31)) == seeded_id
    db.expire_all()
    assert _nutrition_snapshot(db, seeded_id) == first


def _foreign_nutrition_observation(
    db: Session, *, user_id: UUID, source_instance_id: UUID, provider_key: str
) -> NutritionSourceObservation:
    run = NutritionIngestionRun(
        user_id=user_id,
        provider_key=provider_key,
        source_instance_id=source_instance_id,
        status="completed",
        coverage_state=CoverageState.COMPLETE.value,
    )
    db.add(run)
    db.flush()
    observation = NutritionSourceObservation(
        user_id=user_id,
        ingestion_run_id=run.id,
        provider_key=provider_key,
        source_instance_id=source_instance_id,
        observation_kind=ObservationKind.CONSUMPTION_EVENT.value,
        source_namespace="foreign.synthetic",
        source_record_id=f"foreign-{uuid4()}",
        observation_fingerprint="f" * 64,
        local_date=TODAY,
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
    )
    db.add(observation)
    db.flush()
    return observation


def _foreign_nutrition_projection(
    db: Session, *, user_id: UUID, source_observation_id: UUID
) -> None:
    policy = SourcePriorityPolicy(
        user_id=user_id, version=1, effective_from=datetime(2026, 1, 1, tzinfo=UTC)
    )
    db.add(policy)
    db.flush()
    projection = NutritionDailyProjection(
        user_id=user_id,
        local_date=TODAY,
        projection_version=1,
        projection_algorithm_version="foreign-synthetic-test",
        priority_policy_id=policy.id,
        input_watermark="foreign-synthetic-evidence",
        projection_status="ready",
    )
    db.add(projection)
    db.flush()
    fact = NutritionDailyProjectionFact(
        user_id=user_id,
        projection_id=projection.id,
        metric_key="protein_g",
        value=Decimal("42"),
        unit="g",
        selected_provider_key="apple_health",
        selected_granularity="event",
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
        lineage_state="confirmed",
    )
    db.add(fact)
    db.flush()
    db.add_all([
        NutritionDailyProjectionLineage(
            user_id=user_id,
            projection_fact_id=fact.id,
            source_observation_id=source_observation_id,
            provider_key="apple_health",
            role="selected",
            granularity="event",
            contribution_value=Decimal("42"),
            presence_state=PresenceState.SUPPLIED.value,
            coverage_state=CoverageState.COMPLETE.value,
        ),
        NutritionProjectionHead(
            user_id=user_id, local_date=TODAY, current_projection_id=projection.id
        ),
    ])


def test_removal_clears_demo_projections_without_touching_foreign_evidence(
    db: Session, user: User
) -> None:
    foreign = _foreign_nutrition_observation(
        db, user_id=user.id, source_instance_id=uuid4(), provider_key="apple_health"
    )
    _foreign_nutrition_projection(db, user_id=user.id, source_observation_id=foreign.id)
    _add_real_user_data(db, user)
    db.commit()
    foreign_before = _nutrition_snapshot(db, user.id)
    real_before = _real_user_snapshot(db, user)
    seeded_id = _seed(db)
    assert _rows(db, NutritionProjectionHead, seeded_id)
    assert _rows(db, NutritionDailyProjection, seeded_id)
    assert remove_analysis_demo(db) is True
    assert db.get(User, seeded_id) is None
    assert all(_rows(db, model, seeded_id) == [] for model in _NUTRITION_MODELS)
    assert db.get(NutritionSourceObservation, foreign.id) is not None
    assert _nutrition_snapshot(db, user.id) == foreign_before
    assert _real_user_snapshot(db, user) == real_before
    assert remove_analysis_demo(db) is False
    assert _nutrition_snapshot(db, user.id) == foreign_before


@pytest.mark.parametrize("operation", ["seed", "remove"])
@pytest.mark.parametrize("conflict", ["foreign_run", "foreign_event", "foreign_source_account"])
def test_mixed_nutrition_ownership_refuses_before_any_mutation(
    db: Session, user: User, operation: str, conflict: str
) -> None:
    seeded_id = _seed(db)
    if conflict == "foreign_run":
        _foreign_nutrition_observation(
            db, user_id=seeded_id, source_instance_id=uuid4(), provider_key="apple_health"
        )
    elif conflict == "foreign_event":
        event = _rows(db, NutritionConsumptionEvent, seeded_id)[0]
        event.provider_key = "apple_health"
    else:
        _foreign_nutrition_observation(
            db,
            user_id=user.id,
            source_instance_id=demo_source_instance_id(seeded_id),
            provider_key="analysis_demo",
        )
    db.commit()
    demo_before = _nutrition_snapshot(db, seeded_id)
    foreign_before = _nutrition_snapshot(db, user.id)
    account_before = _account_snapshot(db, seeded_id)
    real_before = _real_user_snapshot(db, user)
    with pytest.raises(AnalysisDemoOwnershipError):
        if operation == "seed":
            seed_analysis_demo(db, password=PASSWORD, today=TODAY + timedelta(days=1))
        else:
            remove_analysis_demo(db)
    assert _nutrition_snapshot(db, seeded_id) == demo_before
    assert _nutrition_snapshot(db, user.id) == foreign_before
    assert _account_snapshot(db, seeded_id) == account_before
    assert _real_user_snapshot(db, user) == real_before


def test_foreign_account_using_demo_nutrition_source_blocks_initial_seed(
    db: Session, user: User
) -> None:
    foreign = _foreign_nutrition_observation(
        db,
        user_id=user.id,
        source_instance_id=demo_source_instance_id(demo_user_id()),
        provider_key="analysis_demo",
    )
    db.commit()
    before = _nutrition_snapshot(db, user.id)
    with pytest.raises(AnalysisDemoOwnershipError):
        _seed(db)
    assert db.get(User, demo_user_id()) is None
    assert db.get(NutritionSourceObservation, foreign.id) is not None
    assert _nutrition_snapshot(db, user.id) == before
