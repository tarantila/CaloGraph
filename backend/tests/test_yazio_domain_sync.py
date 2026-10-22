from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select

from app.config import Settings, settings
from app.database import SessionLocal
from app.models import GoogleHealthConnection, HealthSample, ImportBatch, User, YazioConnection
from app.nutrition.models import (
    NutritionConsumptionEvent,
    NutritionDailyProjection,
    NutritionFieldObservation,
    NutritionFoodProfile,
    NutritionFoodSnapshot,
    NutritionIngestionRun,
    NutritionSourceObservation,
)
from app.nutrition.projection import lifecycle as projection_lifecycle
from app.nutrition.projection.lifecycle import (
    NutritionProjectionLifecycleError,
    NutritionProjectionLifecycleResult,
)
from app.schemas import ImportSummary
from app.security_events import security_reference
from app.services import yazio_projection_retry, yazio_sync, yazio_transport
from app.services.credential_crypto import encrypt_credential
from app.services.yazio_provider import (
    YazioConsumedProduct,
    YazioConsumedSimpleProduct,
    YazioDailyNutrientSummary,
    YazioFoodDiary,
    YazioNutrientValues,
    YazioProductProfile,
    YazioProviderAuthenticationError,
    YazioProviderInvalidResponseError,
    YazioProviderNetworkTimeoutError,
)
from app.services.yazio_sync import YazioSyncError, run_manual_yazio_sync
from app.source_priority.application import create_policy_with_rules
from app.source_priority.contracts import PriorityRuleSpec
from app.source_priority.models import SourcePriorityPolicy, SourcePriorityRule

DAY = date(2026, 9, 1)


def _connection(db, user) -> YazioConnection:
    if not settings.credential_encryption_key:
        settings.credential_encryption_key = Fernet.generate_key().decode()
    connection = YazioConnection(
        user_id=user.id,
        source_identifier=f"yazio:{user.id}",
        encrypted_email=encrypt_credential("owner@example.com"),
        encrypted_password=encrypt_credential("yazio-password"),
        sync_days=1,
        sync_enabled=True,
    )
    db.add(connection)
    db.commit()
    return connection


def _aggregate() -> dict[str, object]:
    return {
        DAY.isoformat(): {
            "daily_summary": {
                "meals": {
                    "dinner": {
                        "nutrients": {
                            "energy.energy": 1800,
                            "nutrient.protein": 120,
                        }
                    }
                }
            }
        }
    }


def _diary() -> YazioFoodDiary:
    return YazioFoodDiary(
        requested_start_day=DAY,
        requested_end_day=DAY,
        consumed_products=(),
        consumed_simple_products=(),
        product_profiles=(),
        daily_summaries=(
            YazioDailyNutrientSummary(
                local_date=DAY,
                nutrients=YazioNutrientValues(energy=Decimal("1800")),
                energy_goal=None,
            ),
        ),
    )


def _configure_sdk_transport(
    monkeypatch, events: list[str], diary: YazioFoodDiary | None = None
) -> None:
    monkeypatch.setattr(settings, "yazio_enabled", True)
    monkeypatch.setattr(settings, "yazio_sdk_client_secret", "test-sdk-secret")

    def fetch_domain(email, password, start_day, end_day):
        del email, password, start_day, end_day
        events.extend(("aggregate", "consumed-items", "daily-summary", "product"))
        return _aggregate(), diary or _diary()

    monkeypatch.setattr(yazio_sync, "fetch_yazio_domain_transport", fetch_domain)


def test_enabled_sdk_reads_everything_before_shared_transaction(
    db, user, monkeypatch
) -> None:
    _connection(db, user)
    events: list[str] = []
    _configure_sdk_transport(monkeypatch, events)
    original = yazio_sync._persist_import_locked

    def mark_transaction(*args, **kwargs):
        events.append("shared-transaction")
        return original(*args, **kwargs)

    monkeypatch.setattr(yazio_sync, "_persist_import_locked", mark_transaction)
    assert run_manual_yazio_sync(user.id, now=datetime(2026, 9, 1, 12, tzinfo=UTC))
    assert events[:4] == ["aggregate", "consumed-items", "daily-summary", "product"]
    assert events.index("shared-transaction") == 4


def test_manual_sync_uses_configured_sdk_domain(
    db, user, monkeypatch
) -> None:
    _connection(db, user)
    monkeypatch.setattr(settings, "yazio_sdk_client_secret", "test-sdk-secret")

    diary = YazioFoodDiary(
        requested_start_day=DAY,
        requested_end_day=DAY,
        consumed_products=(),
        consumed_simple_products=(
            YazioConsumedSimpleProduct(
                consumed_item_id="manual-sdk-food-1",
                amount=Decimal("1"),
                provider_civil_datetime=datetime(2026, 9, 1, 12),
                local_date=DAY,
                daytime="dinner",
                nutrients=YazioNutrientValues(energy=Decimal("180")),
                serving="portion",
                serving_quantity=Decimal("1"),
                name="Synthetic food",
            ),
        ),
        product_profiles=(),
        daily_summaries=(
            YazioDailyNutrientSummary(
                local_date=DAY,
                nutrients=YazioNutrientValues(energy=Decimal("1800")),
                energy_goal=None,
            ),
        ),
    )

    def fetch_domain(*_args, **_kwargs):
        return _aggregate(), diary

    monkeypatch.setattr(yazio_sync, "fetch_yazio_domain_transport", fetch_domain)
    now = datetime(2026, 9, 1, 12, tzinfo=UTC)
    summary = run_manual_yazio_sync(user.id, now=now)

    assert summary.status == "completed"
    assert db.scalar(select(func.count()).select_from(ImportBatch)) == 1
    assert db.scalar(select(func.count()).select_from(NutritionIngestionRun)) == 1
    first_event_count = db.scalar(select(func.count()).select_from(NutritionConsumptionEvent))
    assert first_event_count > 0
    assert (
        db.scalar(select(func.count()).select_from(NutritionFieldObservation)) > 0
    )

    run_manual_yazio_sync(user.id, now=now)
    assert (
        db.scalar(select(func.count()).select_from(NutritionConsumptionEvent))
        == first_event_count
    )


def test_enabled_sync_commits_legacy_and_domain_rows_together(
    db, user, monkeypatch
) -> None:
    _connection(db, user)
    events: list[str] = []
    _configure_sdk_transport(monkeypatch, events)
    summary = run_manual_yazio_sync(user.id, now=datetime(2026, 9, 1, 12, tzinfo=UTC))
    assert summary.inserted == 2
    assert db.scalar(select(func.count()).select_from(HealthSample)) == 2
    assert db.scalar(select(func.count()).select_from(ImportBatch)) == 1
    assert db.scalar(select(func.count()).select_from(NutritionIngestionRun)) == 1
    assert db.scalar(select(func.count()).select_from(NutritionConsumptionEvent)) == 0


def test_domain_sync_invokes_lifecycle_after_commit_with_committed_run(
    db, user, monkeypatch
) -> None:
    _connection(db, user)
    events: list[str] = []
    _configure_sdk_transport(monkeypatch, events)
    original = yazio_sync._persist_import_locked

    def mark_transaction(*args, **kwargs):
        events.append("shared-transaction")
        return original(*args, **kwargs)

    monkeypatch.setattr(yazio_sync, "_persist_import_locked", mark_transaction)
    calls: list[dict[str, object]] = []

    def lifecycle(**kwargs):
        calls.append(kwargs)
        with SessionLocal() as lifecycle_db:
            run = lifecycle_db.get(NutritionIngestionRun, kwargs["ingestion_run_id"])
            observations = lifecycle_db.scalars(
                select(NutritionSourceObservation).where(
                    NutritionSourceObservation.ingestion_run_id == kwargs["ingestion_run_id"]
                )
            ).all()
        assert run is not None
        assert run.user_id == kwargs["user_id"]
        assert observations
        events.append("lifecycle")
        return NutritionProjectionLifecycleResult(
            user_id=kwargs["user_id"],
            ingestion_run_id=kwargs["ingestion_run_id"],
            affected_dates=(DAY,),
            created_dates=(),
            unchanged_dates=(),
            policy_missing_dates=(DAY,),
        )

    monkeypatch.setattr(yazio_projection_retry, "rebuild_replay_nutrition_days", lifecycle)
    summary = run_manual_yazio_sync(
        user.id, now=datetime(2026, 9, 1, 12, tzinfo=UTC)
    )

    assert summary.inserted == 2
    assert len(calls) == 1
    call = calls[0]
    assert set(call) == {"session_factory", "user_id", "ingestion_run_id", "policy_at"}
    assert call["session_factory"] is SessionLocal
    assert call["user_id"] == user.id
    run_id = db.scalar(select(NutritionIngestionRun.id))
    assert call["ingestion_run_id"] == run_id
    assert isinstance(call["ingestion_run_id"], type(user.id))
    assert isinstance(call["policy_at"], datetime)
    assert call["policy_at"].tzinfo is not None
    assert call["policy_at"].utcoffset() is not None
    assert events.index("shared-transaction") < events.index("lifecycle")


def test_domain_sync_accepts_policy_missing(db, user, monkeypatch) -> None:
    _connection(db, user)
    _configure_sdk_transport(monkeypatch, [])
    lifecycle_results: list[NutritionProjectionLifecycleResult] = []

    def lifecycle(**kwargs):
        result = NutritionProjectionLifecycleResult(
            user_id=kwargs["user_id"],
            ingestion_run_id=kwargs["ingestion_run_id"],
            affected_dates=(DAY,),
            created_dates=(),
            unchanged_dates=(),
            policy_missing_dates=(DAY,),
        )
        lifecycle_results.append(result)
        return result

    monkeypatch.setattr(yazio_projection_retry, "rebuild_replay_nutrition_days", lifecycle)
    summary = run_manual_yazio_sync(
        user.id, now=datetime(2026, 9, 1, 12, tzinfo=UTC)
    )

    assert isinstance(summary, ImportSummary)
    assert len(lifecycle_results) == 1
    assert lifecycle_results[0].policy_missing_dates == (DAY,)


def test_idempotent_domain_sync_replays_unchanged_without_duplicate_version(
    db, user, monkeypatch,
) -> None:
    _connection(db, user)
    _configure_sdk_transport(monkeypatch, [])
    original = yazio_projection_retry.rebuild_replay_nutrition_days
    results = []

    def lifecycle(**kwargs):
        result = original(**kwargs)
        results.append(result)
        return result

    monkeypatch.setattr(yazio_projection_retry, "rebuild_replay_nutrition_days", lifecycle)
    first = run_manual_yazio_sync(user.id, now=datetime(2026, 9, 1, 12, tzinfo=UTC))
    second = run_manual_yazio_sync(user.id, now=datetime(2026, 9, 1, 12, tzinfo=UTC))
    assert first.status == second.status == "completed"
    assert results[0].created_dates == (DAY,)
    assert results[1].affected_dates == (DAY,)
    assert results[1].unchanged_dates == (DAY,)
    assert list(db.scalars(select(NutritionDailyProjection.projection_version))) == [1]


def test_domain_lifecycle_failure_keeps_committed_evidence(
    db, user, monkeypatch
) -> None:
    _connection(db, user)
    _configure_sdk_transport(monkeypatch, [])

    def fail_lifecycle(**kwargs):
        raise RuntimeError(f"lifecycle failed for {kwargs['ingestion_run_id']}")

    monkeypatch.setattr(yazio_projection_retry, "rebuild_replay_nutrition_days", fail_lifecycle)
    with pytest.raises(RuntimeError, match="lifecycle failed"):
        run_manual_yazio_sync(user.id, now=datetime(2026, 9, 1, 12, tzinfo=UTC))

    assert db.scalar(select(func.count()).select_from(HealthSample)) == 2
    assert db.scalar(select(func.count()).select_from(ImportBatch)) == 1
    assert db.scalar(select(func.count()).select_from(NutritionIngestionRun)) == 1
    assert db.scalar(select(func.count()).select_from(NutritionSourceObservation)) == 1


def test_domain_failure_rolls_back_legacy_and_domain_rows(
    db, user, monkeypatch
) -> None:
    _connection(db, user)
    events: list[str] = []
    _configure_sdk_transport(monkeypatch, events)

    def fail_ingestion(*args, **kwargs):
        raise ValueError("domain failure")

    monkeypatch.setattr(yazio_sync, "ingest_yazio_food_diary", fail_ingestion)
    with pytest.raises(ValueError, match="domain failure"):
        run_manual_yazio_sync(user.id, now=datetime(2026, 9, 1, 12, tzinfo=UTC))
    assert db.scalar(select(func.count()).select_from(HealthSample)) == 0
    assert db.scalar(select(func.count()).select_from(ImportBatch)) == 0
    assert db.scalar(select(func.count()).select_from(NutritionIngestionRun)) == 0


def test_domain_transport_rejects_malformed_worker_result(monkeypatch) -> None:
    monkeypatch.setattr(
        yazio_transport,
        "_run_worker",
        lambda *_args, **_kwargs: {"aggregate": {}},
    )
    with pytest.raises(YazioProviderInvalidResponseError):
        yazio_transport.fetch_yazio_domain_transport(
            "owner@example.com",
            "yazio-password",
            DAY,
            DAY,
        )



@pytest.mark.parametrize("selector", ("legacy", "sdk", "garbage", ""))
@pytest.mark.parametrize("rollout", ("false", "true", "garbage"))
def test_stale_environment_cannot_disable_canonical_sdk_persistence(
    db, user, monkeypatch, selector, rollout,
):
    _connection(db, user)
    events: list[str] = []
    _configure_sdk_transport(monkeypatch, events)
    monkeypatch.setenv("YAZIO_PROVIDER", selector)
    monkeypatch.setenv("YAZIO_NUTRITION_DOMAIN_WRITE_ENABLED", rollout)
    configured = Settings(
        _env_file=None,
        environment="development",
        yazio_enabled=True,
        yazio_sdk_client_secret="synthetic-sdk-secret",
    )
    monkeypatch.setattr(yazio_sync, "settings", configured)

    summary = run_manual_yazio_sync(user.id, now=datetime(2026, 9, 1, 12, tzinfo=UTC))

    assert summary.inserted == 2
    assert db.scalar(select(func.count()).select_from(HealthSample)) == 2
    assert db.scalar(select(func.count()).select_from(NutritionIngestionRun)) == 1
    assert list(db.scalars(select(NutritionDailyProjection.projection_version))) == [1]


def _yazio_policy_snapshot(db, user) -> tuple[tuple[object, ...], ...]:
    db.expire_all()
    return tuple(
        (policy.id, policy.version, policy.effective_from)
        for policy in db.scalars(
            select(SourcePriorityPolicy)
            .where(SourcePriorityPolicy.user_id == user.id)
            .order_by(SourcePriorityPolicy.version)
        )
    )


def test_sdk_v22_bootstraps_single_provider_before_lifecycle_and_repeats_idempotently(
    db, user, monkeypatch
) -> None:
    _connection(db, user)
    events: list[str] = []
    _configure_sdk_transport(monkeypatch, events)
    lifecycle_results: list[NutritionProjectionLifecycleResult] = []
    original_bootstrap = yazio_projection_retry.bootstrap_nutrition_priority
    original_lifecycle = yazio_projection_retry.rebuild_replay_nutrition_days

    def bootstrap(**kwargs):
        events.append("bootstrap")
        return original_bootstrap(**kwargs)

    def lifecycle(**kwargs):
        events.append("lifecycle")
        result = original_lifecycle(**kwargs)
        lifecycle_results.append(result)
        return result

    monkeypatch.setattr(yazio_projection_retry, "bootstrap_nutrition_priority", bootstrap)
    monkeypatch.setattr(yazio_projection_retry, "rebuild_replay_nutrition_days", lifecycle)
    now = datetime(2026, 9, 1, 12, tzinfo=UTC)

    run_manual_yazio_sync(user.id, now=now)
    db.expire_all()
    first_projection = db.scalar(
        select(NutritionDailyProjection).where(NutritionDailyProjection.user_id == user.id)
    )
    assert first_projection is not None
    assert first_projection.projection_version == 1
    assert _yazio_policy_snapshot(db, user)
    assert events.index("bootstrap") < events.index("lifecycle")
    assert lifecycle_results[0].created_dates == (DAY,)

    run_manual_yazio_sync(user.id, now=now)
    db.expire_all()
    assert len(_yazio_policy_snapshot(db, user)) == 1
    projections = db.scalars(
        select(NutritionDailyProjection)
        .where(NutritionDailyProjection.user_id == user.id)
        .order_by(NutritionDailyProjection.projection_version)
    ).all()
    assert [projection.projection_version for projection in projections] == [1]
    assert lifecycle_results[1].unchanged_dates == (DAY,)


def test_sdk_v22_multi_provider_requires_selection_without_policy(db, user, monkeypatch) -> None:
    _connection(db, user)
    db.add(
        GoogleHealthConnection(
            client_id="yazio-domain-google-client",
            encrypted_client_secret=encrypt_credential("yazio-domain-google-client-secret"),
            user_id=user.id,
            encrypted_refresh_token=b"encrypted-refresh-token",
            granted_scopes=["https://www.googleapis.com/auth/googlehealth.nutrition.readonly"],
            state="active",
        )
    )
    db.commit()
    _configure_sdk_transport(monkeypatch, [])
    lifecycle_results: list[NutritionProjectionLifecycleResult] = []
    original_lifecycle = yazio_projection_retry.rebuild_replay_nutrition_days

    def lifecycle(**kwargs):
        result = original_lifecycle(**kwargs)
        lifecycle_results.append(result)
        return result

    monkeypatch.setattr(yazio_projection_retry, "rebuild_replay_nutrition_days", lifecycle)
    run_manual_yazio_sync(user.id, now=datetime(2026, 9, 1, 12, tzinfo=UTC))
    assert _yazio_policy_snapshot(db, user) == ()
    assert lifecycle_results[0].policy_missing_dates == (DAY,)
    assert db.scalar(select(func.count()).select_from(NutritionDailyProjection)) == 0


def test_sdk_v22_existing_policy_is_immutable_and_projection_uses_it(db, user, monkeypatch) -> None:
    _connection(db, user)
    effective_from = datetime(2026, 9, 1, 12, tzinfo=UTC)
    snapshot = create_policy_with_rules(
        db,
        user.id,
        1,
        effective_from,
        (
            PriorityRuleSpec(
                data_area="nutrition",
                metric_key=None,
                provider_key="yazio",
                priority_rank=1,
            ),
        ),
    )
    db.commit()
    before = _yazio_policy_snapshot(db, user)
    _configure_sdk_transport(monkeypatch, [])
    run_manual_yazio_sync(user.id, now=effective_from)
    db.expire_all()
    assert _yazio_policy_snapshot(db, user) == before
    projection = db.scalar(
        select(NutritionDailyProjection).where(NutritionDailyProjection.user_id == user.id)
    )
    assert projection is not None
    assert projection.priority_policy_id == snapshot.policy_id




def test_manual_sdk_domain_path_persists_nutrient_observations(db, user, monkeypatch) -> None:
    _connection(db, user)
    monkeypatch.setattr(settings, "yazio_enabled", True)
    monkeypatch.setattr(settings, "yazio_sdk_client_secret", "test-sdk-secret")
    called = False

    def fetch_domain(email, password, start_day, end_day):
        nonlocal called
        called = True
        del email, password, start_day, end_day
        return _aggregate(), _diary()

    monkeypatch.setattr(yazio_sync, "fetch_yazio_domain_transport", fetch_domain)
    summary = run_manual_yazio_sync(
        user.id,
        now=datetime(2026, 9, 1, 12, tzinfo=UTC),
    )

    assert called is True
    assert summary.inserted == 2
    assert db.scalar(select(func.count()).select_from(NutritionIngestionRun)) == 1
    assert db.scalar(select(func.count()).select_from(NutritionSourceObservation)) == 1
    assert db.scalar(select(func.count()).select_from(NutritionFieldObservation)) >= 1


def _range_diary() -> YazioFoodDiary:
    nutrients = YazioNutrientValues(
        energy=Decimal("1800"),
        protein=Decimal("120"),
        carb=Decimal("180"),
        fat=Decimal("60"),
        additional={"vitamin.b6": Decimal("0.0014")},
    )
    return replace(
        _diary(),
        consumed_products=(
            YazioConsumedProduct(
                consumed_item_id="configured-product-event",
                product_id="configured-product",
                amount=Decimal("100"),
                provider_civil_datetime=datetime(2026, 9, 1, 12),
                local_date=DAY,
                daytime="lunch",
                serving=None,
                serving_quantity=None,
            ),
        ),
        consumed_simple_products=(
            YazioConsumedSimpleProduct(
                consumed_item_id="configured-simple-event",
                amount=Decimal("1"),
                provider_civil_datetime=datetime(2026, 9, 1, 18),
                local_date=DAY,
                daytime="dinner",
                nutrients=nutrients,
                serving=None,
                serving_quantity=None,
                name="Synthetic dinner",
            ),
        ),
        product_profiles=(
            YazioProductProfile(
                product_id="configured-product",
                name="Synthetic food",
                producer=None,
                category=None,
                base_unit="g",
                nutrients=nutrients,
                servings=(),
                eans=(),
                language=None,
                countries=(),
                updated_at=None,
                is_verified=True,
                is_private=False,
                is_deleted=False,
            ),
        ),
        daily_summaries=(
            YazioDailyNutrientSummary(
                local_date=DAY,
                nutrients=nutrients,
                energy_goal=Decimal("2000"),
            ),
        ),
    )


def _range_aggregate() -> dict[str, object]:
    return {
        "days": {
            DAY.isoformat(): {
                "energy": 1800,
                "protein": 120,
                "carb": 180,
                "fat": 60,
                "activity_energy": 250,
            },
        },
        "weight": {DAY.isoformat(): 75},
    }


def _sync_configured_range(connection_id, *, user_id=None):
    if user_id is None:
        with SessionLocal() as lookup_db:
            user_id = lookup_db.scalar(
                select(YazioConnection.user_id).where(YazioConnection.id == connection_id)
            )
        assert user_id is not None
    with (
        yazio_sync.yazio_operation_slot(user_id),
        SessionLocal() as operation_db,
        yazio_sync._active_yazio_user_operation(operation_db, user_id),
    ):
        return yazio_sync._sync_yazio_connection_range_locked(
            connection_id, start_day=DAY, end_day=DAY,
        )


def _connection_sync_state(db, connection_id):
    db.expire_all()
    connection = db.get(YazioConnection, connection_id)
    return (
        connection.last_attempt_at,
        connection.last_success_at,
        connection.last_error,
        connection.next_sync_at,
        connection.last_micronutrient_sync_at,
        connection.historical_sync_state,
        connection.historical_sync_cursor_date,
    )


def test_configured_range_commits_both_stores_before_projection(db, user, monkeypatch):
    connection = _connection(db, user)
    connection.source_identifier = "configured-source-not-derived-from-user"
    connection.last_error = "prior error stays until finalization"
    db.commit()
    before = _connection_sync_state(db, connection.id)
    _configure_sdk_transport(monkeypatch, [])
    fetched = []
    projected = []
    original_rebuild = yazio_sync.rebuild_yazio_projection_run

    def fetch(email, password, start_day, end_day):
        fetched.append((email, password, start_day, end_day))
        return _range_aggregate(), _range_diary()

    def rebuild(**kwargs):
        assert completions == ["import.started"]
        with SessionLocal() as fresh:
            run = fresh.get(NutritionIngestionRun, kwargs["ingestion_run_id"])
            assert run is not None
            assert run.projection_pending is True
            assert run.user_id == user.id
            assert run.source_instance_id == connection.id
            assert run.connector_variant == "sdk-v22"
            assert (run.requested_start_date, run.requested_end_date) == (DAY, DAY)
            assert fresh.scalar(select(func.count()).select_from(HealthSample)) == 6
            assert fresh.scalar(select(func.count()).select_from(ImportBatch)) == 1
            observations = fresh.scalars(select(NutritionSourceObservation)).all()
            assert len(observations) == 4
            assert {item.source_instance_id for item in observations} == {connection.id}
            assert {item.source_namespace for item in observations} == {
                "yazio.consumed_item", "yazio.simple_product",
                "yazio.product", "yazio.daily_summary",
            }
            events = fresh.scalars(select(NutritionConsumptionEvent)).all()
            assert {event.daytime for event in events} == {"lunch", "dinner"}
            assert fresh.scalar(select(func.count()).select_from(NutritionFoodProfile)) == 1
            assert fresh.scalar(select(func.count()).select_from(NutritionFoodSnapshot)) == 1
        assert kwargs["policy_at"].tzinfo is UTC
        projected.append(kwargs["ingestion_run_id"])
        return original_rebuild(**kwargs)

    completions = []
    monkeypatch.setattr(yazio_sync, "fetch_yazio_domain_transport", fetch)
    monkeypatch.setattr(yazio_sync, "rebuild_yazio_projection_run", rebuild)
    monkeypatch.setattr(
        yazio_sync, "log_security_event",
        lambda event, **kwargs: completions.append(event),
    )
    result = _sync_configured_range(connection.id)
    db.expire_all()
    assert fetched == [("owner@example.com", "yazio-password", DAY, DAY)]
    assert result.summary.inserted == 6
    assert result.user_id == user.id
    assert result.source_instance_id == connection.id
    assert projected == [result.ingestion_run_id]
    assert db.get(NutritionIngestionRun, result.ingestion_run_id).projection_pending is True
    assert _connection_sync_state(db, connection.id) == before
    assert completions == ["import.started"]
    assert not hasattr(result, "__dict__")
    with pytest.raises(FrozenInstanceError):
        result.user_id = uuid4()

    samples = db.scalars(select(HealthSample)).all()
    assert {sample.metric_type: sample.value for sample in samples} == {
        "dietary_energy_kcal": Decimal("1800"),
        "protein_g": Decimal("120"),
        "carbohydrates_g": Decimal("180"),
        "fat_g": Decimal("60"),
        "active_energy_kcal": Decimal("250"),
        "weight_kg": Decimal("75"),
    }
    assert {sample.source_type for sample in samples} == {"yazio_export_v1"}
    assert {sample.source_identifier for sample in samples} == {connection.source_identifier}
    assert {sample.external_sample_id for sample in samples} == {
        "2026-09-01:dietary_energy_kcal", "2026-09-01:protein_g",
        "2026-09-01:carbohydrates_g", "2026-09-01:fat_g",
        "2026-09-01:active_energy_kcal", "2026-09-01:weight_kg",
    }
    batch = db.get(ImportBatch, result.summary.batch_id)
    assert (batch.source_type, batch.client_identifier, batch.connector_variant) == (
        "yazio_export_v1", "yazio-sdk", "sdk-v22",
    )
    assert db.scalar(
        select(func.count()).select_from(NutritionFieldObservation).where(
            NutritionFieldObservation.metric_key == "vitamin_b6_mg",
        )
    ) > 0
    projection = db.scalar(select(NutritionDailyProjection))
    assert projection is not None
    assert projection.projection_version == 1
    assert len(_yazio_policy_snapshot(db, user)) == 1


def test_configured_range_ingestion_failure_rolls_back_flushed_compatibility(db, user, monkeypatch):
    connection = _connection(db, user)
    _configure_sdk_transport(monkeypatch, [], _range_diary())
    before = _connection_sync_state(db, connection.id)
    original_ingest = yazio_sync.ingest_yazio_food_diary

    def fail_after_ingestion(session, **kwargs):
        assert session.scalar(select(func.count()).select_from(HealthSample)) == 2
        assert session.scalar(select(func.count()).select_from(ImportBatch)) == 1
        original_ingest(session, **kwargs)
        raise RuntimeError("domain persistence failed")

    def provider_failure():
        pytest.fail("domain persistence is not a provider failure")

    monkeypatch.setattr(yazio_sync, "ingest_yazio_food_diary", fail_after_ingestion)
    monkeypatch.setattr(yazio_sync, "_record_yazio_provider_failure", provider_failure)
    with pytest.raises(RuntimeError, match="domain persistence failed"):
        _sync_configured_range(connection.id)
    db.expire_all()
    for model in (
        HealthSample, ImportBatch, NutritionIngestionRun, NutritionSourceObservation,
        NutritionConsumptionEvent, NutritionFoodProfile, NutritionFoodSnapshot,
    ):
        assert db.scalar(select(func.count()).select_from(model)) == 0
    assert _connection_sync_state(db, connection.id) == before


def test_configured_range_projection_failure_keeps_evidence_pending(db, user, monkeypatch):
    connection = _connection(db, user)
    _configure_sdk_transport(monkeypatch, [], _range_diary())
    before = _connection_sync_state(db, connection.id)
    events = []
    monkeypatch.setattr(
        yazio_sync, "log_security_event",
        lambda event, **kwargs: events.append(event),
    )

    def fail_projection(**kwargs):
        with SessionLocal() as fresh:
            assert fresh.get(NutritionIngestionRun, kwargs["ingestion_run_id"]).projection_pending
        raise RuntimeError("projection persistence failed")

    monkeypatch.setattr(yazio_sync, "rebuild_yazio_projection_run", fail_projection)
    monkeypatch.setattr(
        yazio_sync, "_record_yazio_provider_failure",
        lambda: pytest.fail("projection failure is not a provider failure"),
    )
    with pytest.raises(RuntimeError, match="projection persistence failed"):
        _sync_configured_range(connection.id)
    assert events == ["import.started"]
    db.expire_all()
    run = db.scalar(select(NutritionIngestionRun))
    assert run.projection_pending is True
    assert db.scalar(select(func.count()).select_from(HealthSample)) == 2
    assert db.scalar(select(func.count()).select_from(ImportBatch)) == 1
    assert db.scalar(select(func.count()).select_from(NutritionSourceObservation)) == 4
    assert db.scalar(select(func.count()).select_from(NutritionConsumptionEvent)) == 2
    assert _connection_sync_state(db, connection.id) == before


def test_configured_range_retry_recovers_pending_before_fetch_without_duplicates(db, user, monkeypatch):
    connection = _connection(db, user)
    _configure_sdk_transport(monkeypatch, [], _range_diary())
    original_day = projection_lifecycle.rebuild_nutrition_day

    def fail_day(*args, **kwargs):
        raise RuntimeError("interrupted projection")

    monkeypatch.setattr(projection_lifecycle, "rebuild_nutrition_day", fail_day)
    with pytest.raises(
        NutritionProjectionLifecycleError,
        match=r"nutrition projection lifecycle rebuild failed for ingestion run .* on 2026-09-01",
    ):
        _sync_configured_range(connection.id)
    db.expire_all()
    old_run_id = db.scalar(select(NutritionIngestionRun.id))
    old_observation_ids = set(db.scalars(select(NutritionSourceObservation.id)))
    old_event_ids = set(db.scalars(select(NutritionConsumptionEvent.id)))
    old_sample_ids = set(db.scalars(select(HealthSample.id)))
    assert db.get(NutritionIngestionRun, old_run_id).projection_pending
    monkeypatch.setattr(projection_lifecycle, "rebuild_nutrition_day", original_day)
    fetched = []

    def fetch(*args):
        with SessionLocal() as fresh:
            assert fresh.get(NutritionIngestionRun, old_run_id).projection_pending is False
            projection = fresh.scalar(select(NutritionDailyProjection))
            assert projection is not None
            assert projection.projection_version == 1
        fetched.append(args)
        return _aggregate(), _range_diary()

    monkeypatch.setattr(yazio_sync, "fetch_yazio_domain_transport", fetch)
    retry = _sync_configured_range(connection.id)
    db.expire_all()
    assert len(fetched) == 1
    assert retry.ingestion_run_id != old_run_id
    assert retry.summary.inserted == 0
    assert retry.summary.skipped == 2
    assert db.get(NutritionIngestionRun, old_run_id).projection_pending is False
    assert db.get(NutritionIngestionRun, retry.ingestion_run_id).projection_pending is True
    assert set(db.scalars(select(NutritionSourceObservation.id))) == old_observation_ids
    assert set(db.scalars(select(NutritionConsumptionEvent.id))) == old_event_ids
    assert set(db.scalars(select(HealthSample.id))) == old_sample_ids
    assert list(db.scalars(select(NutritionDailyProjection.projection_version))) == [1]


def test_configured_range_failed_pending_recovery_stops_before_fetch(db, user, monkeypatch):
    connection = _connection(db, user)
    _configure_sdk_transport(monkeypatch, [])

    def fail_day(*args, **kwargs):
        raise RuntimeError("projection remains unavailable")

    monkeypatch.setattr(projection_lifecycle, "rebuild_nutrition_day", fail_day)
    with pytest.raises(
        NutritionProjectionLifecycleError,
        match=r"nutrition projection lifecycle rebuild failed for ingestion run .* on 2026-09-01",
    ):
        _sync_configured_range(connection.id)
    monkeypatch.setattr(
        yazio_sync, "fetch_yazio_domain_transport",
        lambda *args: pytest.fail("pending recovery must finish before fetching"),
    )
    with pytest.raises(
        NutritionProjectionLifecycleError,
        match=r"nutrition projection lifecycle rebuild failed for ingestion run .* on 2026-09-01",
    ):
        _sync_configured_range(connection.id)
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(NutritionIngestionRun)) == 1
    assert db.scalar(select(NutritionIngestionRun.projection_pending)) is True


def test_configured_range_preserves_higher_priority_other_provider(db, user, monkeypatch):
    connection = _connection(db, user)
    policy = create_policy_with_rules(
        db, user.id, 1, datetime(2026, 9, 1, tzinfo=UTC),
        (
            PriorityRuleSpec(
                data_area="nutrition", metric_key=None,
                provider_key="google_health", priority_rank=1,
            ),
            PriorityRuleSpec(
                data_area="nutrition", metric_key=None,
                provider_key="yazio", priority_rank=2,
            ),
        ),
    )
    db.commit()
    before = _yazio_policy_snapshot(db, user)
    _configure_sdk_transport(monkeypatch, [], _range_diary())
    result = _sync_configured_range(connection.id)
    db.expire_all()
    assert _yazio_policy_snapshot(db, user) == before
    assert list(db.execute(
        select(SourcePriorityRule.provider_key, SourcePriorityRule.priority_rank)
        .where(SourcePriorityRule.policy_id == policy.policy_id)
        .order_by(SourcePriorityRule.priority_rank)
    )) == [("google_health", 1), ("yazio", 2)]
    assert db.scalar(select(func.count()).select_from(NutritionConsumptionEvent)) == 2
    assert db.get(NutritionIngestionRun, result.ingestion_run_id).projection_pending
    projection = db.scalar(select(NutritionDailyProjection))
    assert projection is not None
    assert projection.priority_policy_id == policy.policy_id


def test_configured_range_multi_provider_without_policy_requires_selection(db, user, monkeypatch):
    connection = _connection(db, user)
    db.add(
        GoogleHealthConnection(
            client_id="configured-range-google-client",
            encrypted_client_secret=encrypt_credential("synthetic-client-secret"),
            user_id=user.id,
            encrypted_refresh_token=b"synthetic-refresh-token",
            granted_scopes=["https://www.googleapis.com/auth/googlehealth.nutrition.readonly"],
            state="active",
        )
    )
    db.commit()
    _configure_sdk_transport(monkeypatch, [])
    result = _sync_configured_range(connection.id)
    assert _yazio_policy_snapshot(db, user) == ()
    run = db.get(NutritionIngestionRun, result.ingestion_run_id)
    assert run is not None and run.projection_pending
    observations = list(db.scalars(select(NutritionSourceObservation).where(
        NutritionSourceObservation.ingestion_run_id == run.id,
    )))
    assert observations
    assert {item.source_instance_id for item in observations} == {connection.id}
    assert db.scalar(select(func.count()).select_from(NutritionDailyProjection)) == 0

@pytest.mark.parametrize("invalid_bound", ("start", "end"))
def test_configured_range_rejects_diary_bound_mismatch_atomically(db, user, monkeypatch, invalid_bound):
    connection = _connection(db, user)
    diary = replace(
        _diary(),
        **{
            f"requested_{invalid_bound}_day": DAY + (
                timedelta(days=-1) if invalid_bound == "start" else timedelta(days=1)
            ),
        },
    )
    _configure_sdk_transport(monkeypatch, [], diary)
    with pytest.raises(ValueError, match="diary range must match requested range"):
        _sync_configured_range(connection.id)
    for model in (HealthSample, ImportBatch, NutritionIngestionRun):
        assert db.scalar(select(func.count()).select_from(model)) == 0


def test_configured_range_rejects_fully_failed_aggregate_without_domain_writes(db, user, monkeypatch):
    connection = _connection(db, user)
    _configure_sdk_transport(monkeypatch, [])
    monkeypatch.setattr(
        yazio_sync, "fetch_yazio_domain_transport",
        lambda *args: ({DAY.isoformat(): {"error": "unavailable"}}, _diary()),
    )
    with pytest.raises(YazioSyncError, match="konnten nicht verarbeitet"):
        _sync_configured_range(connection.id)
    for model in (HealthSample, ImportBatch, NutritionIngestionRun):
        assert db.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.parametrize("invalid_connection", ("missing", "inactive", "credentials"))
def test_configured_range_requires_real_active_configured_connection(
    db, user, monkeypatch, invalid_connection,
):
    connection = _connection(db, user)
    connection_id = connection.id
    if invalid_connection == "missing":
        connection_id = uuid4()
    elif invalid_connection == "inactive":
        user.is_active = False
        user.deactivated_at = datetime(2026, 8, 10, tzinfo=UTC)
    else:
        connection.encrypted_password = b"not-a-valid-encrypted-credential"
    db.commit()
    _configure_sdk_transport(monkeypatch, [])
    monkeypatch.setattr(
        yazio_sync, "fetch_yazio_domain_transport",
        lambda *args: pytest.fail("invalid connection must not reach provider"),
    )
    with pytest.raises(YazioSyncError):
        _sync_configured_range(connection_id, user_id=user.id)
    assert db.scalar(select(func.count()).select_from(ImportBatch)) == 0
    assert db.scalar(select(func.count()).select_from(NutritionIngestionRun)) == 0


@pytest.mark.parametrize("changed", ("inactive", "removed", "source", "credentials"))
def test_configured_range_revalidates_connection_after_fetch(db, user, monkeypatch, changed):
    connection = _connection(db, user)
    _configure_sdk_transport(monkeypatch, [])

    def fetch(*args):
        with SessionLocal() as other:
            if changed == "inactive":
                other.get(User, user.id).is_active = False
                other.get(User, user.id).deactivated_at = datetime(2026, 8, 10, tzinfo=UTC)
            elif changed == "removed":
                other.delete(other.get(YazioConnection, connection.id))
            elif changed == "source":
                other.get(YazioConnection, connection.id).source_identifier = "changed-source"
            else:
                other.get(YazioConnection, connection.id).encrypted_password = encrypt_credential(
                    "changed-password",
                )
            other.commit()
        return _aggregate(), _diary()

    monkeypatch.setattr(yazio_sync, "fetch_yazio_domain_transport", fetch)
    with pytest.raises(YazioSyncError):
        _sync_configured_range(connection.id)
    assert db.scalar(select(func.count()).select_from(HealthSample)) == 0
    assert db.scalar(select(func.count()).select_from(NutritionIngestionRun)) == 0


@pytest.mark.parametrize(
    ("provider_error", "sync_error", "failure_count"),
    (
        (YazioProviderAuthenticationError, yazio_sync.YazioAuthenticationError, 0),
        (YazioProviderNetworkTimeoutError, yazio_sync.YazioNetworkTimeoutError, 1),
    ),
)
def test_configured_range_classifies_only_provider_fetch_failures(
    db, user, monkeypatch, provider_error, sync_error, failure_count,
):
    connection = _connection(db, user)
    _configure_sdk_transport(monkeypatch, [])
    failures = []

    def fail_fetch(*args):
        raise provider_error()

    monkeypatch.setattr(yazio_sync, "fetch_yazio_domain_transport", fail_fetch)
    monkeypatch.setattr(yazio_sync, "_record_yazio_provider_failure", lambda: failures.append(True))
    with pytest.raises(sync_error):
        _sync_configured_range(connection.id)
    assert len(failures) == failure_count
    assert db.scalar(select(func.count()).select_from(ImportBatch)) == 0
    assert db.scalar(select(func.count()).select_from(NutritionIngestionRun)) == 0


@pytest.mark.parametrize(
    ("setting_name", "setting_value", "error_type"),
    (
        ("yazio_enabled", False, yazio_sync.YazioDisabled),
        ("yazio_sdk_client_secret", "", yazio_sync.YazioSdkNotConfigured),
    ),
)
def test_configured_range_keeps_current_sdk_guards(
    db, user, monkeypatch, setting_name, setting_value, error_type,
):
    connection = _connection(db, user)
    _configure_sdk_transport(monkeypatch, [])
    monkeypatch.setattr(settings, setting_name, setting_value)
    monkeypatch.setattr(
        yazio_sync, "fetch_yazio_domain_transport",
        lambda *args: pytest.fail("disabled or unconfigured SDK must not fetch"),
    )
    with pytest.raises(error_type):
        _sync_configured_range(connection.id)
    assert db.scalar(select(func.count()).select_from(NutritionIngestionRun)) == 0


def test_configured_range_respects_open_provider_circuit(db, user, monkeypatch):
    connection = _connection(db, user)
    _configure_sdk_transport(monkeypatch, [])

    def open_circuit():
        raise yazio_sync.YazioCircuitOpen(60)

    monkeypatch.setattr(yazio_sync, "_ensure_yazio_circuit_closed", open_circuit)
    monkeypatch.setattr(
        yazio_sync, "fetch_yazio_domain_transport",
        lambda *args: pytest.fail("open circuit must prevent fetching"),
    )
    with pytest.raises(yazio_sync.YazioCircuitOpen) as failure:
        _sync_configured_range(connection.id)
    assert failure.value.retry_after == 60
    assert db.scalar(select(func.count()).select_from(NutritionIngestionRun)) == 0


@pytest.mark.parametrize("caller", ("service", "api", "scheduled", "explicit"))
def test_manual_and_scheduled_share_configured_sdk_range_persistence(
    db, user, client, monkeypatch, caller,
):
    connection = _connection(db, user)
    old_micronutrient_timestamp = datetime(2025, 1, 1, 12, tzinfo=UTC)
    connection.last_micronutrient_sync_at = old_micronutrient_timestamp
    connection.source_identifier = "configured-source-identity"
    db.commit()
    _configure_sdk_transport(monkeypatch, [])
    attempted_at = datetime(2026, 9, 1, 12, tzinfo=UTC)
    events = []

    def record_event(event, **kwargs):
        events.append((event, kwargs))
        if event in {"import.started", "import.completed"}:
            with SessionLocal() as fresh:
                batch = fresh.scalar(select(ImportBatch))
                assert batch is not None
                assert kwargs["target_ref"] == security_reference("import_batch", batch.id)
                run = fresh.scalar(select(NutritionIngestionRun))
                assert run is not None
                stored_connection = fresh.get(YazioConnection, connection.id)
                if event == "import.started":
                    assert run.projection_pending is True
                    assert stored_connection.last_success_at is None
                    assert fresh.scalar(select(NutritionDailyProjection)) is None
                else:
                    assert run.projection_pending is False
                    assert stored_connection.last_success_at is not None
                    assert fresh.scalar(select(NutritionDailyProjection)) is not None

    monkeypatch.setattr(yazio_sync, "log_security_event", record_event)

    def fetch(email, password, start_day, end_day):
        assert (email, password, start_day, end_day) == (
            "owner@example.com", "yazio-password", DAY, DAY,
        )
        return _range_aggregate(), _range_diary()

    monkeypatch.setattr(yazio_sync, "fetch_yazio_domain_transport", fetch)
    if caller == "api":
        from app.api import yazio as yazio_api

        original = yazio_sync.run_manual_yazio_sync
        monkeypatch.setattr(
            yazio_api, "run_manual_yazio_sync",
            lambda user_id, **kwargs: original(user_id, now=attempted_at, **kwargs),
        )
        login = client.post("/api/v1/auth/login", json={
            "username": user.username, "password": "correct-horse-battery-staple",
        })
        assert login.status_code == 200
        response = client.post(
            "/api/v1/yazio/sync?days=1",
            headers={"X-CSRF-Token": login.json()["csrf_token"]},
        )
        assert response.status_code == 200
        summary = ImportSummary.model_validate(response.json())
    elif caller == "scheduled":
        summary = yazio_sync.run_scheduled_yazio_sync(connection.id, now=attempted_at)
    elif caller == "explicit":
        summary = run_manual_yazio_sync(
            user.id, start_day=DAY, end_day=DAY, now=attempted_at,
        )
    else:
        summary = run_manual_yazio_sync(user.id, sync_days=1, now=attempted_at)

    assert summary.status == "completed"
    assert summary.inserted == 6
    db.expire_all()
    stored = db.get(YazioConnection, connection.id)
    assert stored.last_attempt_at.replace(tzinfo=UTC) == attempted_at
    assert stored.last_success_at is not None and stored.last_error is None
    assert stored.next_sync_at is not None
    assert stored.last_micronutrient_sync_at.replace(tzinfo=UTC) == old_micronutrient_timestamp
    current_run = db.scalar(select(NutritionIngestionRun))
    assert current_run.projection_pending is False
    assert current_run.source_instance_id == connection.id
    assert [event for event, _kwargs in events] == [
        "import.started", "import.completed", "integration.yazio.sync_completed",
    ]
    assert events[0][1] == {
        "actor_ref": security_reference("user", user.id),
        "target_ref": security_reference("import_batch", summary.batch_id),
        "details": {"source_type": "yazio_export_v1"},
    }
    assert events[1][1] == {
        "actor_ref": security_reference("user", user.id),
        "target_ref": security_reference("import_batch", summary.batch_id),
        "details": {
            "source_type": "yazio_export_v1",
            "received": 6,
            "inserted": 6,
            "updated": 0,
            "skipped": 0,
            "failed": 0,
        },
    }
    assert (
        summary.received, summary.inserted, summary.updated, summary.skipped, summary.failed,
    ) == (6, 6, 0, 0, 0)
    samples = db.scalars(select(HealthSample)).all()
    assert len(samples) == 6
    assert {sample.user_id for sample in samples} == {user.id}
    assert {sample.source_identifier for sample in samples} == {"configured-source-identity"}
    assert {sample.external_sample_id for sample in samples} == {
        "2026-09-01:dietary_energy_kcal", "2026-09-01:protein_g",
        "2026-09-01:carbohydrates_g", "2026-09-01:fat_g",
        "2026-09-01:active_energy_kcal", "2026-09-01:weight_kg",
    }
    batch = db.get(ImportBatch, summary.batch_id)
    assert (batch.source_type, batch.client_identifier, batch.connector_variant) == (
        "yazio_export_v1", "yazio-sdk", "sdk-v22",
    )
    observations = db.scalars(select(NutritionSourceObservation)).all()
    assert len(observations) == 4
    assert {item.source_instance_id for item in observations} == {connection.id}
    assert {item.source_namespace for item in observations} == {
        "yazio.consumed_item", "yazio.simple_product",
        "yazio.product", "yazio.daily_summary",
    }
    assert {event.daytime for event in db.scalars(select(NutritionConsumptionEvent))} == {
        "lunch", "dinner",
    }
    assert db.scalar(select(func.count()).select_from(NutritionFieldObservation)) > 0
    assert list(db.scalars(select(NutritionDailyProjection.projection_version))) == [1]


@pytest.mark.parametrize("kwargs", (
    {"start_day": DAY}, {"end_day": DAY},
    {"start_day": DAY, "end_day": DAY - timedelta(days=1)},
    {"start_day": DAY - timedelta(days=366), "end_day": DAY},
    {"start_day": DAY, "end_day": DAY, "sync_days": 1},
))
def test_manual_explicit_range_rejects_invalid_arguments(user, kwargs):
    with pytest.raises(ValueError):
        run_manual_yazio_sync(user.id, **kwargs)


def _history_connection(db, user):
    connection = _connection(db, user)
    connection.historical_sync_state = "pending"
    connection.historical_sync_start_date = DAY - timedelta(days=400)
    connection.historical_sync_end_date = DAY
    connection.historical_sync_cursor_date = DAY
    db.commit()
    return connection


def _history_identity(connection):
    return (
        connection.historical_sync_start_date, connection.historical_sync_end_date,
        connection.historical_sync_cursor_date, connection.historical_sync_started_at,
    )


def _matching_range_transport(_email, _password, start_day, end_day):
    diary = _range_diary()
    return {"days": {end_day.isoformat(): {"energy": 1800}}}, replace(
        diary, requested_start_day=start_day, requested_end_day=end_day,
        consumed_products=tuple(
            replace(
                item, consumed_item_id=f"product-{end_day}",
                local_date=end_day,
                provider_civil_datetime=datetime(end_day.year, end_day.month, end_day.day, 12),
            )
            for item in diary.consumed_products
        ),
        consumed_simple_products=tuple(
            replace(
                item, consumed_item_id=f"simple-{end_day}",
                local_date=end_day,
                provider_civil_datetime=datetime(end_day.year, end_day.month, end_day.day, 18),
            )
            for item in diary.consumed_simple_products
        ),
        daily_summaries=tuple(
            replace(item, local_date=end_day) for item in diary.daily_summaries
        ),
    )


def test_historical_chunks_share_canonical_evidence_and_advance_after_projection(
    db, user, monkeypatch,
):
    connection = _history_connection(db, user)
    _configure_sdk_transport(monkeypatch, [])
    ranges = []
    cursors = []
    original = yazio_sync.rebuild_yazio_projection_run

    def fetch(email, password, start_day, end_day):
        ranges.append((start_day, end_day))
        return _matching_range_transport(email, password, start_day, end_day)

    def rebuild(**kwargs):
        with SessionLocal() as fresh:
            cursors.append(fresh.get(YazioConnection, connection.id).historical_sync_cursor_date)
        return original(**kwargs)

    monkeypatch.setattr(yazio_sync, "fetch_yazio_domain_transport", fetch)
    monkeypatch.setattr(yazio_sync, "rebuild_yazio_projection_run", rebuild)
    assert yazio_sync.run_scheduled_yazio_sync(connection.id) is not None
    assert yazio_sync.run_scheduled_yazio_sync(connection.id) is not None
    assert ranges == [
        (date(2025, 9, 1), DAY), (date(2025, 7, 28), date(2025, 8, 31)),
    ]
    assert cursors == [DAY, date(2025, 8, 31)]
    db.expire_all()
    assert connection.historical_sync_state == "completed"
    assert connection.historical_sync_cursor_date is None
    runs = db.scalars(select(NutritionIngestionRun)).all()
    assert len(runs) == 2 and all(not run.projection_pending for run in runs)
    assert db.scalar(
        select(func.count()).select_from(NutritionSourceObservation).where(
            NutritionSourceObservation.source_namespace == "yazio.daily_summary",
        )
    ) == 2
    events = db.scalars(select(NutritionConsumptionEvent)).all()
    assert len(events) == 4
    assert {event.daytime for event in events} == {"lunch", "dinner"}
    assert {event.local_date for event in events} == {DAY, date(2025, 8, 31)}
    assert db.scalar(select(func.count()).select_from(NutritionDailyProjection)) == 2


def test_historical_projection_failure_retains_range_cursor_and_pending(db, user, monkeypatch):
    connection = _history_connection(db, user)
    original_identity = _history_identity(connection)[:3]
    _configure_sdk_transport(monkeypatch, [])
    ranges = []
    original = yazio_sync.rebuild_yazio_projection_run

    def fetch(email, password, start_day, end_day):
        ranges.append((start_day, end_day))
        return _matching_range_transport(email, password, start_day, end_day)

    def fail(**kwargs):
        raise RuntimeError("synthetic projection failure")

    monkeypatch.setattr(yazio_sync, "fetch_yazio_domain_transport", fetch)
    monkeypatch.setattr(yazio_sync, "rebuild_yazio_projection_run", fail)
    with pytest.raises(RuntimeError, match="projection failure"):
        yazio_sync.run_scheduled_yazio_sync(connection.id)
    db.expire_all()
    assert _history_identity(connection)[:3] == original_identity
    assert connection.historical_sync_state == "failed"
    assert connection.historical_sync_last_error is not None
    failed_run = db.scalar(select(NutritionIngestionRun))
    assert failed_run.projection_pending is True
    monkeypatch.setattr(yazio_sync, "rebuild_yazio_projection_run", original)
    assert yazio_sync.run_scheduled_yazio_sync(connection.id) is not None
    assert ranges == [(date(2025, 9, 1), DAY)] * 2
    db.expire_all()
    assert connection.historical_sync_cursor_date == date(2025, 8, 31)
    assert all(not run.projection_pending for run in db.scalars(select(NutritionIngestionRun)))


@pytest.mark.parametrize("replaced", ("range", "cursor", "started", "state"))
def test_history_finalization_rejects_replaced_job(db, user, monkeypatch, replaced):
    connection = _history_connection(db, user)
    _configure_sdk_transport(monkeypatch, [])
    original = yazio_sync.rebuild_yazio_projection_run
    replacement = []

    def rebuild(**kwargs):
        result = original(**kwargs)
        with SessionLocal() as fresh:
            stored = fresh.get(YazioConnection, connection.id)
            if replaced == "range":
                stored.historical_sync_start_date = DAY - timedelta(days=10)
            elif replaced == "cursor":
                stored.historical_sync_cursor_date = DAY - timedelta(days=1)
            elif replaced == "started":
                stored.historical_sync_started_at = datetime(2026, 1, 1, tzinfo=UTC)
            else:
                stored.historical_sync_state = "pending"
            fresh.commit()
            fresh.refresh(stored)
            replacement.append((_history_identity(stored), stored.historical_sync_state))
        return result

    monkeypatch.setattr(yazio_sync, "fetch_yazio_domain_transport", _matching_range_transport)
    monkeypatch.setattr(yazio_sync, "rebuild_yazio_projection_run", rebuild)
    with pytest.raises(YazioSyncError):
        yazio_sync.run_scheduled_yazio_sync(connection.id)
    db.expire_all()
    assert (_history_identity(connection), connection.historical_sync_state) == replacement[0]
    assert connection.historical_sync_last_error is None
    assert connection.last_success_at is None
    assert db.scalar(select(NutritionIngestionRun.projection_pending)) is True


def test_recovery_alone_never_advances_connection_success_or_history(db, user, monkeypatch):
    connection = _history_connection(db, user)
    connection.last_success_at = datetime(2025, 1, 1, tzinfo=UTC)
    db.commit()
    _configure_sdk_transport(monkeypatch, [])
    monkeypatch.setattr(yazio_sync, "fetch_yazio_domain_transport", _matching_range_transport)
    original = yazio_sync.rebuild_yazio_projection_run
    monkeypatch.setattr(
        yazio_sync, "rebuild_yazio_projection_run",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("projection failed")),
    )
    with pytest.raises(RuntimeError):
        yazio_sync.run_scheduled_yazio_sync(connection.id)
    db.expire_all()
    identity_before = _history_identity(connection)
    last_success_before = connection.last_success_at
    old_run = db.scalar(select(NutritionIngestionRun))
    monkeypatch.setattr(yazio_sync, "rebuild_yazio_projection_run", original)

    def fail_fetch(*args):
        raise YazioProviderNetworkTimeoutError()

    monkeypatch.setattr(yazio_sync, "fetch_yazio_domain_transport", fail_fetch)
    assert yazio_sync.run_scheduled_yazio_sync(connection.id) is None
    db.expire_all()
    assert old_run.projection_pending is False
    assert connection.last_success_at == last_success_before
    assert _history_identity(connection) == identity_before
    assert connection.historical_sync_state == "failed"


@pytest.mark.parametrize("mode", ("manual", "scheduled", "history"))
def test_final_status_failure_is_not_success(db, user, monkeypatch, mode):
    from sqlalchemy.orm import Session

    connection = _history_connection(db, user) if mode == "history" else _connection(db, user)
    last_success_before = datetime(2025, 1, 1, tzinfo=UTC)
    connection.last_success_at = last_success_before
    db.commit()
    _configure_sdk_transport(monkeypatch, [])
    monkeypatch.setattr(yazio_sync, "fetch_yazio_domain_transport", _matching_range_transport)
    cursor_before = connection.historical_sync_cursor_date
    attempted_at = datetime(2026, 9, 1, 12, tzinfo=UTC)
    original_commit = Session.commit
    final_sessions = []
    failure_sessions = []
    events = []

    def commit(session):
        final = any(
            isinstance(row, NutritionIngestionRun) and not row.projection_pending
            for row in session.dirty
        ) and any(
            isinstance(row, YazioConnection) and row.last_success_at is not None
            for row in session.dirty
        )
        if final:
            final_sessions.append(session)
            raise RuntimeError("definitely aborted final commit")
        if any(isinstance(row, YazioConnection) and row.last_error for row in session.dirty):
            failure_sessions.append(session)
        return original_commit(session)

    monkeypatch.setattr(Session, "commit", commit)
    monkeypatch.setattr(yazio_sync, "log_security_event", lambda event, **kwargs: events.append(event))
    with pytest.raises(RuntimeError, match="aborted final commit"):
        if mode == "manual":
            run_manual_yazio_sync(user.id, now=attempted_at)
        else:
            yazio_sync.run_scheduled_yazio_sync(connection.id, now=attempted_at)
    db.expire_all()
    assert events.count("import.started") == 1
    assert events.count("import.completed") == 0
    assert events.count("integration.yazio.sync_completed") == 0
    assert db.scalar(select(NutritionIngestionRun.projection_pending)) is True
    assert connection.last_success_at.replace(tzinfo=UTC) == last_success_before
    assert connection.historical_sync_cursor_date == cursor_before
    assert connection.last_attempt_at.replace(tzinfo=UTC) == attempted_at
    assert connection.last_error is not None and connection.next_sync_at is not None
    assert len(final_sessions) == len(failure_sessions) == 1
    assert final_sessions[0] is not failure_sessions[0]


def test_failure_recording_error_propagates(db, user, monkeypatch):
    from sqlalchemy.orm import Session

    connection = _connection(db, user)
    _configure_sdk_transport(monkeypatch, [])

    def fail_fetch(*args):
        raise YazioProviderNetworkTimeoutError()

    original = Session.commit

    def commit(session):
        if any(isinstance(row, YazioConnection) and row.last_error for row in session.dirty):
            raise RuntimeError("failure recorder commit failed")
        return original(session)

    monkeypatch.setattr(yazio_sync, "fetch_yazio_domain_transport", fail_fetch)
    monkeypatch.setattr(Session, "commit", commit)
    with pytest.raises(RuntimeError, match="failure recorder commit failed"):
        yazio_sync.run_scheduled_yazio_sync(connection.id)


def test_lost_commit_acknowledgement_is_reread_on_retry(db, user, monkeypatch):
    from sqlalchemy.orm import Session

    connection = _history_connection(db, user)
    _configure_sdk_transport(monkeypatch, [])
    original = Session.commit
    ranges = []
    lost = False

    def fetch(email, password, start_day, end_day):
        ranges.append((start_day, end_day))
        return _matching_range_transport(email, password, start_day, end_day)

    def commit(session):
        nonlocal lost
        final = any(
            isinstance(row, YazioConnection) and row.last_success_at is not None
            for row in session.dirty
        )
        original(session)
        if final and not lost:
            lost = True
            raise RuntimeError("lost commit acknowledgement")

    monkeypatch.setattr(yazio_sync, "fetch_yazio_domain_transport", fetch)
    monkeypatch.setattr(Session, "commit", commit)
    with pytest.raises(RuntimeError, match="lost commit acknowledgement"):
        yazio_sync.run_scheduled_yazio_sync(connection.id)
    db.expire_all()
    assert db.scalar(select(NutritionIngestionRun.projection_pending)) is False
    assert connection.last_success_at is not None and connection.last_error is None
    assert connection.historical_sync_state == "pending"
    assert connection.historical_sync_cursor_date == date(2025, 8, 31)
    assert yazio_sync.run_scheduled_yazio_sync(connection.id) is not None
    assert ranges == [
        (date(2025, 9, 1), DAY), (date(2025, 7, 28), date(2025, 8, 31)),
    ]


@pytest.mark.parametrize("stage", ("evidence", "bootstrap", "day"))
def test_persistence_and_projection_errors_are_not_provider_failures(db, user, monkeypatch, stage):
    from sqlalchemy.orm import Session

    connection = _connection(db, user)
    _configure_sdk_transport(monkeypatch, [])
    monkeypatch.setattr(yazio_sync, "fetch_yazio_domain_transport", _matching_range_transport)
    circuit_failures = []
    monkeypatch.setattr(yazio_sync, "_record_yazio_provider_failure", lambda: circuit_failures.append(True))
    original = Session.commit

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic storage failure")

    if stage == "evidence":
        def commit(session):
            if any(isinstance(row, NutritionIngestionRun) for row in session.identity_map.values()):
                fail()
            return original(session)
        monkeypatch.setattr(Session, "commit", commit)
    elif stage == "bootstrap":
        monkeypatch.setattr(yazio_projection_retry, "bootstrap_nutrition_priority", fail)
    else:
        monkeypatch.setattr(projection_lifecycle, "rebuild_nutrition_day", fail)
    with pytest.raises((RuntimeError, NutritionProjectionLifecycleError)):
        yazio_sync.run_scheduled_yazio_sync(connection.id)
    db.expire_all()
    assert connection.last_success_at is None and connection.last_error is not None
    assert circuit_failures == []
    if stage == "evidence":
        assert db.scalar(select(func.count()).select_from(NutritionIngestionRun)) == 0
    else:
        assert db.scalar(select(NutritionIngestionRun.projection_pending)) is True


@pytest.mark.parametrize("identity", ("user", "source", "run", "missing_connection"))
def test_final_status_rejects_missing_or_mismatched_identity(db, user, monkeypatch, identity):
    connection = _connection(db, user)
    _configure_sdk_transport(monkeypatch, [])
    connection_id = connection.id
    monkeypatch.setattr(yazio_sync, "fetch_yazio_domain_transport", _matching_range_transport)
    original = yazio_sync._finalize_yazio_connection_sync
    completions = []
    monkeypatch.setattr(
        yazio_sync, "log_security_event",
        lambda event, **kwargs: completions.append(event),
    )

    def finalize(connection_id, *, result, **kwargs):
        if identity == "user":
            result = replace(result, user_id=uuid4())
        elif identity == "source":
            result = replace(result, source_instance_id=uuid4())
        elif identity == "run":
            result = replace(result, ingestion_run_id=uuid4())
        else:
            with SessionLocal() as fresh:
                fresh.delete(fresh.get(YazioConnection, connection_id))
                fresh.commit()
        return original(connection_id, result=result, **kwargs)

    monkeypatch.setattr(yazio_sync, "_finalize_yazio_connection_sync", finalize)
    with pytest.raises((YazioSyncError, NutritionProjectionLifecycleError)):
        run_manual_yazio_sync(user.id, now=datetime(2026, 9, 1, 12, tzinfo=UTC))
    db.expire_all()
    assert db.scalar(select(NutritionIngestionRun.projection_pending)) is True
    stored = db.get(YazioConnection, connection_id)
    if stored is not None:
        assert stored.last_success_at is None
    assert completions.count("integration.yazio.sync_completed") == 0
