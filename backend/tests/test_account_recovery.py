import argparse
import json
import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import security_events
from app.auth.security import (
    create_api_token,
    create_session,
    hash_account_recovery_token,
    hash_mfa_recovery_code,
    hash_password,
    verify_password,
)
from app.cli import issue_recovery
from app.config import settings
from app.models import (
    AccountRecoveryToken,
    ApiToken,
    MfaRecoveryCode,
    NutritionTarget,
    PasskeyCredential,
    RateLimitBucket,
    User,
    UserSession,
    UserTotpCredential,
    WebAuthnChallenge,
    WebAuthnUserHandle,
    YazioConnection,
)
from app.services.account_recovery import complete_account_recovery, purge_account_recovery_tokens
from app.services.mfa_crypto import encrypt_mfa_secret
from app.services.user_lifecycle import (
    UserLifecycleRejected,
    issue_account_recovery,
    reactivate_user,
    reset_user_authenticators,
)

ADMIN_PASSWORD = "correct-horse-battery-staple"
NEW_PASSWORD = "new-unique-recovery-passphrase-2026"
TARGET_PASSWORD = "target-original-password-2026"


def _add_user(
    db: Session,
    username: str,
    *,
    is_admin: bool = False,
    is_active: bool = True,
) -> User:
    user = User(
        username=username,
        password_hash=hash_password(ADMIN_PASSWORD if is_admin else TARGET_PASSWORD),
        is_admin=is_admin,
        is_active=is_active,
        deactivated_at=None if is_active else datetime.now(UTC),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login(client: TestClient, username: str, password: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return str(response.json()["csrf_token"])


def _capture_security_events(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[int, str]]:
    records: list[tuple[int, str]] = []
    monkeypatch.setattr(
        security_events.logger,
        "log",
        lambda level, message: records.append((level, message)),
    )
    return records


def _issue_via_api(
    client: TestClient,
    actor: User,
    target: User,
    *,
    password: str = ADMIN_PASSWORD,
    code: str | None = None,
) -> tuple[str, datetime]:
    csrf = _login(client, actor.username, ADMIN_PASSWORD)
    response = client.post(
        f"/api/v1/users/{target.id}/recovery-links",
        headers={"X-CSRF-Token": csrf},
        json={"current_password": password, "code": code},
    )
    assert response.status_code == 201
    payload = response.json()
    return str(payload["recovery_token"]), datetime.fromisoformat(payload["expires_at"])


def _enable_admin_totp(db: Session, admin: User) -> tuple[str, str]:
    secret = pyotp.random_base32()
    recovery_code = "ABCD-EF01-2345-6789"
    db.add(
        UserTotpCredential(
            user_id=admin.id,
            encrypted_secret=encrypt_mfa_secret(secret),
            enabled_at=datetime.now(UTC),
        )
    )
    db.add(
        MfaRecoveryCode(
            user_id=admin.id,
            code_hash=hash_mfa_recovery_code(recovery_code.replace("-", "")),
        )
    )
    db.commit()
    return secret, recovery_code


def test_recovery_issue_deactivates_target_and_returns_only_raw_token_once(
    client: TestClient,
    user: User,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user.is_admin = True
    db.commit()
    target = _add_user(db, "recovery-issue-target")
    other = _add_user(db, "recovery-other-user")
    target_session, _, _ = create_session(db, target)
    target_api_token, _ = create_api_token(db, target, "recovery-target-token")
    other_session, _, _ = create_session(db, other)
    target_session_id = target_session.id
    target_api_token_id = target_api_token.id
    other_session_id = other_session.id
    db.add(
        YazioConnection(
            user_id=target.id,
            encrypted_email=b"encrypted-email",
            encrypted_password=b"encrypted-password",
            source_identifier=f"yazio:{target.id}",
            sync_enabled=True,
            sync_interval_minutes=360,
            sync_days=7,
            next_sync_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    db.commit()
    records = _capture_security_events(monkeypatch)

    raw_token, expires_at = _issue_via_api(client, user, target)
    db.expire_all()

    stored = db.scalar(select(AccountRecoveryToken).where(AccountRecoveryToken.user_id == target.id))
    assert stored is not None
    assert len(raw_token) >= 43
    assert stored.token_hash == hash_account_recovery_token(raw_token)
    assert raw_token != stored.token_hash
    assert (
        expires_at.astimezone(UTC)
        - stored.created_at.replace(tzinfo=stored.created_at.tzinfo or UTC)
        == timedelta(minutes=30)
    )
    assert db.get(User, target.id).is_active is False
    assert db.get(User, target.id).deactivated_at is not None
    assert db.get(UserSession, target_session_id) is None
    assert db.get(ApiToken, target_api_token_id).revoked_at is not None
    connection = db.scalar(
        select(YazioConnection).where(YazioConnection.user_id == target.id)
    )
    assert connection is not None
    assert connection.sync_enabled is False
    assert connection.next_sync_at is None
    assert db.get(UserSession, other_session_id) is not None
    serialized_events = "\n".join(message for _, message in records)
    assert raw_token not in serialized_events
    assert TARGET_PASSWORD not in serialized_events
    assert json.loads(records[-1][1])["event"] == "admin.user.recovery_issued"
    assert records[-1][0] == logging.WARNING


def test_second_recovery_issue_revokes_first_token(
    client: TestClient,
    user: User,
    db: Session,
) -> None:
    user.is_admin = True
    db.commit()
    target = _add_user(db, "recovery-reissue-target")

    first_raw, _ = _issue_via_api(client, user, target)
    second_raw, _ = _issue_via_api(client, user, target)
    db.expire_all()

    first = db.scalar(
        select(AccountRecoveryToken).where(
            AccountRecoveryToken.token_hash == hash_account_recovery_token(first_raw)
        )
    )
    second = db.scalar(
        select(AccountRecoveryToken).where(
            AccountRecoveryToken.token_hash == hash_account_recovery_token(second_raw)
        )
    )
    assert first is not None and first.revoked_at is not None
    assert second is not None and second.revoked_at is None and second.used_at is None
    assert first_raw != second_raw


def test_recovery_issue_requires_admin_reauthentication_and_blocks_self_target(
    client: TestClient,
    user: User,
    db: Session,
) -> None:
    target = _add_user(db, "recovery-authorization-target")
    csrf = _login(client, user.username, ADMIN_PASSWORD)
    non_admin = client.post(
        f"/api/v1/users/{target.id}/recovery-links",
        headers={"X-CSRF-Token": csrf},
        json={"current_password": ADMIN_PASSWORD},
    )
    assert non_admin.status_code == 403

    user.is_admin = True
    db.commit()
    wrong_password = client.post(
        f"/api/v1/users/{target.id}/recovery-links",
        headers={"X-CSRF-Token": csrf},
        json={"current_password": "wrong-admin-password"},
    )
    assert wrong_password.status_code == 400
    assert wrong_password.json()["detail"] == "Reauthentifizierung fehlgeschlagen"
    self_target = client.post(
        f"/api/v1/users/{user.id}/recovery-links",
        headers={"X-CSRF-Token": csrf},
        json={"current_password": ADMIN_PASSWORD},
    )
    assert self_target.status_code == 409
    db.expire_all()
    assert db.get(User, target.id).is_active is True
    assert db.scalar(select(func.count(AccountRecoveryToken.id))) == 0


def test_admin_reauthentication_requires_enabled_mfa_and_accepts_recovery_code(
    client: TestClient,
    user: User,
    db: Session,
) -> None:
    user.is_admin = True
    db.commit()
    target = _add_user(db, "recovery-mfa-target")
    csrf = _login(client, user.username, ADMIN_PASSWORD)
    secret, recovery_code = _enable_admin_totp(db, user)
    assert client.post(
        f"/api/v1/users/{target.id}/recovery-links",
        headers={"X-CSRF-Token": csrf},
        json={"current_password": ADMIN_PASSWORD},
    ).status_code == 400
    assert client.post(
        f"/api/v1/users/{target.id}/recovery-links",
        headers={"X-CSRF-Token": csrf},
        json={"current_password": ADMIN_PASSWORD, "code": "000000"},
    ).status_code == 400
    accepted = client.post(
        f"/api/v1/users/{target.id}/recovery-links",
        headers={"X-CSRF-Token": csrf},
        json={"current_password": ADMIN_PASSWORD, "code": recovery_code},
    )
    assert accepted.status_code == 201
    recovery = db.scalar(
        select(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user.id)
    )
    assert recovery is not None and recovery.used_at is not None
    assert pyotp.TOTP(secret).now()


def test_recovery_issue_rollback_emits_no_success_event(
    user: User,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user.is_admin = True
    target = _add_user(db, "recovery-rollback-target")
    records = _capture_security_events(monkeypatch)

    def fail_commit(_session: Session) -> None:
        raise RuntimeError("synthetic recovery commit failure")

    monkeypatch.setattr(Session, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="synthetic recovery commit failure"):
        issue_account_recovery(db, user.id, target.id)
    db.expire_all()
    assert db.get(User, target.id).is_active is True
    assert db.scalar(select(func.count(AccountRecoveryToken.id))) == 0
    assert [json.loads(message)["event"] for _, message in records] == [
        "admin.user.lifecycle_failed"
    ]


def test_recovery_completion_changes_password_but_keeps_account_inactive(
    client: TestClient,
    user: User,
    db: Session,
) -> None:
    user.is_admin = True
    db.commit()
    target = _add_user(db, "recovery-completion-target")
    recovery, raw_token = issue_account_recovery(db, user.id, target.id)

    completed = client.post(
        "/api/v1/auth/recovery/complete",
        json={"recovery_token": raw_token, "new_password": NEW_PASSWORD},
    )
    assert completed.status_code == 204
    db.expire_all()
    stored_target = db.get(User, target.id)
    stored_recovery = db.get(AccountRecoveryToken, recovery.id)
    assert stored_target is not None and stored_target.is_active is False
    assert verify_password(stored_target.password_hash, NEW_PASSWORD)
    assert stored_recovery is not None and stored_recovery.used_at is not None
    assert db.scalar(select(func.count(UserSession.id)).where(UserSession.user_id == target.id)) == 0
    blocked_login = client.post(
        "/api/v1/auth/login",
        json={"username": target.username, "password": NEW_PASSWORD},
    )
    assert blocked_login.status_code == 401

    reactivate_user(db, user.id, target.id)
    client.cookies.clear()
    assert client.post(
        "/api/v1/auth/login",
        json={"username": target.username, "password": NEW_PASSWORD},
    ).status_code == 200
    client.cookies.clear()
    assert client.post(
        "/api/v1/auth/login",
        json={"username": target.username, "password": TARGET_PASSWORD},
    ).status_code == 401


def test_recovery_completion_rollback_preserves_password_and_token(
    user: User,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user.is_admin = True
    db.commit()
    target = _add_user(db, "recovery-completion-rollback-target")
    original_hash = target.password_hash
    recovery, raw_token = issue_account_recovery(db, user.id, target.id)
    records = _capture_security_events(monkeypatch)

    def fail_commit(_session: Session) -> None:
        raise RuntimeError("synthetic recovery completion commit failure")

    monkeypatch.setattr(Session, "commit", fail_commit)
    with pytest.raises(
        RuntimeError,
        match="synthetic recovery completion commit failure",
    ):
        complete_account_recovery(db, raw_token, NEW_PASSWORD)

    db.expire_all()
    stored_target = db.get(User, target.id)
    stored_recovery = db.get(AccountRecoveryToken, recovery.id)
    assert stored_target is not None and stored_target.password_hash == original_hash
    assert stored_recovery is not None and stored_recovery.used_at is None
    assert [json.loads(message)["event"] for _, message in records] == [
        "auth.password.recovery_failed"
    ]


def test_recovery_completion_uses_existing_password_policy_without_consuming_token(
    client: TestClient,
    user: User,
    db: Session,
) -> None:
    user.is_admin = True
    db.commit()
    target = _add_user(db, "recovery-policy-target")
    recovery, raw_token = issue_account_recovery(db, user.id, target.id)

    rejected = client.post(
        "/api/v1/auth/recovery/complete",
        json={"recovery_token": raw_token, "new_password": "too-short"},
    )
    assert rejected.status_code == 422
    db.expire_all()
    assert db.get(AccountRecoveryToken, recovery.id).used_at is None
    common = client.post(
        "/api/v1/auth/recovery/complete",
        json={"recovery_token": raw_token, "new_password": "123456789qwerty"},
    )
    assert common.status_code == 422
    db.expire_all()
    assert db.get(AccountRecoveryToken, recovery.id).used_at is None
    assert client.post(
        "/api/v1/auth/recovery/complete",
        json={"recovery_token": raw_token, "new_password": NEW_PASSWORD},
    ).status_code == 204


def test_invalid_recovery_tokens_have_uniform_response_and_no_secret_leakage(
    client: TestClient,
    user: User,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user.is_admin = True
    db.commit()
    now = datetime.now(UTC)
    target = _add_user(db, "invalid-recovery-target", is_active=False)
    raw_values = {
        "expired": "expired-recovery-token-value-00000001",
        "revoked": "revoked-recovery-token-value-00000001",
        "used": "used-recovery-token-value-00000000001",
    }
    db.add_all(
        [
            AccountRecoveryToken(
                user_id=target.id,
                token_hash=hash_account_recovery_token(raw_values["expired"]),
                expires_at=now - timedelta(seconds=1),
            ),
            AccountRecoveryToken(
                user_id=target.id,
                token_hash=hash_account_recovery_token(raw_values["revoked"]),
                expires_at=now + timedelta(minutes=30),
                revoked_at=now,
            ),
            AccountRecoveryToken(
                user_id=target.id,
                token_hash=hash_account_recovery_token(raw_values["used"]),
                expires_at=now + timedelta(minutes=30),
                used_at=now,
            ),
        ]
    )
    db.commit()
    records = _capture_security_events(monkeypatch)
    invalid_values = [
        "wrong-recovery-token-value-00000000001",
        "manipulated-recovery-token-value-000001",
        *raw_values.values(),
    ]
    responses = [
        client.post(
            "/api/v1/auth/recovery/complete",
            json={"recovery_token": raw, "new_password": NEW_PASSWORD},
        )
        for raw in invalid_values
    ]
    assert {(response.status_code, response.json()["detail"]) for response in responses} == {
        (400, "Recovery-Token ist ungültig oder abgelaufen")
    }
    serialized = "\n".join(message for _, message in records)
    for raw in invalid_values:
        assert raw not in serialized
    assert NEW_PASSWORD not in serialized


def test_recovery_completion_is_rate_limited_by_ip_and_hashed_token_key(
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "recovery_ip_rate_limit", 1)
    first_token = "first-invalid-recovery-token-00000000001"
    first = client.post(
        "/api/v1/auth/recovery/complete",
        json={"recovery_token": first_token, "new_password": NEW_PASSWORD},
    )
    limited = client.post(
        "/api/v1/auth/recovery/complete",
        json={
            "recovery_token": "second-invalid-recovery-token-0000000001",
            "new_password": NEW_PASSWORD,
        },
    )
    assert first.status_code == 400
    assert limited.status_code == 429
    buckets = list(db.scalars(select(RateLimitBucket)))
    assert buckets
    assert all(len(bucket.key_hash) == 64 for bucket in buckets)
    assert first_token not in repr(buckets)


def test_authenticator_reset_removes_only_authenticators_and_revokes_credentials(
    client: TestClient,
    user: User,
    db: Session,
) -> None:
    user.is_admin = True
    db.commit()
    target = _add_user(db, "authenticator-reset-target", is_active=False)
    other = _add_user(db, "authenticator-reset-other", is_active=False)
    original_hash = target.password_hash
    target_session = UserSession(
        user_id=target.id,
        token_hash="a" * 64,
        csrf_hash="b" * 64,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    target_token = ApiToken(
        user_id=target.id,
        label="target-reset-token",
        token_prefix="target-reset",
        token_hash="c" * 64,
    )
    db.add_all(
        [
            target_session,
            target_token,
            UserTotpCredential(
                user_id=target.id,
                encrypted_secret=b"encrypted-secret",
                enabled_at=datetime.now(UTC),
            ),
            MfaRecoveryCode(user_id=target.id, code_hash="1" * 64),
            WebAuthnUserHandle(user_id=target.id, user_handle=b"target-handle"),
            PasskeyCredential(
                user_id=target.id,
                label="target-passkey",
                credential_id=b"target-credential",
                public_key=b"target-public-key",
                transports=[],
                device_type="single_device",
            ),
            WebAuthnChallenge(
                user_id=target.id,
                purpose="passkey-authentication",
                challenge=b"target-challenge",
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            ),
            NutritionTarget(
                user_id=target.id,
                valid_from=date(2026, 1, 1),
                calories_kcal=Decimal("2200"),
                protein_g=Decimal("140"),
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
            WebAuthnUserHandle(user_id=other.id, user_handle=b"other-handle"),
        ]
    )
    db.commit()
    target_session_id = target_session.id
    target_token_id = target_token.id
    csrf = _login(client, user.username, ADMIN_PASSWORD)

    response = client.post(
        f"/api/v1/users/{target.id}/authenticators/reset",
        headers={"X-CSRF-Token": csrf},
        json={"current_password": ADMIN_PASSWORD},
    )
    assert response.status_code == 204
    db.expire_all()
    assert db.get(UserTotpCredential, target.id) is None
    assert db.scalar(select(MfaRecoveryCode).where(MfaRecoveryCode.user_id == target.id)) is None
    assert db.scalar(select(PasskeyCredential).where(PasskeyCredential.user_id == target.id)) is None
    assert db.get(WebAuthnUserHandle, target.id) is None
    assert db.scalar(select(WebAuthnChallenge).where(WebAuthnChallenge.user_id == target.id)) is None
    assert db.get(UserSession, target_session_id) is None
    assert db.get(ApiToken, target_token_id).revoked_at is not None
    stored_target = db.get(User, target.id)
    assert stored_target is not None and stored_target.is_active is False
    assert stored_target.password_hash == original_hash
    assert db.scalar(select(NutritionTarget).where(NutritionTarget.user_id == target.id)) is not None
    assert db.scalar(select(YazioConnection).where(YazioConnection.user_id == target.id)) is not None
    assert db.get(WebAuthnUserHandle, other.id) is not None


def test_authenticator_reset_requires_inactive_target_and_rolls_back_completely(
    user: User,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user.is_admin = True
    active = _add_user(db, "active-authenticator-target")
    with pytest.raises(UserLifecycleRejected, match="target_active"):
        reset_user_authenticators(db, user.id, active.id)

    target = _add_user(db, "rollback-authenticator-target", is_active=False)
    credential = UserTotpCredential(
        user_id=target.id,
        encrypted_secret=b"encrypted-secret",
        enabled_at=datetime.now(UTC),
    )
    db.add(credential)
    db.commit()

    def fail_commit(_session: Session) -> None:
        raise RuntimeError("synthetic authenticator reset failure")

    monkeypatch.setattr(Session, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="synthetic authenticator reset failure"):
        reset_user_authenticators(db, user.id, target.id)
    db.expire_all()
    assert db.get(UserTotpCredential, target.id) is not None


def test_hard_delete_api_requires_reauthentication_mfa_and_exact_confirmation(
    client: TestClient,
    user: User,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user.is_admin = True
    db.commit()
    target = _add_user(db, "hard-delete-reauth-target", is_active=False)
    csrf = _login(client, user.username, ADMIN_PASSWORD)
    secret, _ = _enable_admin_totp(db, user)
    records = _capture_security_events(monkeypatch)
    url = f"/api/v1/users/{target.id}"

    target_id = target.id
    assert client.request("DELETE", url, headers={"X-CSRF-Token": csrf}).status_code == 422
    wrong_password = client.request(
        "DELETE",
        url,
        headers={"X-CSRF-Token": csrf},
        json={
            "current_password": "wrong-admin-password",
            "confirm_username": target.username,
        },
    )
    missing_mfa = client.request(
        "DELETE",
        url,
        headers={"X-CSRF-Token": csrf},
        json={
            "current_password": ADMIN_PASSWORD,
            "confirm_username": target.username,
        },
    )
    wrong_confirmation = client.request(
        "DELETE",
        url,
        headers={"X-CSRF-Token": csrf},
        json={
            "current_password": ADMIN_PASSWORD,
            "code": pyotp.TOTP(secret).now(),
            "confirm_username": "wrong-target-name",
        },
    )
    assert wrong_password.status_code == 400
    assert missing_mfa.status_code == 400
    assert wrong_confirmation.status_code == 409
    db.expire_all()
    assert db.get(User, target.id) is not None
    assert not any(json.loads(message)["event"] == "admin.user.deleted" for _, message in records)

    next_code = pyotp.TOTP(secret).at(datetime.now(UTC) + timedelta(seconds=30))
    deleted = client.request(
        "DELETE",
        url,
        headers={"X-CSRF-Token": csrf},
        json={
            "current_password": ADMIN_PASSWORD,
            "code": next_code,
            "confirm_username": target.username,
        },
    )
    assert deleted.status_code == 204
    db.expire_all()
    assert db.get(User, target_id) is None


def test_recovery_cleanup_keeps_open_valid_tokens(db: Session) -> None:
    target = _add_user(db, "recovery-cleanup-target", is_active=False)
    now = datetime(2026, 8, 10, 12, tzinfo=UTC)
    tokens = [
        AccountRecoveryToken(
            user_id=target.id,
            token_hash=f"{index:064x}",
            created_at=now - timedelta(days=3),
            expires_at=(now - timedelta(days=2) if index == 1 else now + timedelta(minutes=30)),
            used_at=now - timedelta(days=2) if index == 2 else None,
            revoked_at=now - timedelta(days=2) if index == 3 else None,
        )
        for index in range(1, 5)
    ]
    db.add_all(tokens)
    db.commit()

    assert purge_account_recovery_tokens(db, now=now) == 3
    assert db.get(AccountRecoveryToken, tokens[3].id) is not None


def test_issue_recovery_cli_prints_raw_token_once_and_uses_central_reauth(
    user: User,
    db: Session,
    capsys: pytest.CaptureFixture[str],
) -> None:
    user.is_admin = True
    db.commit()
    target = _add_user(db, "recovery-cli-target")
    issue_recovery(
        argparse.Namespace(
            username=target.username,
            admin_username=user.username,
            admin_password=ADMIN_PASSWORD,
            code="",
        )
    )
    output = capsys.readouterr().out
    db.expire_all()
    stored = db.scalar(select(AccountRecoveryToken).where(AccountRecoveryToken.user_id == target.id))
    assert stored is not None
    raw_line = next(
        line
        for line in output.splitlines()
        if hash_account_recovery_token(line) == stored.token_hash
    )
    assert output.count(raw_line) == 1
    assert raw_line not in stored.token_hash
