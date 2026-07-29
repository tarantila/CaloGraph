import argparse
import base64
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.cli import reset_mfa
from app.main import app
from app.models import (
    PasskeyCredential,
    User,
    UserSession,
    WebAuthnChallenge,
    WebAuthnUserHandle,
)
from app.services.passkeys import (
    PASSKEY_CHALLENGE_TTL_SECONDS,
    purge_expired_webauthn_challenges,
)

PASSWORD = "correct-horse-battery-staple"
CREDENTIAL_ID = b"calograph-test-passkey"
PUBLIC_KEY = b"test-cose-public-key"


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _login(client: TestClient) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": PASSWORD},
    )
    assert response.status_code == 200
    return str(response.json()["csrf_token"])


def _registration_credential() -> dict[str, object]:
    credential_id = _base64url(CREDENTIAL_ID)
    return {
        "id": credential_id,
        "rawId": credential_id,
        "response": {
            "clientDataJSON": _base64url(b"client-data"),
            "attestationObject": _base64url(b"attestation"),
            "transports": ["internal"],
        },
        "authenticatorAttachment": "platform",
        "clientExtensionResults": {},
        "type": "public-key",
    }


def _authentication_credential(user_handle: bytes) -> dict[str, object]:
    credential_id = _base64url(CREDENTIAL_ID)
    return {
        "id": credential_id,
        "rawId": credential_id,
        "response": {
            "clientDataJSON": _base64url(b"client-data"),
            "authenticatorData": _base64url(b"authenticator-data"),
            "signature": _base64url(b"signature"),
            "userHandle": _base64url(user_handle),
        },
        "authenticatorAttachment": "platform",
        "clientExtensionResults": {},
        "type": "public-key",
    }


def _mock_registration_verification(monkeypatch) -> None:
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


def _register_passkey(
    client: TestClient,
    monkeypatch,
    *,
    label: str = "Windows Hello",
) -> dict[str, object]:
    _mock_registration_verification(monkeypatch)
    csrf = _login(client)
    options = client.post(
        "/api/v1/settings/passkeys/options",
        headers={"X-CSRF-Token": csrf},
        json={"current_password": PASSWORD},
    )
    assert options.status_code == 200
    response = client.post(
        "/api/v1/settings/passkeys",
        headers={"X-CSRF-Token": csrf},
        json={
            "challenge_id": options.json()["challenge_id"],
            "label": label,
            "credential": _registration_credential(),
        },
    )
    assert response.status_code == 201
    return response.json()


def test_passkey_registration_is_bound_to_user_session_and_one_time(
    client: TestClient,
    user: User,
    db,
    monkeypatch,
) -> None:
    registered = _register_passkey(client, monkeypatch)
    assert registered["label"] == "Windows Hello"
    assert registered["device_type"] == "single_device"

    passkey = db.scalar(select(PasskeyCredential))
    identity = db.get(WebAuthnUserHandle, user.id)
    challenge = db.scalar(select(WebAuthnChallenge))
    assert passkey is not None
    assert passkey.public_key == PUBLIC_KEY
    assert passkey.transports == ["internal"]
    assert identity is not None
    assert len(identity.user_handle) == 64
    assert challenge is not None
    assert challenge.user_id == user.id
    assert challenge.session_id is not None
    assert challenge.used_at is not None

    replay = client.post(
        "/api/v1/settings/passkeys",
        headers={"X-CSRF-Token": client.get("/api/v1/auth/csrf").json()["csrf_token"]},
        json={
            "challenge_id": str(challenge.id),
            "label": "Replay",
            "credential": _registration_credential(),
        },
    )
    assert replay.status_code == 400
    assert db.scalar(select(func.count(PasskeyCredential.id))) == 1


def test_registration_challenge_cannot_be_completed_from_another_session(
    client: TestClient,
    user: User,
    monkeypatch,
) -> None:
    del user
    _mock_registration_verification(monkeypatch)
    first_csrf = _login(client)
    options = client.post(
        "/api/v1/settings/passkeys/options",
        headers={"X-CSRF-Token": first_csrf},
        json={"current_password": PASSWORD},
    )
    assert options.status_code == 200

    other_client = TestClient(app)
    other_csrf = _login(other_client)
    response = other_client.post(
        "/api/v1/settings/passkeys",
        headers={"X-CSRF-Token": other_csrf},
        json={
            "challenge_id": options.json()["challenge_id"],
            "label": "Foreign session",
            "credential": _registration_credential(),
        },
    )
    assert response.status_code == 400


def test_passkey_login_creates_session_and_updates_counter(
    client: TestClient,
    user: User,
    db,
    monkeypatch,
) -> None:
    _register_passkey(client, monkeypatch)
    identity = db.get(WebAuthnUserHandle, user.id)
    assert identity is not None
    client.cookies.clear()
    monkeypatch.setattr(
        "app.services.passkeys.verify_authentication_response",
        lambda **_: SimpleNamespace(
            credential_id=CREDENTIAL_ID,
            new_sign_count=7,
            credential_device_type=SimpleNamespace(value="multi_device"),
            credential_backed_up=True,
        ),
    )

    options = client.post("/api/v1/auth/passkey/options")
    assert options.status_code == 200
    response = client.post(
        "/api/v1/auth/passkey/verify",
        json={
            "challenge_id": options.json()["challenge_id"],
            "credential": _authentication_credential(identity.user_handle),
        },
    )

    assert response.status_code == 200
    assert response.json()["user"]["username"] == "admin"
    assert response.json()["csrf_token"]
    assert client.get("/api/v1/auth/me").status_code == 200
    db.expire_all()
    passkey = db.scalar(select(PasskeyCredential))
    assert passkey is not None
    assert passkey.sign_count == 7
    assert passkey.device_type == "multi_device"
    assert passkey.backed_up is True
    assert passkey.last_used_at is not None


def test_passkey_login_rejects_wrong_user_handle_with_generic_error(
    client: TestClient,
    user: User,
    db,
    monkeypatch,
) -> None:
    del user
    _register_passkey(client, monkeypatch)
    client.cookies.clear()
    sessions_before = db.scalar(select(func.count(UserSession.id)))
    monkeypatch.setattr(
        "app.services.passkeys.verify_authentication_response",
        lambda **_: SimpleNamespace(),
    )
    options = client.post("/api/v1/auth/passkey/options")
    response = client.post(
        "/api/v1/auth/passkey/verify",
        json={
            "challenge_id": options.json()["challenge_id"],
            "credential": _authentication_credential(b"wrong-user-handle"),
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Benutzername oder Passwort ist falsch"
    assert db.scalar(select(func.count(UserSession.id))) == sessions_before


def test_passkey_removal_requires_password_and_admin_reset_removes_passkeys(
    client: TestClient,
    user: User,
    db,
    monkeypatch,
    capsys,
) -> None:
    passkey = _register_passkey(client, monkeypatch)
    csrf = client.get("/api/v1/auth/csrf").json()["csrf_token"]
    wrong_password = client.request(
        "DELETE",
        f"/api/v1/settings/passkeys/{passkey['id']}",
        headers={"X-CSRF-Token": csrf},
        json={"current_password": "this-password-is-wrong"},
    )
    assert wrong_password.status_code == 400
    assert db.get(PasskeyCredential, UUID(str(passkey["id"]))) is not None

    reset_mfa(argparse.Namespace(username=user.username, confirm=user.username))
    db.expire_all()
    assert db.scalar(select(PasskeyCredential)) is None
    assert db.get(WebAuthnUserHandle, user.id) is None
    assert db.scalar(select(func.count(UserSession.id))) == 0
    assert "Anmeldefaktoren" in capsys.readouterr().out


def test_expired_webauthn_challenges_are_purged(db) -> None:
    now = datetime.now(UTC)
    db.add_all(
        [
            WebAuthnChallenge(
                purpose="passkey-authentication",
                challenge=b"expired",
                expires_at=now - timedelta(seconds=1),
            ),
            WebAuthnChallenge(
                purpose="passkey-authentication",
                challenge=b"active",
                expires_at=now + timedelta(seconds=PASSKEY_CHALLENGE_TTL_SECONDS),
            ),
        ]
    )
    db.commit()

    assert purge_expired_webauthn_challenges(db, now) == 1
    assert db.scalar(select(func.count(WebAuthnChallenge.id))) == 1
