import base64
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import pyotp
import qrcode  # type: ignore[import-untyped]
import qrcode.image.svg  # type: ignore[import-untyped]
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.auth.security import hash_mfa_recovery_code
from app.models import MfaRecoveryCode, User, UserTotpCredential
from app.services.mfa_crypto import decrypt_mfa_secret, encrypt_mfa_secret

RECOVERY_CODE_COUNT = 10
TOTP_INTERVAL_SECONDS = 30
TOTP_VALID_WINDOW = 1


class MfaSetupError(RuntimeError):
    pass


@dataclass(frozen=True)
class TotpSetup:
    secret: str
    provisioning_uri: str
    qr_svg_data_url: str


def totp_status(db: Session, user_id: UUID) -> tuple[bool, bool, int]:
    credential = db.get(UserTotpCredential, user_id)
    remaining = 0
    if credential is not None and credential.enabled_at is not None:
        remaining = int(
            db.scalar(
                select(func.count(MfaRecoveryCode.id)).where(
                    MfaRecoveryCode.user_id == user_id,
                    MfaRecoveryCode.used_at.is_(None),
                )
            )
            or 0
        )
    return (
        credential is not None and credential.enabled_at is not None,
        credential is not None and credential.enabled_at is None,
        remaining,
    )


def begin_totp_setup(db: Session, user: User) -> TotpSetup:
    credential = db.get(UserTotpCredential, user.id)
    if credential is not None and credential.enabled_at is not None:
        raise MfaSetupError("TOTP ist für dieses Konto bereits aktiviert.")

    secret = pyotp.random_base32(length=32)
    encrypted = encrypt_mfa_secret(secret)
    if credential is None:
        credential = UserTotpCredential(
            user_id=user.id,
            encrypted_secret=encrypted,
        )
        db.add(credential)
    else:
        credential.encrypted_secret = encrypted
        credential.last_used_step = None
    db.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user.id))
    db.commit()

    provisioning_uri = pyotp.TOTP(
        secret,
        interval=TOTP_INTERVAL_SECONDS,
    ).provisioning_uri(
        name=user.username,
        issuer_name="CaloGraph",
    )
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        border=4,
        box_size=8,
        image_factory=qrcode.image.svg.SvgPathImage,
    )
    qr.add_data(provisioning_uri)
    qr.make(fit=True)
    image = qr.make_image(attrib={"aria-hidden": "true"})
    svg = image.to_string(encoding="utf-8")
    return TotpSetup(
        secret=secret,
        provisioning_uri=provisioning_uri,
        qr_svg_data_url=f"data:image/svg+xml;base64,{base64.b64encode(svg).decode()}",
    )


def confirm_totp_setup(
    db: Session,
    user_id: UUID,
    code: str,
    now: datetime | None = None,
) -> list[str]:
    credential = db.scalar(
        select(UserTotpCredential)
        .where(
            UserTotpCredential.user_id == user_id,
            UserTotpCredential.enabled_at.is_(None),
        )
        .with_for_update()
    )
    if credential is None:
        raise MfaSetupError("Es besteht keine offene TOTP-Einrichtung.")
    current_time = now or datetime.now(UTC)
    if not verify_totp_code(credential, code, current_time):
        raise MfaSetupError("Der Bestätigungscode ist ungültig.")

    credential.enabled_at = current_time
    recovery_codes = _replace_recovery_codes(db, user_id)
    db.commit()
    return recovery_codes


def verify_totp_code(
    credential: UserTotpCredential,
    code: str,
    now: datetime | None = None,
) -> bool:
    normalized_code = code.strip()
    if len(normalized_code) != 6 or not normalized_code.isascii() or not normalized_code.isdigit():
        return False
    current_time = now or datetime.now(UTC)
    totp = pyotp.TOTP(
        decrypt_mfa_secret(credential.encrypted_secret),
        interval=TOTP_INTERVAL_SECONDS,
    )
    current_step = totp.timecode(current_time)
    for offset in range(-TOTP_VALID_WINDOW, TOTP_VALID_WINDOW + 1):
        step = current_step + offset
        if credential.last_used_step is not None and step <= credential.last_used_step:
            continue
        expected = totp.at(step * TOTP_INTERVAL_SECONDS)
        if pyotp.utils.strings_equal(expected, normalized_code):
            credential.last_used_step = step
            return True
    return False


def consume_mfa_factor(
    db: Session,
    credential: UserTotpCredential,
    code: str,
    now: datetime | None = None,
) -> bool:
    if verify_totp_code(credential, code, now):
        return True
    normalized_recovery = _normalize_recovery_code(code)
    if not normalized_recovery:
        return False
    recovery = db.scalar(
        select(MfaRecoveryCode)
        .where(
            MfaRecoveryCode.user_id == credential.user_id,
            MfaRecoveryCode.code_hash
            == hash_mfa_recovery_code(normalized_recovery),
            MfaRecoveryCode.used_at.is_(None),
        )
        .with_for_update()
    )
    if recovery is None:
        return False
    recovery.used_at = now or datetime.now(UTC)
    return True


def regenerate_recovery_codes(db: Session, user_id: UUID) -> list[str]:
    credential = db.get(UserTotpCredential, user_id)
    if credential is None or credential.enabled_at is None:
        raise MfaSetupError("TOTP ist für dieses Konto nicht aktiviert.")
    codes = _replace_recovery_codes(db, user_id)
    db.commit()
    return codes


def disable_totp(db: Session, user_id: UUID) -> None:
    db.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user_id))
    db.execute(delete(UserTotpCredential).where(UserTotpCredential.user_id == user_id))
    db.commit()


def _replace_recovery_codes(db: Session, user_id: UUID) -> list[str]:
    db.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user_id))
    codes = [_new_recovery_code() for _ in range(RECOVERY_CODE_COUNT)]
    db.add_all(
        MfaRecoveryCode(
            user_id=user_id,
            code_hash=hash_mfa_recovery_code(_normalize_recovery_code(code)),
        )
        for code in codes
    )
    return codes


def _new_recovery_code() -> str:
    raw = secrets.token_hex(8).upper()
    return "-".join(raw[index : index + 4] for index in range(0, len(raw), 4))


def _normalize_recovery_code(value: str) -> str:
    normalized = value.replace("-", "").replace(" ", "").strip().upper()
    if (
        len(normalized) != 16
        or not normalized.isascii()
        or any(character not in "0123456789ABCDEF" for character in normalized)
    ):
        return ""
    return normalized
