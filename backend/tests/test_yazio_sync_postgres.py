import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from decimal import Decimal
from threading import Event

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal, engine
from app.models import YazioConnection
from app.nutrition.models import NutritionIngestionRun
from app.services import yazio_projection_retry, yazio_sync
from app.services.credential_crypto import encrypt_credential
from app.services.user_operation_lock import (
    UserOperationBusy,
    exclusive_user_lifecycle_operation,
)
from app.services.yazio_guard import YazioOperationBusy, yazio_operation_slot
from app.services.yazio_provider import (
    YazioDailyNutrientSummary,
    YazioFoodDiary,
    YazioNutrientValues,
    YazioProviderNetworkTimeoutError,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("CALOGRAPH_ALLOW_DESTRUCTIVE_POSTGRES_TESTS") != "1"
    or not os.environ.get("CALOGRAPH_POSTGRES_TEST_URL"),
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)


@pytest.mark.parametrize("phase", ("recovery", "evidence", "projection", "status", "failure"))
def test_connection_lock_covers_recovery_through_status_commit(db, user, monkeypatch, phase):
    assert engine.dialect.name == "postgresql"
    monkeypatch.setattr(settings, "yazio_sdk_client_secret", "synthetic-sdk-secret")
    connection = YazioConnection(
        user_id=user.id, source_identifier=f"yazio:{user.id}",
        encrypted_email=encrypt_credential("owner@example.com"),
        encrypted_password=encrypt_credential("synthetic-password"),
        sync_enabled=True, sync_days=1,
    )
    db.add(connection)
    db.commit()
    connection_id, user_id = connection.id, user.id
    day = date(2026, 9, 1)
    attempted_at = datetime(2026, 9, 1, 12, tzinfo=UTC)

    def fetch(_email, _password, start_day, end_day):
        return {"days": {day.isoformat(): {"energy": 1800}}}, YazioFoodDiary(
            requested_start_day=start_day, requested_end_day=end_day,
            consumed_products=(), consumed_simple_products=(), product_profiles=(),
            daily_summaries=(YazioDailyNutrientSummary(
                local_date=day, nutrients=YazioNutrientValues(energy=Decimal("1800")),
                energy_goal=None,
            ),),
        )

    monkeypatch.setattr(yazio_sync, "fetch_yazio_domain_transport", fetch)
    entered, release = Event(), Event()

    def block():
        entered.set()
        assert release.wait(timeout=10)

    if phase == "recovery":
        original_rebuild = yazio_sync.rebuild_yazio_projection_run
        monkeypatch.setattr(
            yazio_sync, "rebuild_yazio_projection_run",
            lambda **kwargs: (_ for _ in ()).throw(RuntimeError("seed pending work")),
        )
        with pytest.raises(RuntimeError, match="seed pending"):
            yazio_sync.run_manual_yazio_sync(user_id, now=attempted_at)
        monkeypatch.setattr(yazio_sync, "rebuild_yazio_projection_run", original_rebuild)
        recovery_rebuild = yazio_projection_retry.rebuild_yazio_projection_run

        def recover(**kwargs):
            block()
            return recovery_rebuild(**kwargs)

        monkeypatch.setattr(yazio_projection_retry, "rebuild_yazio_projection_run", recover)
    elif phase == "evidence":
        original_persist = yazio_sync._persist_import_locked

        def persist(*args, **kwargs):
            result = original_persist(*args, **kwargs)
            block()
            return result

        monkeypatch.setattr(yazio_sync, "_persist_import_locked", persist)
    elif phase == "projection":
        original_rebuild = yazio_sync.rebuild_yazio_projection_run

        def rebuild(**kwargs):
            block()
            return original_rebuild(**kwargs)

        monkeypatch.setattr(yazio_sync, "rebuild_yazio_projection_run", rebuild)
    else:
        original_commit = Session.commit

        def commit(session):
            if any(
                isinstance(row, YazioConnection)
                and (row.last_error if phase == "failure" else row.last_success_at)
                for row in session.dirty
            ):
                block()
            return original_commit(session)

        monkeypatch.setattr(Session, "commit", commit)
        if phase == "failure":
            def fail_fetch(*args):
                raise YazioProviderNetworkTimeoutError()
            monkeypatch.setattr(yazio_sync, "fetch_yazio_domain_transport", fail_fetch)

    # Release the fixture's read transaction before the worker acquires locks.
    db.rollback()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(yazio_sync.run_manual_yazio_sync, user_id, now=attempted_at)
        try:
            assert entered.wait(timeout=10)
            with pytest.raises(YazioOperationBusy), yazio_operation_slot(user_id):
                pass
            with (
                SessionLocal() as other,
                pytest.raises(UserOperationBusy),
                exclusive_user_lifecycle_operation(other, user_id),
            ):
                pass
            with SessionLocal() as fresh:
                stored = fresh.get(YazioConnection, connection_id)
                assert stored.last_success_at is None
                if phase in {"projection", "status", "recovery"}:
                    assert fresh.scalar(select(NutritionIngestionRun.projection_pending)) is True
        finally:
            release.set()
        if phase == "failure":
            with pytest.raises(yazio_sync.YazioNetworkTimeoutError):
                future.result(timeout=10)
        else:
            assert future.result(timeout=10).status == "completed"
    with (
        yazio_operation_slot(user_id),
        SessionLocal() as fresh,
        exclusive_user_lifecycle_operation(fresh, user_id),
    ):
        stored = fresh.get(YazioConnection, connection_id)
        if phase == "failure":
            assert stored.last_success_at is None and stored.last_error is not None
        else:
            assert stored.last_success_at is not None and stored.last_error is None
            assert all(not pending for pending in fresh.scalars(select(NutritionIngestionRun.projection_pending)))
