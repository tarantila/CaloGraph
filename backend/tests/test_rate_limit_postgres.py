import base64
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.auth.security import (
    create_session,
    hash_mfa_recovery_code,
    hash_password,
)
from app.config import settings
from app.database import engine as application_engine
from app.models import (
    MfaRecoveryCode,
    PasskeyCredential,
    RateLimitBucket,
    User,
    UserTotpCredential,
)
from app.schemas import WebAuthnRegistrationCredentialInput
from app.services.import_guard import ImportAlreadyRunning, import_slot
from app.services.mfa import consume_mfa_factor
from app.services.mfa_crypto import encrypt_mfa_secret
from app.services.passkeys import (
    PasskeyRegistrationError,
    begin_passkey_registration,
    complete_passkey_registration,
)
from app.services.rate_limit import (
    check_rate_limit,
    clear_rate_limit,
    hash_rate_limit_key,
)
from app.services.yazio_guard import YazioOperationBusy, yazio_operation_slot

POSTGRES_TESTS_ENABLED = (
    os.environ.get("CALOGRAPH_ALLOW_DESTRUCTIVE_POSTGRES_TESTS") == "1"
    and bool(os.environ.get("CALOGRAPH_POSTGRES_TEST_URL"))
)


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_application_guards_are_exercised_with_postgres() -> None:
    assert application_engine.dialect.name == "postgresql"


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_concurrent_rate_limit_updates_are_not_lost() -> None:
    database_url = os.environ["CALOGRAPH_POSTGRES_TEST_URL"]
    engine = create_engine(database_url, pool_pre_ping=True)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    action = "test-concurrency"
    key = f"test:{uuid.uuid4()}"
    attempts = 40

    def consume(_: int) -> None:
        with sessions() as db:
            check_rate_limit(db, action, key, limit=attempts + 1, window_seconds=300)

    try:
        with ThreadPoolExecutor(max_workers=20) as executor:
            list(executor.map(consume, range(attempts)))

        with Session(engine) as db:
            count = db.scalar(
                select(func.sum(RateLimitBucket.count)).where(
                    RateLimitBucket.key_hash == hash_rate_limit_key(key),
                    RateLimitBucket.action == action,
                )
            )
            assert count == attempts
    finally:
        with Session(engine) as db:
            clear_rate_limit(db, action, key)
        engine.dispose()


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_postgres_import_slot_is_exclusive_and_released() -> None:
    user_id = uuid.uuid4()

    with import_slot(user_id), pytest.raises(
        ImportAlreadyRunning
    ), import_slot(user_id):
        pass

    with import_slot(user_id):
        pass


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_postgres_yazio_slots_enforce_user_and_global_capacity(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "yazio_max_parallel_operations", 2)

    with yazio_operation_slot("user-1"):
        with pytest.raises(YazioOperationBusy), yazio_operation_slot("user-1"):
            pass
        with (
            yazio_operation_slot("user-2"),
            pytest.raises(YazioOperationBusy),
            yazio_operation_slot("user-3"),
        ):
            pass

    with yazio_operation_slot("user-3"):
        pass


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_concurrent_recovery_code_consumption_succeeds_only_once() -> None:
    database_url = os.environ["CALOGRAPH_POSTGRES_TEST_URL"]
    engine = create_engine(database_url, pool_pre_ping=True)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    raw_code = "A1B2-C3D4-E5F6-0123"
    with sessions() as db:
        user = User(
            username=f"mfa-race-{uuid.uuid4()}",
            password_hash=hash_password("integration-password-is-unique"),
        )
        db.add(user)
        db.flush()
        db.add(
            UserTotpCredential(
                user_id=user.id,
                encrypted_secret=encrypt_mfa_secret("JBSWY3DPEHPK3PXP"),
                enabled_at=datetime.now(UTC),
            )
        )
        db.add(
            MfaRecoveryCode(
                user_id=user.id,
                code_hash=hash_mfa_recovery_code(raw_code.replace("-", "")),
            )
        )
        db.commit()
        user_id = user.id

    def consume(_: int) -> bool:
        with sessions() as db:
            credential = db.scalar(
                select(UserTotpCredential)
                .where(UserTotpCredential.user_id == user_id)
                .with_for_update()
            )
            assert credential is not None
            accepted = consume_mfa_factor(db, credential, raw_code)
            if accepted:
                db.commit()
            else:
                db.rollback()
            return accepted

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(consume, range(2)))
        assert sorted(results) == [False, True]
    finally:
        with sessions() as db:
            item = db.get(User, user_id)
            if item is not None:
                db.delete(item)
                db.commit()
        engine.dispose()


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_webauthn_challenge_can_be_claimed_only_once(monkeypatch) -> None:
    database_url = os.environ["CALOGRAPH_POSTGRES_TEST_URL"]
    engine = create_engine(database_url, pool_pre_ping=True)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    credential_id = b"postgres-concurrent-passkey"
    encoded_id = base64.urlsafe_b64encode(credential_id).rstrip(b"=").decode()
    credential = WebAuthnRegistrationCredentialInput.model_validate(
        {
            "id": encoded_id,
            "rawId": encoded_id,
            "response": {
                "clientDataJSON": "Y2xpZW50",
                "attestationObject": "YXR0ZXN0YXRpb24",
                "transports": ["internal"],
            },
            "type": "public-key",
        }
    )
    monkeypatch.setattr(
        "app.services.passkeys.verify_registration_response",
        lambda **_: type(
            "RegistrationVerification",
            (),
            {
                "credential_id": credential_id,
                "credential_public_key": b"postgres-public-key",
                "sign_count": 0,
                "credential_device_type": type(
                    "DeviceType",
                    (),
                    {"value": "single_device"},
                )(),
                "credential_backed_up": False,
            },
        )(),
    )

    with sessions() as db:
        user = User(
            username=f"passkey-race-{uuid.uuid4()}",
            password_hash=hash_password("integration-password-is-unique"),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        session, _, _ = create_session(db, user)
        challenge_id, _ = begin_passkey_registration(db, user, session.id)
        user_id = user.id
        session_id = session.id

    def register(_: int) -> bool:
        with sessions() as db:
            user = db.get(User, user_id)
            assert user is not None
            try:
                complete_passkey_registration(
                    db,
                    user,
                    session_id,
                    challenge_id,
                    "Concurrent passkey",
                    credential,
                )
            except PasskeyRegistrationError:
                return False
            return True

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(register, range(2)))
        assert sorted(results) == [False, True]
        with sessions() as db:
            count = db.scalar(
                select(func.count(PasskeyCredential.id)).where(
                    PasskeyCredential.user_id == user_id
                )
            )
            assert count == 1
    finally:
        with sessions() as db:
            item = db.get(User, user_id)
            if item is not None:
                db.delete(item)
                db.commit()
        engine.dispose()
