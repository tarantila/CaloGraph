from datetime import UTC, datetime, timedelta

import pyotp
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.auth.security import (
    MFA_LOGIN_STATE_TTL_SECONDS,
    create_mfa_login_state,
    create_session,
    verify_mfa_login_state,
)
from app.config import settings
from app.models import MfaRecoveryCode, User, UserTotpCredential
from app.services.mfa_crypto import MfaEncryptionError, decrypt_mfa_secret
from app.services.user_operation_lock import (
    UserOperationBusy,
    exclusive_user_lifecycle_operation,
)

PASSWORD = "correct-horse-battery-staple"


def _login(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": PASSWORD},
    )
    assert response.status_code == 200
    return response.json()


def _enable_totp(
    client: TestClient,
    db,
) -> tuple[str, list[str]]:
    login = _login(client)
    setup = client.post(
        "/api/v1/settings/mfa/totp/setup",
        headers={"X-CSRF-Token": str(login["csrf_token"])},
        json={"current_password": PASSWORD},
    )
    assert setup.status_code == 200
    secret = setup.json()["secret"]
    credential = db.scalar(select(UserTotpCredential))
    assert credential is not None
    assert secret.encode() not in credential.encrypted_secret
    assert decrypt_mfa_secret(credential.encrypted_secret) == secret

    confirmation = client.post(
        "/api/v1/settings/mfa/totp/confirm",
        headers={"X-CSRF-Token": str(login["csrf_token"])},
        json={"code": pyotp.TOTP(secret).now()},
    )
    assert confirmation.status_code == 200
    recovery_codes = confirmation.json()["recovery_codes"]
    assert len(recovery_codes) == 10
    assert len(set(recovery_codes)) == 10
    return secret, recovery_codes


def test_totp_login_blocks_session_until_factor_and_prevents_replay(
    client: TestClient,
    user: User,
    db,
    monkeypatch,
) -> None:
    del user
    secret, recovery_codes = _enable_totp(client, db)

    status = client.get("/api/v1/settings/mfa")
    assert status.json() == {
        "totp_enabled": True,
        "totp_setup_pending": False,
        "recovery_codes_remaining": 10,
    }
    stored_hashes = set(db.scalars(select(MfaRecoveryCode.code_hash)))
    assert len(stored_hashes) == 10
    assert all(code.replace("-", "") not in stored_hashes for code in recovery_codes)

    client.cookies.clear()
    challenge = _login(client)
    assert challenge == {"mfa_required": True}
    challenge_cookie = client.cookies.get("calograph_mfa_challenge")
    assert challenge_cookie
    assert client.cookies.get("calograph_session") is None
    assert client.get("/api/v1/auth/me").status_code == 401
    set_cookie = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": PASSWORD},
    ).headers["set-cookie"].lower()
    assert "calograph_mfa_challenge=" in set_cookie
    assert "calograph_session=" not in set_cookie

    # The setup confirmation consumed the current time step. The configured
    # one-step clock-skew window lets us use the following step without sleep.
    following_code = pyotp.TOTP(secret).at(datetime.now(UTC) + timedelta(seconds=30))
    original_create_session = create_session
    outer_lock_observed = False

    def create_session_while_locked(session, target, now=None):
        nonlocal outer_lock_observed
        with (
            pytest.raises(UserOperationBusy),
            exclusive_user_lifecycle_operation(session, target.id),
        ):
            pass
        outer_lock_observed = True
        return original_create_session(session, target, now)

    monkeypatch.setattr("app.api.auth.create_session", create_session_while_locked)
    verified = client.post(
        "/api/v1/auth/mfa/totp/verify",
        json={"code": following_code},
    )
    assert verified.status_code == 200
    assert outer_lock_observed is True
    assert verified.json()["mfa_required"] is False
    assert "csrf_token" in verified.json()

    assert _login(client) == {"mfa_required": True}
    replay = client.post(
        "/api/v1/auth/mfa/totp/verify",
        json={"code": following_code},
    )
    assert replay.status_code == 401

    assert _login(client) == {"mfa_required": True}
    recovered = client.post(
        "/api/v1/auth/mfa/totp/verify",
        json={"code": recovery_codes[0]},
    )
    assert recovered.status_code == 200
    assert client.get("/api/v1/settings/mfa").json()["recovery_codes_remaining"] == 9

    assert _login(client) == {"mfa_required": True}
    reused_recovery = client.post(
        "/api/v1/auth/mfa/totp/verify",
        json={"code": recovery_codes[0]},
    )
    assert reused_recovery.status_code == 401


def test_mfa_failures_are_rate_limited_with_retry_after(
    client: TestClient,
    user: User,
    db,
    monkeypatch,
) -> None:
    del user
    _enable_totp(client, db)
    monkeypatch.setattr(settings, "mfa_rate_limit", 1)
    monkeypatch.setattr(settings, "mfa_ip_rate_limit", 100)

    assert _login(client) == {"mfa_required": True}
    first = client.post("/api/v1/auth/mfa/totp/verify", json={"code": "000000"})
    second = client.post("/api/v1/auth/mfa/totp/verify", json={"code": "000000"})

    assert first.status_code == 401
    assert second.status_code == 429
    assert int(second.headers["Retry-After"]) > 0


def test_production_mfa_challenge_cookie_is_host_only(
    client: TestClient,
    user: User,
    db,
    monkeypatch,
) -> None:
    del user
    _enable_totp(client, db)
    monkeypatch.setattr(settings, "cookie_secure", True)

    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": PASSWORD},
    )

    cookie = response.headers["set-cookie"].lower()
    assert response.json() == {"mfa_required": True}
    assert "__host-calograph_mfa_challenge=" in cookie
    assert "secure" in cookie
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert "path=/" in cookie
    assert "domain=" not in cookie
    assert "__host-calograph_session=" not in cookie


def test_recovery_codes_can_be_replaced_and_totp_can_be_disabled(
    client: TestClient,
    user: User,
    db,
) -> None:
    del user
    secret, recovery_codes = _enable_totp(client, db)
    csrf = client.get("/api/v1/auth/csrf").json()["csrf_token"]
    future_code = pyotp.TOTP(secret).at(datetime.now(UTC) + timedelta(seconds=30))

    replaced = client.post(
        "/api/v1/settings/mfa/totp/recovery-codes",
        headers={"X-CSRF-Token": csrf},
        json={"current_password": PASSWORD, "code": future_code},
    )
    assert replaced.status_code == 200
    replacement_codes = replaced.json()["recovery_codes"]
    assert len(replacement_codes) == 10
    assert set(replacement_codes).isdisjoint(recovery_codes)

    csrf = client.get("/api/v1/auth/csrf").json()["csrf_token"]
    disabled = client.request(
        "DELETE",
        "/api/v1/settings/mfa/totp",
        headers={"X-CSRF-Token": csrf},
        json={"current_password": PASSWORD, "code": replacement_codes[0]},
    )
    assert disabled.status_code == 204
    assert db.scalar(select(UserTotpCredential)) is None
    assert db.scalar(select(func.count(MfaRecoveryCode.id))) == 0
    assert _login(client)["mfa_required"] is False




def test_signed_mfa_login_state_rejects_tampering_and_expiry(user: User) -> None:
    now = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
    state = create_mfa_login_state(user.id, now)

    assert verify_mfa_login_state(state, now + timedelta(seconds=1)) == user.id
    assert verify_mfa_login_state(f"{state[:-1]}x", now + timedelta(seconds=1)) is None
    assert (
        verify_mfa_login_state(
            state,
            now + timedelta(seconds=MFA_LOGIN_STATE_TTL_SECONDS),
        )
        is None
    )


def test_mfa_secret_cannot_be_decrypted_with_a_different_key(
    client: TestClient,
    user: User,
    db,
    monkeypatch,
) -> None:
    del user
    _enable_totp(client, db)
    credential = db.scalar(select(UserTotpCredential))
    assert credential is not None
    monkeypatch.setattr(settings, "mfa_encryption_key", Fernet.generate_key().decode())

    try:
        decrypt_mfa_secret(credential.encrypted_secret)
    except MfaEncryptionError:
        pass
    else:
        raise AssertionError("MFA secret decrypted with an unrelated key")
