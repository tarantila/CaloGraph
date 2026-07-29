from datetime import UTC, datetime, timedelta

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.security import hash_api_token, hash_session_token, session_cookie_name
from app.config import settings
from app.database import get_db
from app.models import ApiToken, User, UserSession

bearer = HTTPBearer(auto_error=False)


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    raw = request.cookies.get(session_cookie_name())
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Anmeldung erforderlich"
        )
    now = datetime.now(UTC)
    idle_cutoff = now - timedelta(hours=settings.session_idle_timeout_hours)
    session = db.scalar(
        select(UserSession).where(
            UserSession.token_hash == hash_session_token(raw),
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now,
            func.coalesce(UserSession.last_used_at, UserSession.created_at)
            > idle_cutoff,
        )
    )
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sitzung ungültig")
    user = db.get(User, session.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Konto inaktiv")
    session.last_used_at = now
    db.commit()
    return user


def require_csrf(
    request: Request,
    x_csrf_token: str = Header(default=""),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> User:
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") not in settings.trusted_origin_list:
        raise HTTPException(status_code=403, detail="Unzulässiger Request-Ursprung")
    raw = request.cookies.get(session_cookie_name(), "")
    session = db.scalar(
        select(UserSession).where(UserSession.token_hash == hash_session_token(raw))
    )
    if (
        not session
        or not x_csrf_token
        or not secrets_compare(session.csrf_hash, hash_session_token(x_csrf_token))
    ):
        raise HTTPException(status_code=403, detail="CSRF-Prüfung fehlgeschlagen")
    return user


def secrets_compare(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(left, right)


def import_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> tuple[User, ApiToken]:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Bearer-Import-Token erforderlich")
    token = db.scalar(
        select(ApiToken).where(
            ApiToken.token_hash == hash_api_token(credentials.credentials),
            ApiToken.revoked_at.is_(None),
        )
    )
    now = datetime.now(UTC)
    if not token or (token.expires_at and token.expires_at <= now) or "import" not in token.scopes:
        raise HTTPException(status_code=401, detail="Import-Token ungültig")
    user = db.get(User, token.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Import-Konto inaktiv")
    token.last_used_at = now
    db.commit()
    return user, token
