import argparse
import base64
import json
import logging
import re
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import security_events
from app.api import settings as settings_api
from app.auth import security
from app.auth.security import create_api_token, hash_password, verify_password
from app.cli import create_token, create_user, reset_authenticators
from app.models import (
    ApiToken,
    MfaRecoveryCode,
    NutritionTarget,
    PasskeyCredential,
    User,
    UserInvitation,
    UserSession,
    UserTotpCredential,
    WebAuthnChallenge,
)
from app.security_events import security_reference
from app.services.passkeys import PasskeyRegistrationError
from app.services.rate_limit import rate_limit_key_id

REFERENCE_PATTERN = re.compile(r"^[a-f0-9]{16}$")
REQUEST_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")
CURRENT_PASSWORD = "PRIVATE-current-password-47!safe"
NEW_PASSWORD = "PRIVATE-new-password-83!safe"
WRONG_PASSWORD = "PRIVATE-wrong-password-29!safe"
CREDENTIAL_ID = b"PRIVATE_PASSKEY_CREDENTIAL_SENTINEL"
PUBLIC_KEY = b"PRIVATE_PASSKEY_PUBLIC_KEY_SENTINEL"
CLIENT_DATA = b"PRIVATE_PASSKEY_CLIENT_DATA_SENTINEL"
ATTESTATION = b"PRIVATE_PASSKEY_ATTESTATION_SENTINEL"


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


def _event_payloads(records: list[tuple[int, str]]) -> list[dict[str, object]]:
    return [json.loads(message) for _, message in records]


def _contract_fields(payload: dict[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in payload.items()
        if key not in {"timestamp", "request_id", "client_ref"}
    }


def _assert_pseudonymous_references(payloads: list[dict[str, object]]) -> None:
    for payload in payloads:
        for field in ("actor_ref", "client_ref", "target_ref"):
            value = payload.get(field)
            if value is not None:
                assert isinstance(value, str)
                assert REFERENCE_PATTERN.fullmatch(value)


def _assert_http_metadata(payloads: list[dict[str, object]]) -> None:
    for payload in payloads:
        request_id = payload.get("request_id")
        assert isinstance(request_id, str)
        assert REQUEST_ID_PATTERN.fullmatch(request_id)
        assert "client_ref" in payload
    _assert_pseudonymous_references(payloads)


def _assert_cli_metadata(payloads: list[dict[str, object]]) -> None:
    for payload in payloads:
        assert "request_id" not in payload
        assert "client_ref" not in payload
    _assert_pseudonymous_references(payloads)


def _assert_sentinels_absent(
    records: list[tuple[int, str]],
    *sentinels: object,
) -> None:
    serialized = "\n".join(message for _, message in records)
    for sentinel in sentinels:
        assert str(sentinel) not in serialized


def _set_user_identity(db: Session, user: User, username: str) -> None:
    user.username = username
    user.password_hash = hash_password(CURRENT_PASSWORD)
    db.commit()
    db.refresh(user)


def _login(client: TestClient, username: str) -> tuple[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": CURRENT_PASSWORD},
    )
    assert response.status_code == 200
    csrf_token = str(response.json()["csrf_token"])
    session_cookie = client.cookies.get("calograph_session")
    assert session_cookie
    return csrf_token, session_cookie


def _session_sentinels(db: Session, user_id: object) -> list[object]:
    sessions = list(db.scalars(select(UserSession).where(UserSession.user_id == user_id)))
    return [value for session in sessions for value in (session.id, session.token_hash)]


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _registration_credential() -> dict[str, object]:
    credential_id = _base64url(CREDENTIAL_ID)
    return {
        "id": credential_id,
        "rawId": credential_id,
        "response": {
            "clientDataJSON": _base64url(CLIENT_DATA),
            "attestationObject": _base64url(ATTESTATION),
            "transports": ["internal"],
        },
        "authenticatorAttachment": "platform",
        "clientExtensionResults": {},
        "type": "public-key",
    }


def _mock_registration_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.passkeys.verify_registration_response",
        lambda **_: SimpleNamespace(
            credential_id=CREDENTIAL_ID,
            credential_public_key=PUBLIC_KEY,
            sign_count=0,
            credential_device_type=SimpleNamespace(value="single_device"),
            credential_backed_up=False,
        ),
    )


def test_api_token_mutations_and_logout_emit_only_committed_safe_events(
    client: TestClient,
    db: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    username = "PRIVATE.token.owner"
    token_label = "private.token.owner@example.invalid"
    other_username = "PRIVATE.other.token.owner"
    other_token_label = "private.other.token.owner@example.invalid"
    commit_failure = "PRIVATE_TOKEN_COMMIT_FAILURE_SENTINEL"
    other_user = User(username=other_username, password_hash=hash_password(WRONG_PASSWORD))
    db.add(other_user)
    db.commit()
    db.refresh(other_user)
    other_password_hash = other_user.password_hash
    other_token, other_raw_token = create_api_token(db, other_user, other_token_label)
    other_token_hash = other_token.token_hash
    other_token_prefix = other_token.token_prefix
    _set_user_identity(db, user, username)
    password_hash = user.password_hash
    csrf_token, session_cookie = _login(client, username)
    session_values = _session_sentinels(db, user.id)
    records = _capture_security_events(monkeypatch)

    created = client.post(
        "/api/v1/settings/tokens",
        headers={"X-CSRF-Token": csrf_token},
        json={"label": token_label},
    )
    assert created.status_code == 201
    raw_token = created.json()["token"]
    token_id = UUID(created.json()["id"])
    token = db.get(ApiToken, token_id)
    assert token is not None
    token_hash = token.token_hash
    token_prefix = token.token_prefix

    missing = client.delete(
        f"/api/v1/settings/tokens/{uuid4()}",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert missing.status_code == 404
    foreign = client.delete(
        f"/api/v1/settings/tokens/{other_token.id}",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert foreign.status_code == 404

    def fail_commit(self: Session) -> None:
        raise RuntimeError(commit_failure)

    with monkeypatch.context() as commit_patch:
        commit_patch.setattr(Session, "commit", fail_commit)
        with pytest.raises(RuntimeError, match=commit_failure):
            client.delete(
                f"/api/v1/settings/tokens/{token_id}",
                headers={"X-CSRF-Token": csrf_token},
            )
    db.rollback()
    db.expire_all()
    failed_token = db.get(ApiToken, token_id)
    assert failed_token is not None and failed_token.revoked_at is None

    revoked = client.delete(
        f"/api/v1/settings/tokens/{token_id}",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert revoked.status_code == 204
    logged_out = client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert logged_out.status_code == 204

    db.expire_all()
    stored_token = db.get(ApiToken, token_id)
    assert stored_token is not None and stored_token.revoked_at is not None
    stored_other_token = db.get(ApiToken, other_token.id)
    assert stored_other_token is not None and stored_other_token.revoked_at is None
    assert all(session.revoked_at is not None for session in db.scalars(select(UserSession)))
    payloads = _event_payloads(records)
    actor_ref = security_reference("user", user.id)
    target_ref = security_reference("api_token", token_id)
    assert [level for level, _ in records] == [
        logging.INFO,
        logging.ERROR,
        logging.INFO,
        logging.INFO,
    ]
    assert [_contract_fields(payload) for payload in payloads] == [
        {
            "event": "auth.api_token.created",
            "outcome": "success",
            "actor_ref": actor_ref,
            "target_ref": target_ref,
        },
        {
            "event": "request.failed",
            "outcome": "failure",
            "reason": "unhandled_exception",
        },
        {
            "event": "auth.api_token.revoked",
            "outcome": "success",
            "actor_ref": actor_ref,
            "target_ref": target_ref,
        },
        {
            "event": "auth.session.logged_out",
            "outcome": "success",
            "actor_ref": actor_ref,
        },
    ]
    _assert_http_metadata(payloads)
    _assert_sentinels_absent(
        records,
        username,
        CURRENT_PASSWORD,
        password_hash,
        other_username,
        WRONG_PASSWORD,
        other_password_hash,
        token_label,
        raw_token,
        token_hash,
        token_prefix,
        other_token_label,
        other_raw_token,
        other_token_hash,
        other_token_prefix,
        user.id,
        token_id,
        other_user.id,
        other_token.id,
        commit_failure,
        csrf_token,
        session_cookie,
        *session_values,
    )


def test_invitation_and_registration_callsites_preserve_actor_target_and_secrets(
    client: TestClient,
    db: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin_username = "PRIVATE.invitation.admin"
    invited_username = "PRIVATE.invited.user"
    invited_password = "PRIVATE-invited-password-71!safe"
    _set_user_identity(db, user, admin_username)
    user.is_admin = True
    db.commit()
    admin_password_hash = user.password_hash
    csrf_token, session_cookie = _login(client, admin_username)
    session_values = _session_sentinels(db, user.id)
    records = _capture_security_events(monkeypatch)

    first_created = client.post(
        "/api/v1/users/invitations",
        headers={"X-CSRF-Token": csrf_token},
        json={"expires_in_days": 7},
    )
    assert first_created.status_code == 201
    first_id = UUID(first_created.json()["id"])
    first_url = first_created.json()["invitation_url"]
    first_raw_token = first_url.partition("#token=")[2]
    first_invitation = db.get(UserInvitation, first_id)
    assert first_invitation is not None
    first_original_hash = first_invitation.token_hash

    exchanged = client.post(
        "/api/v1/auth/invitation/exchange",
        json={"token": first_raw_token},
    )
    assert exchanged.status_code == 204
    registration_cookie = client.cookies.get("calograph_registration")
    assert registration_cookie
    db.expire_all()
    first_invitation = db.get(UserInvitation, first_id)
    assert first_invitation is not None
    first_exchanged_hash = first_invitation.token_hash

    rejected = client.post(
        "/api/v1/auth/invitation/exchange",
        json={"token": first_raw_token},
    )
    assert rejected.status_code == 400
    registered = client.post(
        "/api/v1/auth/register",
        json={"username": invited_username, "password": invited_password},
    )
    assert registered.status_code == 201
    invited_user_id = UUID(registered.json()["id"])
    invited_user = db.get(User, invited_user_id)
    assert invited_user is not None
    invited_password_hash = invited_user.password_hash

    second_created = client.post(
        "/api/v1/users/invitations",
        headers={"X-CSRF-Token": csrf_token},
        json={"expires_in_days": 7},
    )
    assert second_created.status_code == 201
    second_id = UUID(second_created.json()["id"])
    second_url = second_created.json()["invitation_url"]
    second_raw_token = second_url.partition("#token=")[2]
    second_invitation = db.get(UserInvitation, second_id)
    assert second_invitation is not None
    second_token_hash = second_invitation.token_hash

    missing_revoke = client.delete(
        f"/api/v1/users/invitations/{uuid4()}",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert missing_revoke.status_code == 404
    revoked = client.delete(
        f"/api/v1/users/invitations/{second_id}",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert revoked.status_code == 204

    db.expire_all()
    assert db.get(UserInvitation, first_id).used_at is not None  # type: ignore[union-attr]
    assert db.get(UserInvitation, second_id).revoked_at is not None  # type: ignore[union-attr]
    payloads = _event_payloads(records)
    admin_ref = security_reference("user", user.id)
    invited_ref = security_reference("user", invited_user_id)
    first_ref = security_reference("invitation", first_id)
    second_ref = security_reference("invitation", second_id)
    assert [level for level, _ in records] == [
        logging.INFO,
        logging.INFO,
        logging.WARNING,
        logging.INFO,
        logging.INFO,
        logging.INFO,
    ]
    assert [_contract_fields(payload) for payload in payloads] == [
        {
            "event": "auth.invitation.created",
            "outcome": "success",
            "actor_ref": admin_ref,
            "target_ref": first_ref,
        },
        {
            "event": "auth.invitation.exchanged",
            "outcome": "success",
            "target_ref": first_ref,
        },
        {
            "event": "auth.invitation.rejected",
            "outcome": "failure",
            "reason": "invalid_or_expired",
        },
        {
            "event": "auth.registration.succeeded",
            "outcome": "success",
            "actor_ref": invited_ref,
            "target_ref": first_ref,
        },
        {
            "event": "auth.invitation.created",
            "outcome": "success",
            "actor_ref": admin_ref,
            "target_ref": second_ref,
        },
        {
            "event": "auth.invitation.revoked",
            "outcome": "success",
            "actor_ref": admin_ref,
            "target_ref": second_ref,
        },
    ]
    _assert_http_metadata(payloads)
    _assert_sentinels_absent(
        records,
        admin_username,
        CURRENT_PASSWORD,
        admin_password_hash,
        invited_username,
        invited_password,
        invited_password_hash,
        first_url,
        first_raw_token,
        first_original_hash,
        first_exchanged_hash,
        second_url,
        second_raw_token,
        second_token_hash,
        user.id,
        invited_user_id,
        first_id,
        second_id,
        csrf_token,
        session_cookie,
        registration_cookie,
        *session_values,
    )


def test_password_change_events_distinguish_failure_from_committed_success(
    client: TestClient,
    db: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    username = "PRIVATE.password.owner"
    invalid_new_password = "short-password"
    _set_user_identity(db, user, username)
    old_password_hash = user.password_hash
    csrf_token, session_cookie = _login(client, username)
    session_values = _session_sentinels(db, user.id)
    records = _capture_security_events(monkeypatch)

    wrong_current = client.post(
        "/api/v1/auth/password",
        headers={"X-CSRF-Token": csrf_token},
        json={"current_password": WRONG_PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert wrong_current.status_code == 400
    policy_rejected = client.post(
        "/api/v1/auth/password",
        headers={"X-CSRF-Token": csrf_token},
        json={"current_password": CURRENT_PASSWORD, "new_password": invalid_new_password},
    )
    assert policy_rejected.status_code == 422
    changed = client.post(
        "/api/v1/auth/password",
        headers={"X-CSRF-Token": csrf_token},
        json={"current_password": CURRENT_PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert changed.status_code == 204

    db.expire_all()
    stored_user = db.get(User, user.id)
    assert stored_user is not None
    assert verify_password(stored_user.password_hash, NEW_PASSWORD)
    assert db.scalar(select(func.count(UserSession.id))) == 0
    new_password_hash = stored_user.password_hash
    payloads = _event_payloads(records)
    actor_ref = rate_limit_key_id(f"user:{user.id}")
    assert [level for level, _ in records] == [logging.WARNING, logging.WARNING]
    assert [_contract_fields(payload) for payload in payloads] == [
        {
            "event": "auth.password.change_failed",
            "outcome": "failure",
            "actor_ref": actor_ref,
            "reason": "invalid_current_password",
        },
        {
            "event": "auth.password.changed",
            "outcome": "success",
            "actor_ref": actor_ref,
        },
    ]
    _assert_http_metadata(payloads)
    _assert_sentinels_absent(
        records,
        username,
        CURRENT_PASSWORD,
        WRONG_PASSWORD,
        NEW_PASSWORD,
        invalid_new_password,
        old_password_hash,
        new_password_hash,
        user.id,
        csrf_token,
        session_cookie,
        *session_values,
    )


def test_mfa_mutation_events_exclude_secrets_codes_and_recovery_material(
    client: TestClient,
    db: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    username = "PRIVATE.mfa.owner"
    invalid_totp = "NOTOTP"
    _set_user_identity(db, user, username)
    password_hash = user.password_hash
    csrf_token, session_cookie = _login(client, username)
    session_values = _session_sentinels(db, user.id)
    records = _capture_security_events(monkeypatch)

    rejected_setup = client.post(
        "/api/v1/settings/mfa/totp/setup",
        headers={"X-CSRF-Token": csrf_token},
        json={"current_password": WRONG_PASSWORD},
    )
    assert rejected_setup.status_code == 400
    setup = client.post(
        "/api/v1/settings/mfa/totp/setup",
        headers={"X-CSRF-Token": csrf_token},
        json={"current_password": CURRENT_PASSWORD},
    )
    assert setup.status_code == 200
    secret = setup.json()["secret"]
    provisioning_uri = setup.json()["provisioning_uri"]
    qr_data = setup.json()["qr_svg_data_url"]
    credential = db.get(UserTotpCredential, user.id)
    assert credential is not None
    encrypted_secret = credential.encrypted_secret.decode()

    invalid_confirmation = client.post(
        "/api/v1/settings/mfa/totp/confirm",
        headers={"X-CSRF-Token": csrf_token},
        json={"code": invalid_totp},
    )
    assert invalid_confirmation.status_code == 400
    totp_code = pyotp.TOTP(secret).now()
    confirmation = client.post(
        "/api/v1/settings/mfa/totp/confirm",
        headers={"X-CSRF-Token": csrf_token},
        json={"code": totp_code},
    )
    assert confirmation.status_code == 200
    original_recovery_codes = confirmation.json()["recovery_codes"]
    original_recovery_hashes = list(db.scalars(select(MfaRecoveryCode.code_hash)))

    replacement = client.post(
        "/api/v1/settings/mfa/totp/recovery-codes",
        headers={"X-CSRF-Token": csrf_token},
        json={"current_password": CURRENT_PASSWORD, "code": original_recovery_codes[0]},
    )
    assert replacement.status_code == 200
    replacement_codes = replacement.json()["recovery_codes"]
    replacement_hashes = list(db.scalars(select(MfaRecoveryCode.code_hash)))
    disabled = client.request(
        "DELETE",
        "/api/v1/settings/mfa/totp",
        headers={"X-CSRF-Token": csrf_token},
        json={"current_password": CURRENT_PASSWORD, "code": replacement_codes[0]},
    )
    assert disabled.status_code == 204

    db.expire_all()
    assert db.get(UserTotpCredential, user.id) is None
    assert db.scalar(select(func.count(MfaRecoveryCode.id))) == 0
    payloads = _event_payloads(records)
    actor_ref = security_reference("user", user.id)
    assert [level for level, _ in records] == [
        logging.INFO,
        logging.INFO,
        logging.WARNING,
        logging.WARNING,
    ]
    assert [_contract_fields(payload) for payload in payloads] == [
        {
            "event": "auth.mfa.totp_setup_started",
            "outcome": "pending",
            "actor_ref": actor_ref,
        },
        {
            "event": "auth.mfa.totp_enabled",
            "outcome": "success",
            "actor_ref": actor_ref,
        },
        {
            "event": "auth.mfa.recovery_codes_replaced",
            "outcome": "success",
            "actor_ref": actor_ref,
        },
        {
            "event": "auth.mfa.totp_disabled",
            "outcome": "success",
            "actor_ref": actor_ref,
        },
    ]
    _assert_http_metadata(payloads)
    _assert_sentinels_absent(
        records,
        username,
        CURRENT_PASSWORD,
        WRONG_PASSWORD,
        password_hash,
        secret,
        encrypted_secret,
        provisioning_uri,
        qr_data,
        invalid_totp,
        totp_code,
        user.id,
        csrf_token,
        session_cookie,
        *session_values,
        *original_recovery_codes,
        *original_recovery_hashes,
        *replacement_codes,
        *replacement_hashes,
    )


def test_passkey_callsites_emit_after_success_without_credential_or_challenge_data(
    client: TestClient,
    db: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    username = "PRIVATE.passkey.owner"
    passkey_label = "private.passkey.owner@example.invalid"
    exception_sentinel = "PRIVATE_PASSKEY_EXCEPTION_SENTINEL"
    _set_user_identity(db, user, username)
    password_hash = user.password_hash
    csrf_token, session_cookie = _login(client, username)
    session_values = _session_sentinels(db, user.id)
    records = _capture_security_events(monkeypatch)

    rejected_options = client.post(
        "/api/v1/settings/passkeys/options",
        headers={"X-CSRF-Token": csrf_token},
        json={"current_password": WRONG_PASSWORD},
    )
    assert rejected_options.status_code == 400
    options = client.post(
        "/api/v1/settings/passkeys/options",
        headers={"X-CSRF-Token": csrf_token},
        json={"current_password": CURRENT_PASSWORD},
    )
    assert options.status_code == 200
    challenge_id = UUID(options.json()["challenge_id"])
    challenge = db.get(WebAuthnChallenge, challenge_id)
    assert challenge is not None
    challenge_bytes = _base64url(challenge.challenge)
    challenge_session_id = challenge.session_id
    credential_payload = _registration_credential()

    original_complete = settings_api.complete_passkey_registration

    def fail_registration(*args, **kwargs):
        raise PasskeyRegistrationError(exception_sentinel)

    monkeypatch.setattr(settings_api, "complete_passkey_registration", fail_registration)
    failed_registration = client.post(
        "/api/v1/settings/passkeys",
        headers={"X-CSRF-Token": csrf_token},
        json={
            "challenge_id": str(challenge_id),
            "label": passkey_label,
            "credential": credential_payload,
        },
    )
    assert failed_registration.status_code == 400
    monkeypatch.setattr(settings_api, "complete_passkey_registration", original_complete)
    _mock_registration_verification(monkeypatch)
    registered = client.post(
        "/api/v1/settings/passkeys",
        headers={"X-CSRF-Token": csrf_token},
        json={
            "challenge_id": str(challenge_id),
            "label": passkey_label,
            "credential": credential_payload,
        },
    )
    assert registered.status_code == 201
    passkey_id = UUID(registered.json()["id"])
    passkey = db.get(PasskeyCredential, passkey_id)
    assert passkey is not None

    replay = client.post(
        "/api/v1/settings/passkeys",
        headers={"X-CSRF-Token": csrf_token},
        json={
            "challenge_id": str(challenge_id),
            "label": passkey_label,
            "credential": credential_payload,
        },
    )
    assert replay.status_code == 400
    rejected_removal = client.request(
        "DELETE",
        f"/api/v1/settings/passkeys/{passkey_id}",
        headers={"X-CSRF-Token": csrf_token},
        json={"current_password": WRONG_PASSWORD},
    )
    assert rejected_removal.status_code == 400
    removed = client.request(
        "DELETE",
        f"/api/v1/settings/passkeys/{passkey_id}",
        headers={"X-CSRF-Token": csrf_token},
        json={"current_password": CURRENT_PASSWORD},
    )
    assert removed.status_code == 204

    db.expire_all()
    assert db.get(PasskeyCredential, passkey_id) is None
    payloads = _event_payloads(records)
    actor_ref = security_reference("user", user.id)
    target_ref = security_reference("passkey", passkey_id)
    assert [level for level, _ in records] == [logging.INFO, logging.INFO, logging.WARNING]
    assert [_contract_fields(payload) for payload in payloads] == [
        {
            "event": "auth.passkey.registration_started",
            "outcome": "pending",
            "actor_ref": actor_ref,
        },
        {
            "event": "auth.passkey.registered",
            "outcome": "success",
            "actor_ref": actor_ref,
            "target_ref": target_ref,
        },
        {
            "event": "auth.passkey.removed",
            "outcome": "success",
            "actor_ref": actor_ref,
            "target_ref": target_ref,
        },
    ]
    _assert_http_metadata(payloads)
    _assert_sentinels_absent(
        records,
        username,
        CURRENT_PASSWORD,
        WRONG_PASSWORD,
        password_hash,
        passkey_label,
        exception_sentinel,
        user.id,
        passkey_id,
        challenge_id,
        challenge_session_id,
        challenge_bytes,
        CREDENTIAL_ID,
        _base64url(CREDENTIAL_ID),
        PUBLIC_KEY,
        _base64url(PUBLIC_KEY),
        CLIENT_DATA,
        _base64url(CLIENT_DATA),
        ATTESTATION,
        _base64url(ATTESTATION),
        csrf_token,
        session_cookie,
        *session_values,
    )


def test_cli_user_token_and_admin_mfa_reset_emit_only_post_commit_references(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    username = "PRIVATE.cli.owner"
    password = "PRIVATE-cli-password-91!safe"
    token_label = "private.cli.owner@example.invalid"
    records = _capture_security_events(monkeypatch)
    create_args = argparse.Namespace(
        username=username,
        password=password,
        timezone="Europe/Berlin",
        raw_retention_days=0,
        admin=True,
        if_not_exists=False,
    )
    guessable_username = "PRIVATE.cli.guessable"
    with pytest.raises(SystemExit, match="Wiederholungs- oder Sequenzmuster"):
        create_user(
            argparse.Namespace(
                username=guessable_username,
                password="testtesttesttest1",
                timezone="Europe/Berlin",
                raw_retention_days=0,
                admin=False,
                if_not_exists=False,
            )
        )
    assert db.scalar(select(User).where(User.username == guessable_username)) is None

    create_user(create_args)
    with pytest.raises(SystemExit, match="Benutzer existiert bereits"):
        create_user(create_args)
    create_token(argparse.Namespace(username=username, label=token_label))
    output = capsys.readouterr().out
    raw_token = next(line for line in output.splitlines() if line.startswith("cg_"))

    db.expire_all()
    created_user = db.scalar(select(User).where(User.username == username))
    assert created_user is not None
    assert (
        db.scalar(select(NutritionTarget).where(NutritionTarget.user_id == created_user.id)) is None
    )
    token = db.scalar(select(ApiToken).where(ApiToken.user_id == created_user.id))
    assert token is not None
    password_hash = created_user.password_hash
    target_username = "PRIVATE.cli.target"
    target = User(
        username=target_username,
        password_hash=hash_password("PRIVATE-target-password-73!safe"),
        is_active=False,
        deactivated_at=datetime.now(UTC),
    )
    db.add(target)
    db.commit()
    with pytest.raises(SystemExit, match="Authenticator-Rücksetzung abgebrochen"):
        reset_authenticators(
            argparse.Namespace(
                username=target_username,
                confirm="PRIVATE.wrong.confirmation",
            )
        )
    reset_authenticators(
        argparse.Namespace(
            username=target_username,
            confirm=target_username,
            admin_username=username,
            admin_password=password,
            code="",
        )
    )

    db.expire_all()
    assert db.get(User, created_user.id) is not None
    assert db.get(ApiToken, token.id) is not None
    payloads = _event_payloads(records)
    user_ref = security_reference("user", created_user.id)
    token_ref = security_reference("api_token", token.id)
    target_ref = security_reference("user", target.id)
    assert [level for level, _ in records] == [logging.INFO, logging.INFO, logging.WARNING]
    assert [_contract_fields(payload) for payload in payloads] == [
        {
            "event": "admin.user.created",
            "outcome": "success",
            "target_ref": user_ref,
        },
        {
            "event": "auth.api_token.created",
            "outcome": "success",
            "actor_ref": user_ref,
            "target_ref": token_ref,
        },
        {
            "event": "admin.authenticators.reset",
            "outcome": "success",
            "actor_ref": user_ref,
            "target_ref": target_ref,
        },
    ]
    _assert_cli_metadata(payloads)
    _assert_sentinels_absent(
        records,
        username,
        password,
        password_hash,
        token_label,
        raw_token,
        token.token_hash,
        token.token_prefix,
        created_user.id,
        token.id,
        "PRIVATE.wrong.confirmation",
        target_username,
        target.id,
    )


def test_invitation_with_legacy_csrf_creates_one_record_and_event(
    client: TestClient,
    db: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_user_identity(db, user, "PRIVATE.legacy-csrf-admin")
    user.is_admin = True
    db.commit()
    csrf_token, session_cookie = _login(client, "PRIVATE.legacy-csrf-admin")
    session = db.scalar(
        select(UserSession).where(
            UserSession.token_hash == security.hash_session_token(session_cookie)
        )
    )
    assert session is not None
    session.csrf_hash = security.hash_session_token("legacy-csrf-for-invitation")
    db.commit()
    records = _capture_security_events(monkeypatch)

    created = client.post(
        "/api/v1/users/invitations",
        headers={"X-CSRF-Token": "legacy-csrf-for-invitation"},
        json={"expires_in_days": 7},
    )

    assert created.status_code == 201
    invitations = list(
        db.scalars(
            select(UserInvitation).where(UserInvitation.invited_by_user_id == user.id)
        )
    )
    assert len(invitations) == 1
    assert [payload["event"] for payload in _event_payloads(records)] == [
        "auth.invitation.created"
    ]
    assert csrf_token != "legacy-csrf-for-invitation"
