import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy import create_engine, func, select, text

from alembic import command
from app.auth.security import hash_password, verify_password
from app.database import SessionLocal
from app.database import engine as application_engine
from app.models import (
    AccountRecoveryToken,
    MfaRecoveryCode,
    User,
    UserTotpCredential,
)
from app.services.account_recovery import (
    AccountRecoveryRejected,
    complete_account_recovery,
)
from app.services.user_lifecycle import (
    UserLifecycleRejected,
    delete_user,
    issue_account_recovery,
    reactivate_user,
    reset_user_authenticators,
)

POSTGRES_TESTS_ENABLED = (
    os.environ.get("CALOGRAPH_ALLOW_DESTRUCTIVE_POSTGRES_TESTS") == "1"
    and bool(os.environ.get("CALOGRAPH_POSTGRES_TEST_URL"))
)
PREVIOUS_REVISION = "20260810_0010"
TARGET_REVISION = "20260810_0011"
NEW_PASSWORD = "postgres-recovery-password-2026"


def _create_actor_and_target(*, target_active: bool = True) -> tuple[uuid.UUID, uuid.UUID]:
    with SessionLocal() as db:
        actor = User(
            username=f"postgres-recovery-admin-{uuid.uuid4()}",
            password_hash=hash_password("postgres-admin-password-2026"),
            is_admin=True,
        )
        target = User(
            username=f"postgres-recovery-target-{uuid.uuid4()}",
            password_hash=hash_password("postgres-target-password-2026"),
            is_active=target_active,
            deactivated_at=None if target_active else datetime.now(UTC),
        )
        db.add_all([actor, target])
        db.commit()
        return actor.id, target.id


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_account_recovery_migration_preserves_users_and_creates_constraints() -> None:
    assert application_engine.dialect.name == "postgresql"
    database_url = os.environ["CALOGRAPH_POSTGRES_TEST_URL"]
    alembic_config = Config("alembic.ini")
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    engine = create_engine(database_url, pool_pre_ping=True)
    existing_id = uuid.UUID("60000000-0000-0000-0000-000000000001")

    try:
        command.stamp(alembic_config, TARGET_REVISION)
        command.downgrade(alembic_config, PREVIOUS_REVISION)
        with engine.begin() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == PREVIOUS_REVISION
            assert "account_recovery_tokens" not in sa.inspect(connection).get_table_names()
            connection.execute(
                text(
                    """
                    INSERT INTO users (
                        id, username, password_hash, language, timezone,
                        week_starts_on, preferred_weight_unit,
                        raw_payload_retention_days, is_active, deactivated_at,
                        is_admin, created_at, updated_at
                    ) VALUES (
                        :id, 'recovery-migration-user', 'test-password-hash',
                        'de', 'Europe/Berlin', 0, 'kg', 0, true, NULL,
                        false, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    """
                ),
                {"id": existing_id},
            )

        command.upgrade(alembic_config, TARGET_REVISION)
        with engine.connect() as connection:
            inspector = sa.inspect(connection)
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == TARGET_REVISION
            assert connection.scalar(text("SELECT count(*) FROM users WHERE id = :id"), {"id": existing_id}) == 1
            columns = {column["name"] for column in inspector.get_columns("account_recovery_tokens")}
            assert columns == {
                "id",
                "user_id",
                "token_hash",
                "created_at",
                "expires_at",
                "used_at",
                "revoked_at",
            }
            foreign_keys = inspector.get_foreign_keys("account_recovery_tokens")
            assert len(foreign_keys) == 1
            assert foreign_keys[0]["referred_table"] == "users"
            assert foreign_keys[0]["options"].get("ondelete") == "CASCADE"
            unique_columns = {
                tuple(constraint["column_names"])
                for constraint in inspector.get_unique_constraints("account_recovery_tokens")
            }
            assert ("token_hash",) in unique_columns
            indexes = {
                index["name"]: tuple(index["column_names"])
                for index in inspector.get_indexes("account_recovery_tokens")
            }
            assert indexes["ix_account_recovery_tokens_user_id"] == ("user_id",)
            assert indexes["ix_account_recovery_tokens_expires_at"] == ("expires_at",)
            assert indexes["ix_account_recovery_tokens_user_open"] == (
                "user_id",
                "used_at",
                "revoked_at",
            )
    finally:
        command.upgrade(alembic_config, TARGET_REVISION)
        engine.dispose()


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_postgres_recovery_completion_can_succeed_only_once_concurrently() -> None:
    assert application_engine.dialect.name == "postgresql"
    actor_id, target_id = _create_actor_and_target()
    with SessionLocal() as db:
        recovery, raw_token = issue_account_recovery(db, actor_id, target_id)
        recovery_id = recovery.id

    barrier = Barrier(2)

    def complete() -> str:
        barrier.wait()
        with SessionLocal() as db:
            try:
                complete_account_recovery(db, raw_token, NEW_PASSWORD)
            except AccountRecoveryRejected:
                return "rejected"
            return "success"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: complete(), range(2)))

    assert results.count("success") == 1
    assert results.count("rejected") == 1
    with SessionLocal() as db:
        token = db.get(AccountRecoveryToken, recovery_id)
        target = db.get(User, target_id)
        assert token is not None and token.used_at is not None
        assert target is not None and target.is_active is False
        assert verify_password(target.password_hash, NEW_PASSWORD)


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_postgres_recovery_issue_and_reactivate_preserve_consistent_state() -> None:
    assert application_engine.dialect.name == "postgresql"
    actor_id, target_id = _create_actor_and_target()
    barrier = Barrier(2)

    def issue() -> str:
        barrier.wait()
        with SessionLocal() as db:
            try:
                issue_account_recovery(db, actor_id, target_id)
            except UserLifecycleRejected as exc:
                return f"rejected:{exc.reason}"
            return "success"

    def reactivate() -> str:
        barrier.wait()
        with SessionLocal() as db:
            try:
                reactivate_user(db, actor_id, target_id)
            except UserLifecycleRejected as exc:
                return f"rejected:{exc.reason}"
            return "success"

    with ThreadPoolExecutor(max_workers=2) as executor:
        issue_result = executor.submit(issue)
        reactivate_result = executor.submit(reactivate)
        results = [issue_result.result(), reactivate_result.result()]

    assert all(result == "success" or result == "rejected:operation_busy" for result in results)
    with SessionLocal() as db:
        target = db.get(User, target_id)
        open_tokens = db.scalar(
            select(func.count(AccountRecoveryToken.id)).where(
                AccountRecoveryToken.user_id == target_id,
                AccountRecoveryToken.used_at.is_(None),
                AccountRecoveryToken.revoked_at.is_(None),
            )
        )
        assert target is not None
        assert open_tokens in {0, 1}
        if target.is_active:
            assert open_tokens == 0
        else:
            assert target.deactivated_at is not None


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_postgres_authenticator_reset_and_reactivate_do_not_partially_mutate() -> None:
    assert application_engine.dialect.name == "postgresql"
    actor_id, target_id = _create_actor_and_target(target_active=False)
    with SessionLocal() as db:
        db.add(
            UserTotpCredential(
                user_id=target_id,
                encrypted_secret=b"encrypted-secret",
                enabled_at=datetime.now(UTC),
            )
        )
        db.add(MfaRecoveryCode(user_id=target_id, code_hash="a" * 64))
        db.commit()

    barrier = Barrier(2)

    def reset() -> str:
        barrier.wait()
        with SessionLocal() as db:
            try:
                reset_user_authenticators(db, actor_id, target_id)
            except UserLifecycleRejected as exc:
                return f"rejected:{exc.reason}"
            return "success"

    def reactivate() -> str:
        barrier.wait()
        with SessionLocal() as db:
            try:
                reactivate_user(db, actor_id, target_id)
            except UserLifecycleRejected as exc:
                return f"rejected:{exc.reason}"
            return "success"

    with ThreadPoolExecutor(max_workers=2) as executor:
        reset_result = executor.submit(reset)
        reactivate_result = executor.submit(reactivate)
        results = [reset_result.result(), reactivate_result.result()]

    assert all(
        result in {"success", "rejected:operation_busy", "rejected:target_active"}
        for result in results
    )
    with SessionLocal() as db:
        totp_count = db.scalar(
            select(func.count(UserTotpCredential.user_id)).where(
                UserTotpCredential.user_id == target_id
            )
        )
        recovery_count = db.scalar(
            select(func.count(MfaRecoveryCode.id)).where(MfaRecoveryCode.user_id == target_id)
        )
        assert (totp_count, recovery_count) in {(0, 0), (1, 1)}


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_postgres_hard_delete_and_recovery_complete_have_no_partial_outcome() -> None:
    assert application_engine.dialect.name == "postgresql"
    actor_id, target_id = _create_actor_and_target()
    with SessionLocal() as db:
        target = db.get(User, target_id)
        assert target is not None
        target_username = target.username
        _, raw_token = issue_account_recovery(db, actor_id, target_id)

    barrier = Barrier(2)

    def hard_delete() -> str:
        barrier.wait()
        with SessionLocal() as db:
            try:
                delete_user(db, actor_id, target_id, target_username)
            except UserLifecycleRejected as exc:
                return f"rejected:{exc.reason}"
            return "success"

    def complete() -> str:
        barrier.wait()
        with SessionLocal() as db:
            try:
                complete_account_recovery(db, raw_token, NEW_PASSWORD)
            except AccountRecoveryRejected:
                return "rejected"
            return "success"

    with ThreadPoolExecutor(max_workers=2) as executor:
        delete_result = executor.submit(hard_delete)
        complete_result = executor.submit(complete)
        results = [delete_result.result(), complete_result.result()]

    assert results[0] in {"success", "rejected:operation_busy"}
    assert results[1] in {"success", "rejected"}
    with SessionLocal() as db:
        target = db.get(User, target_id)
        if target is None:
            assert db.scalar(
                select(func.count(AccountRecoveryToken.id)).where(
                    AccountRecoveryToken.user_id == target_id
                )
            ) == 0
        else:
            assert target.is_active is False
