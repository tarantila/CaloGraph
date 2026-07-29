import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.config import settings
from app.models import ApiToken, User, UserSession

password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
# Generated once per process so unknown accounts perform the same Argon2 work
# without embedding a reusable credential in the source tree.
DUMMY_PASSWORD_HASH = password_hasher.hash(secrets.token_urlsafe(48))
REGISTRATION_STATE_TTL_SECONDS = 10 * 60
REGISTRATION_STATE_VERSION = "v1"
MFA_LOGIN_STATE_TTL_SECONDS = 5 * 60
MFA_LOGIN_STATE_VERSION = "v1"


def session_cookie_name() -> str:
    if settings.cookie_secure:
        return "__Host-calograph_session"
    return "calograph_session"


def mfa_challenge_cookie_name() -> str:
    if settings.cookie_secure:
        return "__Host-calograph_mfa_challenge"
    return "calograph_mfa_challenge"


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def verify_login_password(
    password: str,
    password_hash: str | None,
) -> bool:
    return verify_password(password_hash or DUMMY_PASSWORD_HASH, password)


def _hmac_token(raw: str, secret: str) -> str:
    return hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()


def hash_session_token(raw: str) -> str:
    return _hmac_token(raw, settings.session_secret)


def hash_api_token(raw: str) -> str:
    return _hmac_token(raw, settings.rate_limit_secret)


def hash_invitation_token(raw: str) -> str:
    return _hmac_token(raw, settings.session_secret)


def hash_mfa_recovery_code(raw: str) -> str:
    return _hmac_token(f"mfa-recovery:{raw}", settings.session_secret)


def create_registration_state(
    invitation_id: UUID,
    now: datetime | None = None,
) -> str:
    expires_at = int((now or datetime.now(UTC)).timestamp()) + REGISTRATION_STATE_TTL_SECONDS
    payload = f"{REGISTRATION_STATE_VERSION}.{invitation_id.hex}.{expires_at}"
    signature = _hmac_token(f"registration-state:{payload}", settings.session_secret)
    return f"{payload}.{signature}"


def verify_registration_state(
    raw: str,
    now: datetime | None = None,
) -> UUID | None:
    try:
        version, invitation_hex, expires_raw, signature = raw.split(".", maxsplit=3)
        expires_at = int(expires_raw)
        invitation_id = UUID(hex=invitation_hex)
    except (ValueError, TypeError):
        return None
    if version != REGISTRATION_STATE_VERSION:
        return None
    payload = f"{version}.{invitation_hex}.{expires_at}"
    expected = _hmac_token(f"registration-state:{payload}", settings.session_secret)
    if not hmac.compare_digest(signature, expected):
        return None
    if expires_at <= int((now or datetime.now(UTC)).timestamp()):
        return None
    return invitation_id


def create_mfa_login_state(
    user_id: UUID,
    now: datetime | None = None,
) -> str:
    expires_at = int((now or datetime.now(UTC)).timestamp()) + MFA_LOGIN_STATE_TTL_SECONDS
    nonce = secrets.token_urlsafe(24)
    payload = f"{MFA_LOGIN_STATE_VERSION}.{user_id.hex}.{expires_at}.{nonce}"
    signature = _hmac_token(f"mfa-login-state:{payload}", settings.session_secret)
    return f"{payload}.{signature}"


def verify_mfa_login_state(
    raw: str,
    now: datetime | None = None,
) -> UUID | None:
    try:
        version, user_hex, expires_raw, nonce, signature = raw.split(".", maxsplit=4)
        expires_at = int(expires_raw)
        user_id = UUID(hex=user_hex)
    except (ValueError, TypeError):
        return None
    if version != MFA_LOGIN_STATE_VERSION or len(nonce) < 20:
        return None
    payload = f"{version}.{user_hex}.{expires_at}.{nonce}"
    expected = _hmac_token(f"mfa-login-state:{payload}", settings.session_secret)
    if not hmac.compare_digest(signature, expected):
        return None
    if expires_at <= int((now or datetime.now(UTC)).timestamp()):
        return None
    return user_id


def create_session(
    db: Session,
    user: User,
    now: datetime | None = None,
) -> tuple[UserSession, str, str]:
    created_at = now or datetime.now(UTC)
    raw_token = secrets.token_urlsafe(48)
    csrf_token = secrets.token_urlsafe(32)
    session = UserSession(
        user_id=user.id,
        token_hash=hash_session_token(raw_token),
        csrf_hash=hash_session_token(csrf_token),
        created_at=created_at,
        expires_at=created_at
        + timedelta(days=settings.session_absolute_timeout_days),
        last_used_at=created_at,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session, raw_token, csrf_token


def create_api_token(
    db: Session, user: User, label: str, expires_at: datetime | None = None
) -> tuple[ApiToken, str]:
    raw = f"cg_{secrets.token_urlsafe(40)}"
    token = ApiToken(
        user_id=user.id,
        label=label,
        token_prefix=raw[:12],
        token_hash=hash_api_token(raw),
        scopes=["import"],
        expires_at=expires_at,
    )
    db.add(token)
    db.commit()
    db.refresh(token)
    return token, raw


def revoke_user_sessions(db: Session, user_id: object) -> None:
    db.execute(delete(UserSession).where(UserSession.user_id == user_id))
    db.commit()


def revoke_other_user_sessions(
    db: Session,
    user_id: object,
    current_token_hash: str,
) -> None:
    db.execute(
        delete(UserSession).where(
            UserSession.user_id == user_id,
            UserSession.token_hash != current_token_hash,
        )
    )
    db.commit()


def purge_expired_sessions(
    db: Session,
    now: datetime | None = None,
) -> int:
    current_time = now or datetime.now(UTC)
    idle_cutoff = current_time - timedelta(hours=settings.session_idle_timeout_hours)
    result = db.execute(
        delete(UserSession).where(
            (UserSession.revoked_at.is_not(None))
            | (UserSession.expires_at <= current_time)
            | (
                UserSession.last_used_at.is_not(None)
                & (UserSession.last_used_at <= idle_cutoff)
            )
            | (
                UserSession.last_used_at.is_(None)
                & (UserSession.created_at <= idle_cutoff)
            )
        )
    )
    db.commit()
    return int(getattr(result, "rowcount", 0) or 0)
