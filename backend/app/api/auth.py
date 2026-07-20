import secrets
from datetime import UTC, datetime
from typing import Never

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import current_user, require_csrf
from app.auth.security import (
    create_session,
    hash_password,
    hash_session_token,
    revoke_user_sessions,
    verify_password,
)
from app.config import settings
from app.database import get_db
from app.models import User, UserSession
from app.schemas import CsrfResponse, LoginRequest, PasswordChangeRequest, UserResponse
from app.services.rate_limit import check_rate_limit

router = APIRouter(prefix="/auth", tags=["Authentifizierung"])


@router.post("/login")
def login(
    payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)
) -> dict[str, object]:
    client = request.client.host if request.client else "unknown"
    check_rate_limit(db, "login", f"{client}:{payload.username.lower()}", settings.login_rate_limit)
    user = db.scalar(select(User).where(User.username == payload.username))
    if not user or not user.is_active or not verify_password(user.password_hash, payload.password):
        raise_invalid_login()
    _, raw_token, csrf_token = create_session(db, user)
    response.set_cookie(
        "calograph_session",
        raw_token,
        max_age=30 * 24 * 60 * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return {"user": UserResponse.model_validate(user), "csrf_token": csrf_token}


def raise_invalid_login() -> Never:
    from fastapi import HTTPException

    raise HTTPException(status_code=401, detail="Benutzername oder Passwort ist falsch")


@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(current_user)) -> User:
    return user


@router.get("/csrf", response_model=CsrfResponse)
def csrf_token(
    request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> CsrfResponse:
    del user
    raw_session = request.cookies.get("calograph_session", "")
    session = db.scalar(
        select(UserSession).where(UserSession.token_hash == hash_session_token(raw_session))
    )
    if session is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=401, detail="Sitzung ungültig")
    raw_csrf = secrets.token_urlsafe(32)
    session.csrf_hash = hash_session_token(raw_csrf)
    db.commit()
    return CsrfResponse(csrf_token=raw_csrf)


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> None:
    del user
    raw = request.cookies.get("calograph_session", "")
    session = db.scalar(
        select(UserSession).where(UserSession.token_hash == hash_session_token(raw))
    )
    if session:
        session.revoked_at = datetime.now(UTC)
        db.commit()
    response.delete_cookie("calograph_session", path="/")


@router.post("/password", status_code=204)
def change_password(
    payload: PasswordChangeRequest,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> None:
    if not verify_password(user.password_hash, payload.current_password):
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="Aktuelles Passwort ist falsch")
    user.password_hash = hash_password(payload.new_password)
    revoke_user_sessions(db, user.id)
