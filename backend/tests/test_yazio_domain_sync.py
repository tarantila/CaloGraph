from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select

from app.config import Settings, settings
from app.models import HealthSample, ImportBatch, YazioConnection
from app.nutrition.models import NutritionConsumptionEvent, NutritionIngestionRun
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
