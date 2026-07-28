import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

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


def create_session(db: Session, user: User) -> tuple[UserSession, str, str]:
    raw_token = secrets.token_urlsafe(48)
    csrf_token = secrets.token_urlsafe(32)
    session = UserSession(
        user_id=user.id,
        token_hash=hash_session_token(raw_token),
        csrf_hash=hash_session_token(csrf_token),
        expires_at=datetime.now(UTC) + timedelta(days=30),
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
