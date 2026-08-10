import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from threading import Barrier, Event

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy import create_engine, func, select, text

from alembic import command
from app.database import SessionLocal
from app.database import engine as application_engine
from app.importers.json_adapter import AdapterResult
from app.models import (
    ApiToken,
    HealthSample,
    ImportBatch,
    ImportError,
    MfaRecoveryCode,
    NutritionTarget,
    PasskeyCredential,
    RateLimitBucket,
    RawImportPayload,
    TrackingOverride,
    TrackingQualitySettings,
    User,
    UserInvitation,
    UserSession,
    UserTotpCredential,
    WebAuthnChallenge,
    WebAuthnUserHandle,
    YazioConnection,
)
from app.services import import_service
from app.services.import_service import persist_import
from app.services.rate_limit import hash_rate_limit_key, normalize_account_identifier
from app.services.user_lifecycle import (
    UserLifecycleRejected,
    deactivate_user,
    delete_user,
)
from app.services.user_operation_lock import InactiveUserOperation
from app.services.yazio_sync import YazioSyncError, sync_yazio_user

POSTGRES_TESTS_ENABLED = (
    os.environ.get("CALOGRAPH_ALLOW_DESTRUCTIVE_POSTGRES_TESTS") == "1"
    and bool(os.environ.get("CALOGRAPH_POSTGRES_TEST_URL"))
)
PREVIOUS_REVISION = "20260803_0009"
TARGET_REVISION = "20260810_0010"


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_user_lifecycle_migration_backfills_state_and_enforces_consistency() -> None:
    assert application_engine.dialect.name == "postgresql"
    database_url = os.environ["CALOGRAPH_POSTGRES_TEST_URL"]
    alembic_config = Config("alembic.ini")
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    engine = create_engine(database_url, pool_pre_ping=True)
    active_id = uuid.UUID("50000000-0000-0000-0000-000000000001")
    inactive_id = uuid.UUID("50000000-0000-0000-0000-000000000002")
    updated_at = datetime(2026, 8, 9, 12, tzinfo=UTC)

    try:
        command.stamp(alembic_config, TARGET_REVISION)
        command.downgrade(alembic_config, PREVIOUS_REVISION)
        with engine.begin() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == PREVIOUS_REVISION
            columns = {
                column["name"] for column in sa.inspect(connection).get_columns("users")
            }
            assert "deactivated_at" not in columns
            connection.execute(
                text(
                    """
                    INSERT INTO users (
                        id, username, password_hash, language, timezone,
                        week_starts_on, preferred_weight_unit,
                        raw_payload_retention_days, is_active, is_admin,
                        created_at, updated_at
                    ) VALUES (
                        :id, :username, 'test-password-hash', 'de', 'Europe/Berlin',
                        0, 'kg', 0, :is_active, false, :updated_at, :updated_at
                    )
                    """
                ),
                [
                    {
                        "id": active_id,
                        "username": "migration-active-user",
                        "is_active": True,
                        "updated_at": updated_at,
                    },
                    {
                        "id": inactive_id,
                        "username": "migration-inactive-user",
                        "is_active": False,
                        "updated_at": updated_at,
                    },
                ],
            )

        command.upgrade(alembic_config, TARGET_REVISION)
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == TARGET_REVISION
            columns = {
                column["name"] for column in sa.inspect(connection).get_columns("users")
            }
            constraints = {
                constraint["name"]
                for constraint in sa.inspect(connection).get_check_constraints("users")
            }
            assert "deactivated_at" in columns
            assert "ck_users_active_deactivation_state" in constraints
            rows = {
                row.id: row
                for row in connection.execute(
                    text("SELECT id, is_active, deactivated_at, updated_at FROM users")
                )
            }
            assert rows[active_id].deactivated_at is None
            assert rows[inactive_id].deactivated_at == rows[inactive_id].updated_at

        with pytest.raises(sa.exc.IntegrityError), engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE users
                    SET is_active = false, deactivated_at = NULL
                    WHERE id = :id
                    """
                ),
                {"id": active_id},
            )
        command.downgrade(alembic_config, PREVIOUS_REVISION)
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM users")) == 2
            assert "deactivated_at" not in {
                column["name"] for column in sa.inspect(connection).get_columns("users")
            }
        command.upgrade(alembic_config, TARGET_REVISION)
    finally:
        engine.dispose()


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_postgres_hard_delete_cascades_all_user_owned_rows() -> None:
    assert application_engine.dialect.name == "postgresql"
    now = datetime(2026, 8, 10, 12, tzinfo=UTC)
    with SessionLocal() as db:
        actor = User(
            username="postgres-lifecycle-admin",
            password_hash="test-password-hash",
            is_admin=True,
        )
        target = User(
            username="postgres-delete-target",
            password_hash="test-password-hash",
            is_active=False,
            deactivated_at=now,
        )
        db.add_all([actor, target])
        db.flush()
        session = UserSession(
            user_id=target.id,
            token_hash="a" * 64,
            csrf_hash="b" * 64,
            expires_at=now + timedelta(days=1),
        )
        token = ApiToken(
            user_id=target.id,
            label="postgres-delete-token",
            token_prefix="postgres",
            token_hash="c" * 64,
        )
        batch = ImportBatch(
            user_id=target.id,
            source_type="calograph_json",
            status="completed",
            received=1,
            inserted=1,
        )
        db.add_all(
            [
                session,
                token,
                batch,
                UserInvitation(
                    token_hash="d" * 64,
                    invited_by_user_id=target.id,
                    expires_at=now + timedelta(days=1),
                ),
                UserTotpCredential(
                    user_id=target.id,
                    encrypted_secret=b"encrypted-totp",
                    enabled_at=now,
                ),
                MfaRecoveryCode(user_id=target.id, code_hash="e" * 64),
                WebAuthnUserHandle(user_id=target.id, user_handle=b"postgres-handle"),
                PasskeyCredential(
                    user_id=target.id,
                    label="PostgreSQL test key",
                    credential_id=b"postgres-credential",
                    public_key=b"public-key",
                    sign_count=0,
                    transports=["internal"],
                    device_type="single_device",
                    backed_up=False,
                ),
                NutritionTarget(
                    user_id=target.id,
                    valid_from=date(2026, 8, 1),
                    calories_kcal=Decimal("2000"),
                    protein_g=Decimal("120"),
                ),
                TrackingQualitySettings(user_id=target.id),
                TrackingOverride(
                    user_id=target.id,
                    local_date=date(2026, 8, 1),
                    status="complete",
                ),
                YazioConnection(
                    user_id=target.id,
                    encrypted_email=b"encrypted-email",
                    encrypted_password=b"encrypted-password",
                    source_identifier=f"yazio:{target.id}",
                    sync_enabled=False,
                    sync_interval_minutes=360,
                    sync_days=7,
                ),
            ]
        )
        db.flush()
        db.add_all(
            [
                WebAuthnChallenge(
                    purpose="authentication",
                    challenge=b"postgres-challenge",
                    user_id=target.id,
                    session_id=session.id,
                    expires_at=now + timedelta(minutes=5),
                ),
                RawImportPayload(
                    batch_id=batch.id,
                    content_type="application/json",
                    compressed_payload=b"compressed",
                    expires_at=now + timedelta(days=1),
                ),
                ImportError(
                    batch_id=batch.id,
                    item_index=1,
                    metric_type="dietary_energy_kcal",
                    error_code="invalid",
                    safe_detail="Ungültiger Testwert",
                ),
                HealthSample(
                    user_id=target.id,
                    import_batch_id=batch.id,
                    external_sample_id="postgres-sample",
                    fingerprint="f" * 64,
                    source_type="calograph_json",
                    source_identifier="postgres-source",
                    metric_type="dietary_energy_kcal",
                    value=Decimal("2000"),
                    unit="kcal",
                    original_value=Decimal("2000"),
                    original_unit="kcal",
                    start_at=now,
                    end_at=now,
                    local_date=date(2026, 8, 1),
                    timezone="Europe/Berlin",
                ),
            ]
        )
        db.flush()
        for index, key in enumerate(
            (
                f"user:{target.id}",
                f"account:{normalize_account_identifier(target.username)}",
                f"token:{token.id}",
            )
        ):
            db.add(
                RateLimitBucket(
                    key_hash=hash_rate_limit_key(key),
                    action=f"postgres-lifecycle-{index}",
                    window_start=now,
                )
            )
        db.add(
            RateLimitBucket(
                key_hash=hash_rate_limit_key("postgres-unrelated"),
                action="postgres-unrelated",
                window_start=now,
            )
        )
        db.commit()
        actor_id = actor.id
        target_id = target.id

        delete_user(db, actor_id, target_id)

        assert db.get(User, target_id) is None
        assert db.get(User, actor_id) is not None
        assert db.scalar(select(func.count()).select_from(User)) == 1
        deleted_tables = (
            ApiToken,
            HealthSample,
            ImportBatch,
            ImportError,
            MfaRecoveryCode,
            NutritionTarget,
            PasskeyCredential,
            RawImportPayload,
            TrackingOverride,
            TrackingQualitySettings,
            UserInvitation,
            UserSession,
            UserTotpCredential,
            WebAuthnChallenge,
            WebAuthnUserHandle,
            YazioConnection,
        )
        for model in deleted_tables:
            assert db.scalar(select(func.count()).select_from(model)) == 0
        assert db.scalar(select(func.count()).select_from(RateLimitBucket)) == 1
        db.delete(actor)
        db.execute(sa.delete(RateLimitBucket))
        db.commit()


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_concurrent_admin_deactivations_preserve_one_active_admin() -> None:
    assert application_engine.dialect.name == "postgresql"
    with SessionLocal() as db:
        first = User(
            username="postgres-concurrent-admin-one",
            password_hash="test-password-hash",
            is_admin=True,
        )
        second = User(
            username="postgres-concurrent-admin-two",
            password_hash="test-password-hash",
            is_admin=True,
        )
        db.add_all([first, second])
        db.commit()
        user_ids = (first.id, second.id)

    barrier = Barrier(2)

    def deactivate(actor_id: uuid.UUID, target_id: uuid.UUID) -> str:
        barrier.wait()
        with SessionLocal() as db:
            try:
                deactivate_user(db, actor_id, target_id)
            except UserLifecycleRejected as exc:
                return exc.reason
        return "success"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda ids: deactivate(*ids),
                ((user_ids[0], user_ids[1]), (user_ids[1], user_ids[0])),
            )
        )

    with SessionLocal() as db:
        assert results.count("success") == 1
        assert results.count("operation_busy") + results.count("not_admin") == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(User)
                .where(User.id.in_(user_ids), User.is_active.is_(True))
            )
            == 1
        )
        db.execute(sa.delete(User).where(User.id.in_(user_ids)))
        db.commit()


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_import_and_deactivation_are_serialized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert application_engine.dialect.name == "postgresql"
    with SessionLocal() as db:
        actor = User(
            username="postgres-import-race-admin",
            password_hash="test-password-hash",
            is_admin=True,
        )
        target = User(
            username="postgres-import-race-target",
            password_hash="test-password-hash",
        )
        db.add_all([actor, target])
        db.commit()
        actor_id = actor.id
        target_id = target.id

    entered = Event()
    release = Event()
    original_persist = import_service._persist_import_locked

    def blocked_persist(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=10)
        return original_persist(*args, **kwargs)

    monkeypatch.setattr(import_service, "_persist_import_locked", blocked_persist)

    def run_import() -> None:
        with SessionLocal() as db:
            attached_target = db.get(User, target_id)
            assert attached_target is not None
            persist_import(
                db,
                attached_target,
                AdapterResult(source_type="calograph_json"),
                None,
                "application/json",
                "postgres-import-race",
            )

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(run_import)
            assert entered.wait(timeout=10)
            with SessionLocal() as db:
                with pytest.raises(UserLifecycleRejected) as caught:
                    deactivate_user(db, actor_id, target_id)
                assert caught.value.reason == "operation_busy"
            release.set()
            future.result(timeout=10)
    finally:
        release.set()

    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(ImportBatch)
                .where(ImportBatch.user_id == target_id)
            )
            == 1
        )
        deactivate_user(db, actor_id, target_id)
        with pytest.raises(InactiveUserOperation):
            attached_target = db.get(User, target_id)
            assert attached_target is not None
            persist_import(
                db,
                attached_target,
                AdapterResult(source_type="calograph_json"),
                None,
                "application/json",
                "postgres-import-after-deactivation",
            )
        db.execute(sa.delete(User).where(User.id.in_((actor_id, target_id))))
        db.commit()


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_yazio_sync_and_deactivation_are_serialized() -> None:
    assert application_engine.dialect.name == "postgresql"
    with SessionLocal() as db:
        actor = User(
            username="postgres-yazio-race-admin",
            password_hash="test-password-hash",
            is_admin=True,
        )
        target = User(
            username="postgres-yazio-race-target",
            password_hash="test-password-hash",
        )
        db.add_all([actor, target])
        db.commit()
        actor_id = actor.id
        target_id = target.id
        db.expunge(target)

    entered = Event()
    release = Event()

    def blocked_fetch(*_args):
        entered.set()
        assert release.wait(timeout=10)
        return {
            "2026-08-01": {
                "daily_summary": {
                    "meals": {
                        "dinner": {
                            "nutrients": {
                                "energy.energy": 1800,
                            }
                        }
                    }
                }
            }
        }

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                sync_yazio_user,
                target,
                "owner@example.com",
                "secret",
                date(2026, 8, 1),
                date(2026, 8, 1),
                None,
                blocked_fetch,
            )
            assert entered.wait(timeout=10)
            with SessionLocal() as db:
                with pytest.raises(UserLifecycleRejected) as caught:
                    deactivate_user(db, actor_id, target_id)
                assert caught.value.reason == "operation_busy"
            release.set()
            future.result(timeout=10)
    finally:
        release.set()

    fetch_called = False

    def fetch_after_deactivation(*_args):
        nonlocal fetch_called
        fetch_called = True
        return {}

    with SessionLocal() as db:
        deactivate_user(db, actor_id, target_id)
    with pytest.raises(YazioSyncError, match="nicht aktiv"):
        sync_yazio_user(
            target,
            "owner@example.com",
            "secret",
            date(2026, 8, 1),
            date(2026, 8, 1),
            fetcher=fetch_after_deactivation,
        )
    assert fetch_called is False

    with SessionLocal() as db:
        db.execute(sa.delete(User).where(User.id.in_((actor_id, target_id))))
        db.commit()
