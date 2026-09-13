from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select

from app.config import Settings, settings
from app.database import SessionLocal
from app.models import HealthSample, ImportBatch, YazioConnection
from app.nutrition.models import (
    NutritionConsumptionEvent,
    NutritionIngestionRun,
    NutritionSourceObservation,
)
from app.nutrition.projection import lifecycle as projection_lifecycle
from app.nutrition.projection.contracts import (
    ProjectionPersistenceResult,
    ProjectionPersistenceStatus,
)
from app.nutrition.projection.lifecycle import NutritionProjectionLifecycleResult
from app.schemas import ImportSummary
from app.services import yazio_sync, yazio_transport
from app.services.credential_crypto import encrypt_credential
from app.services.yazio_provider import (
    YazioDailyNutrientSummary,
    YazioFoodDiary,
    YazioNutrientValues,
    YazioProviderInvalidResponseError,
)
from app.services.yazio_sync import YazioSyncError, run_manual_yazio_sync

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


def _enable_sdk_rollout(
    monkeypatch, events: list[str], diary: YazioFoodDiary | None = None
) -> None:
    monkeypatch.setattr(settings, "yazio_enabled", True)
    monkeypatch.setattr(settings, "yazio_provider", "sdk")
    monkeypatch.setattr(settings, "yazio_nutrition_domain_write_enabled", True)

    def fetch_domain(email, password, start_day, end_day):
        del email, password, start_day, end_day
        events.extend(("aggregate", "consumed-items", "daily-summary", "product"))
        return _aggregate(), diary or _diary()

    monkeypatch.setattr(yazio_sync, "fetch_yazio_domain_transport", fetch_domain)


def test_rollout_flag_defaults_false_and_reads_environment(monkeypatch) -> None:
    monkeypatch.delenv("YAZIO_NUTRITION_DOMAIN_WRITE_ENABLED", raising=False)
    disabled = Settings(_env_file=None, environment="development")
    assert disabled.yazio_nutrition_domain_write_enabled is False
    monkeypatch.setenv("YAZIO_NUTRITION_DOMAIN_WRITE_ENABLED", "true")
    enabled = Settings(_env_file=None, environment="development")
    assert enabled.yazio_nutrition_domain_write_enabled is True


def test_enabled_sdk_reads_everything_before_shared_transaction(
    db, user, monkeypatch
) -> None:
    _connection(db, user)
    events: list[str] = []
    _enable_sdk_rollout(monkeypatch, events)
    original = yazio_sync._persist_import_locked

    def mark_transaction(*args, **kwargs):
        events.append("shared-transaction")
        return original(*args, **kwargs)

    monkeypatch.setattr(yazio_sync, "_persist_import_locked", mark_transaction)
    assert run_manual_yazio_sync(user.id, now=datetime(2026, 9, 1, 12, tzinfo=UTC))
    assert events[:4] == ["aggregate", "consumed-items", "daily-summary", "product"]
    assert events.index("shared-transaction") == 4


def test_enabled_rollout_is_sdk_only_and_fails_without_domain_writes(
    db, user, monkeypatch
) -> None:
    _connection(db, user)
    monkeypatch.setattr(settings, "yazio_nutrition_domain_write_enabled", True)
    monkeypatch.setattr(settings, "yazio_provider", "legacy")
    with pytest.raises(YazioSyncError, match="SDK"):
        run_manual_yazio_sync(user.id, now=datetime(2026, 9, 1, 12, tzinfo=UTC))
    assert db.scalar(select(func.count()).select_from(ImportBatch)) == 0
    assert db.scalar(select(func.count()).select_from(NutritionIngestionRun)) == 0


def test_enabled_sync_commits_legacy_and_domain_rows_together(
    db, user, monkeypatch
) -> None:
    _connection(db, user)
    events: list[str] = []
    _enable_sdk_rollout(monkeypatch, events)
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
    _enable_sdk_rollout(monkeypatch, events)
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

    monkeypatch.setattr(yazio_sync, "rebuild_affected_nutrition_days", lifecycle)
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
    _enable_sdk_rollout(monkeypatch, [])
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

    monkeypatch.setattr(yazio_sync, "rebuild_affected_nutrition_days", lifecycle)
    summary = run_manual_yazio_sync(
        user.id, now=datetime(2026, 9, 1, 12, tzinfo=UTC)
    )

    assert isinstance(summary, ImportSummary)
    assert len(lifecycle_results) == 1
    assert lifecycle_results[0].policy_missing_dates == (DAY,)


def test_idempotent_domain_sync_has_no_affected_dates_or_second_b6_rebuild(
    db, user, monkeypatch
) -> None:
    _connection(db, user)
    _enable_sdk_rollout(monkeypatch, [])
    original_lifecycle = yazio_sync.rebuild_affected_nutrition_days
    lifecycle_results: list[NutritionProjectionLifecycleResult] = []
    b6_calls: list[tuple[object, date]] = []

    def rebuild_day(db, *, user_id, local_date, policy_at, **kwargs):
        del db, policy_at, kwargs
        b6_calls.append((user_id, local_date))
        return ProjectionPersistenceResult(
            user_id=user_id,
            local_date=local_date,
            projection_id=None,
            projection_version=None,
            input_watermark=None,
            created=False,
            status=ProjectionPersistenceStatus.POLICY_MISSING,
        )

    def lifecycle(**kwargs):
        result = original_lifecycle(**kwargs)
        lifecycle_results.append(result)
        return result

    monkeypatch.setattr(projection_lifecycle, "rebuild_nutrition_day", rebuild_day)
    monkeypatch.setattr(yazio_sync, "rebuild_affected_nutrition_days", lifecycle)
    first = run_manual_yazio_sync(
        user.id, now=datetime(2026, 9, 1, 12, tzinfo=UTC)
    )
    second = run_manual_yazio_sync(
        user.id, now=datetime(2026, 9, 1, 12, tzinfo=UTC)
    )

    assert isinstance(first, ImportSummary)
    assert isinstance(second, ImportSummary)
    assert len(lifecycle_results) == 2
    assert lifecycle_results[0].affected_dates == (DAY,)
    assert lifecycle_results[0].policy_missing_dates == (DAY,)
    assert lifecycle_results[1].affected_dates == ()
    assert b6_calls == [(user.id, DAY)]


def test_domain_lifecycle_failure_keeps_committed_evidence(
    db, user, monkeypatch
) -> None:
    _connection(db, user)
    _enable_sdk_rollout(monkeypatch, [])

    def fail_lifecycle(**kwargs):
        raise RuntimeError(f"lifecycle failed for {kwargs['ingestion_run_id']}")

    monkeypatch.setattr(yazio_sync, "rebuild_affected_nutrition_days", fail_lifecycle)
    with pytest.raises(YazioSyncError, match="unerwartet"):
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
    _enable_sdk_rollout(monkeypatch, events)

    def fail_ingestion(*args, **kwargs):
        raise ValueError("domain failure")

    monkeypatch.setattr(yazio_sync, "ingest_yazio_food_diary", fail_ingestion)
    with pytest.raises(YazioSyncError):
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



def test_disabled_legacy_path_never_constructs_food_diary_provider(
    db, user, monkeypatch
) -> None:
    _connection(db, user)
    monkeypatch.setattr(settings, "yazio_nutrition_domain_write_enabled", False)
    monkeypatch.setattr(settings, "yazio_provider", "legacy")
    called = False

    lifecycle_called = False

    def forbidden_lifecycle(**kwargs):
        nonlocal lifecycle_called
        lifecycle_called = True
        raise AssertionError("projection lifecycle must not run in legacy mode")

    monkeypatch.setattr(yazio_sync, "rebuild_affected_nutrition_days", forbidden_lifecycle)

    def forbidden_transport(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("domain transport must not run in legacy mode")

    monkeypatch.setattr(yazio_sync, "fetch_yazio_domain_transport", forbidden_transport)
    result = run_manual_yazio_sync(
        user.id,
        fetcher=lambda *_args: _aggregate(),
        now=datetime(2026, 9, 1, 12, tzinfo=UTC),
    )
    assert isinstance(result, ImportSummary)
    assert called is False
    assert lifecycle_called is False
