import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import options_to_json_dict
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.config import settings
from app.models import (
    PasskeyCredential,
    User,
    WebAuthnChallenge,
    WebAuthnUserHandle,
)
from app.schemas import (
    WebAuthnAuthenticationCredentialInput,
    WebAuthnRegistrationCredentialInput,
)

PASSKEY_CHALLENGE_TTL_SECONDS = 5 * 60
PASSKEY_REGISTRATION_PURPOSE = "passkey-registration"
PASSKEY_AUTHENTICATION_PURPOSE = "passkey-authentication"


class PasskeyRegistrationError(RuntimeError):
    pass


class PasskeyAuthenticationError(RuntimeError):
    pass


def passkey_rp_id() -> str:
    hostname = urlsplit(settings.calograph_public_url).hostname
    if not hostname:
        raise RuntimeError("CALOGRAPH_PUBLIC_URL enthält keinen gültigen Hostnamen.")
    return hostname


def begin_passkey_registration(
    db: Session,
    user: User,
    session_id: UUID,
    now: datetime | None = None,
) -> tuple[UUID, dict[str, object]]:
    current_time = now or datetime.now(UTC)
    identity = db.get(WebAuthnUserHandle, user.id)
    if identity is None:
        identity = WebAuthnUserHandle(
            user_id=user.id,
            user_handle=secrets.token_bytes(64),
        )
        db.add(identity)
        db.flush()

    existing_credentials = list(
        db.scalars(select(PasskeyCredential).where(PasskeyCredential.user_id == user.id))
    )
    options = generate_registration_options(
        rp_id=passkey_rp_id(),
        rp_name=settings.app_name,
        user_id=identity.user_handle,
        user_name=user.username,
        user_display_name=user.username,
        attestation=AttestationConveyancePreference.NONE,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            require_resident_key=True,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=item.credential_id) for item in existing_credentials
        ],
    )
    challenge = WebAuthnChallenge(
        purpose=PASSKEY_REGISTRATION_PURPOSE,
        challenge=options.challenge,
        user_id=user.id,
        session_id=session_id,
        expires_at=current_time + timedelta(seconds=PASSKEY_CHALLENGE_TTL_SECONDS),
    )
    db.add(challenge)
    db.commit()
    return challenge.id, options_to_json_dict(options)


def complete_passkey_registration(
    db: Session,
    user: User,
    session_id: UUID,
    challenge_id: UUID,
    label: str,
    credential_input: WebAuthnRegistrationCredentialInput,
    now: datetime | None = None,
) -> PasskeyCredential:
    current_time = now or datetime.now(UTC)
    normalized_label = label.strip()
    if not normalized_label:
        raise PasskeyRegistrationError("Bitte gib eine Bezeichnung für den Passkey an.")
    challenge = _claim_challenge(
        db,
        challenge_id,
        PASSKEY_REGISTRATION_PURPOSE,
        current_time,
        user_id=user.id,
        session_id=session_id,
    )
    credential = credential_input.model_dump(by_alias=True, exclude_none=True)
    try:
        verification = verify_registration_response(
            credential=credential,
            expected_challenge=challenge.challenge,
            expected_rp_id=passkey_rp_id(),
            expected_origin=settings.calograph_public_url,
            require_user_verification=True,
        )
    except WebAuthnException:
        db.commit()
        raise PasskeyRegistrationError(
            "Passkey-Registrierung konnte nicht verifiziert werden."
        ) from None

    existing = db.scalar(
        select(PasskeyCredential.id).where(
            PasskeyCredential.credential_id == verification.credential_id
        )
    )
    if existing is not None:
        db.commit()
        raise PasskeyRegistrationError("Dieser Passkey ist bereits registriert.")

    passkey = PasskeyCredential(
        user_id=user.id,
        label=normalized_label,
        credential_id=verification.credential_id,
        public_key=verification.credential_public_key,
        sign_count=verification.sign_count,
        transports=list(credential_input.response.transports),
        device_type=verification.credential_device_type.value,
        backed_up=verification.credential_backed_up,
    )
    db.add(passkey)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        consumed_challenge = db.get(WebAuthnChallenge, challenge_id)
        if consumed_challenge is not None and consumed_challenge.used_at is None:
            consumed_challenge.used_at = current_time
            db.commit()
        raise PasskeyRegistrationError("Dieser Passkey ist bereits registriert.") from None
    db.refresh(passkey)
    return passkey


def begin_passkey_authentication(
    db: Session,
    now: datetime | None = None,
) -> tuple[UUID, dict[str, object]]:
    current_time = now or datetime.now(UTC)
    options = generate_authentication_options(
        rp_id=passkey_rp_id(),
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    challenge = WebAuthnChallenge(
        purpose=PASSKEY_AUTHENTICATION_PURPOSE,
        challenge=options.challenge,
        expires_at=current_time + timedelta(seconds=PASSKEY_CHALLENGE_TTL_SECONDS),
    )
    db.add(challenge)
    db.commit()
    return challenge.id, options_to_json_dict(options)


def passkey_authentication_user_id(
    db: Session,
    credential_input: WebAuthnAuthenticationCredentialInput,
) -> UUID | None:
    try:
        credential_id = base64url_to_bytes(credential_input.id)
    except ValueError:
        return None
    return db.scalar(
        select(PasskeyCredential.user_id).where(
            PasskeyCredential.credential_id == credential_id
        )
    )


def complete_passkey_authentication(
    db: Session,
    challenge_id: UUID,
    credential_input: WebAuthnAuthenticationCredentialInput,
    now: datetime | None = None,
) -> User:
    current_time = now or datetime.now(UTC)
    challenge = _claim_challenge(
        db,
        challenge_id,
        PASSKEY_AUTHENTICATION_PURPOSE,
        current_time,
    )
    try:
        credential_id = base64url_to_bytes(credential_input.id)
    except ValueError:
        db.commit()
        raise PasskeyAuthenticationError("Passkey-Anmeldung ist fehlgeschlagen.") from None

    passkey = db.scalar(
        select(PasskeyCredential)
        .where(PasskeyCredential.credential_id == credential_id)
        .with_for_update()
    )
    if passkey is None or not _user_handle_matches(
        db,
        passkey.user_id,
        credential_input.response.user_handle,
    ):
        db.commit()
        raise PasskeyAuthenticationError("Passkey-Anmeldung ist fehlgeschlagen.")

    credential = credential_input.model_dump(by_alias=True, exclude_none=True)
    try:
        verification = verify_authentication_response(
            credential=credential,
            expected_challenge=challenge.challenge,
            expected_rp_id=passkey_rp_id(),
            expected_origin=settings.calograph_public_url,
            credential_public_key=passkey.public_key,
            credential_current_sign_count=passkey.sign_count,
            require_user_verification=True,
        )
    except WebAuthnException:
        db.commit()
        raise PasskeyAuthenticationError("Passkey-Anmeldung ist fehlgeschlagen.") from None

    if verification.credential_id != passkey.credential_id:
        db.commit()
        raise PasskeyAuthenticationError("Passkey-Anmeldung ist fehlgeschlagen.")
    user = db.get(User, passkey.user_id)
    if user is None or not user.is_active:
        db.commit()
        raise PasskeyAuthenticationError("Passkey-Anmeldung ist fehlgeschlagen.")

    passkey.sign_count = verification.new_sign_count
    passkey.device_type = verification.credential_device_type.value
    passkey.backed_up = verification.credential_backed_up
    passkey.last_used_at = current_time
    db.commit()
    return user


def list_passkeys(db: Session, user_id: UUID) -> list[PasskeyCredential]:
    return list(
        db.scalars(
            select(PasskeyCredential)
            .where(PasskeyCredential.user_id == user_id)
            .order_by(PasskeyCredential.created_at.desc())
        )
    )


def delete_passkey(db: Session, user_id: UUID, passkey_id: UUID) -> bool:
    result = db.execute(
        delete(PasskeyCredential).where(
            PasskeyCredential.id == passkey_id,
            PasskeyCredential.user_id == user_id,
        )
    )
    db.commit()
    return bool(getattr(result, "rowcount", 0))


def purge_expired_webauthn_challenges(
    db: Session,
    now: datetime | None = None,
) -> int:
    current_time = now or datetime.now(UTC)
    result = db.execute(
        delete(WebAuthnChallenge).where(WebAuthnChallenge.expires_at <= current_time)
    )
    db.commit()
    return int(getattr(result, "rowcount", 0) or 0)


def _claim_challenge(
    db: Session,
    challenge_id: UUID,
    purpose: str,
    now: datetime,
    *,
    user_id: UUID | None = None,
    session_id: UUID | None = None,
) -> WebAuthnChallenge:
    conditions = [
        WebAuthnChallenge.id == challenge_id,
        WebAuthnChallenge.purpose == purpose,
        WebAuthnChallenge.used_at.is_(None),
        WebAuthnChallenge.expires_at > now,
    ]
    if user_id is not None:
        conditions.append(WebAuthnChallenge.user_id == user_id)
    if session_id is not None:
        conditions.append(WebAuthnChallenge.session_id == session_id)
    challenge = db.scalar(select(WebAuthnChallenge).where(*conditions).with_for_update())
    if challenge is None:
        if purpose == PASSKEY_REGISTRATION_PURPOSE:
            raise PasskeyRegistrationError("Passkey-Einrichtung ist abgelaufen. Bitte neu starten.")
        raise PasskeyAuthenticationError("Passkey-Anmeldung ist fehlgeschlagen.")
    challenge.used_at = now
    return challenge


def _user_handle_matches(
    db: Session,
    user_id: UUID,
    encoded_user_handle: str | None,
) -> bool:
    if not encoded_user_handle:
        return False
    identity = db.get(WebAuthnUserHandle, user_id)
    if identity is None:
        return False
    try:
        supplied_handle = base64url_to_bytes(encoded_user_handle)
    except ValueError:
        return False
    return secrets.compare_digest(identity.user_handle, supplied_handle)
