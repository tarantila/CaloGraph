import base64
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
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
    LOGIN_PASSWORD_MAX_PARALLEL,
    RateLimitExceeded,
    check_rate_limit,
    clear_rate_limit,
    hash_rate_limit_key,
    login_password_slot,
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
def test_postgres_login_password_slots_are_global_and_released() -> None:
    with ExitStack() as stack:
        for _ in range(LOGIN_PASSWORD_MAX_PARALLEL):
            stack.enter_context(login_password_slot())

        with pytest.raises(RateLimitExceeded), login_password_slot():
            pass

    with login_password_slot():
        pass


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_concurrent_login_account_limit_reserves_before_password_verification(
    client: TestClient,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del user
    monkeypatch.setattr(settings, "login_ip_rate_limit", 100)
    monkeypatch.setattr(settings, "login_rate_limit", 2)
    attempts = 6
    start = threading.Barrier(attempts)
    limit_reached = threading.Event()
    release = threading.Event()
    state_lock = threading.Lock()
    active = 0
    max_active = 0

    def fake_verify(_password: str, _password_hash: str | None) -> bool:
        nonlocal active, max_active
        with state_lock:
            active += 1
            max_active = max(max_active, active)
            if active == 2:
                limit_reached.set()
        release.wait(timeout=10)
        with state_lock:
            active -= 1
        return False

    monkeypatch.setattr("app.api.auth.verify_login_password", fake_verify)

    def attempt(_: int) -> int:
        start.wait()
        return client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "wrong-password"},
        ).status_code

    with ThreadPoolExecutor(max_workers=attempts) as executor:
        futures = [executor.submit(attempt, index) for index in range(attempts)]
        try:
            assert limit_reached.wait(timeout=5)
            deadline = time.monotonic() + 5
            while (
                sum(future.done() for future in futures) < attempts - 2
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            assert sum(future.done() for future in futures) == attempts - 2
        finally:
            release.set()
        statuses = [future.result() for future in futures]

    assert statuses.count(401) == 2
    assert statuses.count(429) == attempts - 2
    assert max_active == 2


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_login_endpoint_enforces_global_password_capacity(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "login_ip_rate_limit", 100)
    monkeypatch.setattr(settings, "login_rate_limit", 100)
    attempts = LOGIN_PASSWORD_MAX_PARALLEL * 2
    start = threading.Barrier(attempts)
    capacity_reached = threading.Event()
    release = threading.Event()
    state_lock = threading.Lock()
    active = 0
    max_active = 0

    def fake_verify(_password: str, _password_hash: str | None) -> bool:
        nonlocal active, max_active
        with state_lock:
            active += 1
            max_active = max(max_active, active)
            if active == LOGIN_PASSWORD_MAX_PARALLEL:
                capacity_reached.set()
        release.wait(timeout=10)
        with state_lock:
            active -= 1
        return False

    monkeypatch.setattr("app.api.auth.verify_login_password", fake_verify)

    def attempt(_: int) -> int:
        start.wait()
        return client.post(
            "/api/v1/auth/login",
            json={"username": "capacity-target", "password": "wrong-password"},
        ).status_code

    with ThreadPoolExecutor(max_workers=attempts) as executor:
        futures = [executor.submit(attempt, index) for index in range(attempts)]
        try:
            assert capacity_reached.wait(timeout=5)
            deadline = time.monotonic() + 5
            while (
                sum(future.done() for future in futures) < LOGIN_PASSWORD_MAX_PARALLEL
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            assert sum(future.done() for future in futures) == LOGIN_PASSWORD_MAX_PARALLEL
        finally:
            release.set()
        statuses = [future.result() for future in futures]

    assert statuses.count(401) == LOGIN_PASSWORD_MAX_PARALLEL
    assert statuses.count(429) == LOGIN_PASSWORD_MAX_PARALLEL
    assert max_active == LOGIN_PASSWORD_MAX_PARALLEL

    account_key = "account:capacity-target"
    with Session(application_engine) as db:
        account_count = db.scalar(
            select(RateLimitBucket.count).where(
                RateLimitBucket.action == "login-account",
                RateLimitBucket.key_hash == hash_rate_limit_key(account_key),
            )
        )
        clear_rate_limit(db, "login-account", account_key)

    assert account_count == LOGIN_PASSWORD_MAX_PARALLEL


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
