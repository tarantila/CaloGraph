import logging
import secrets
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Never

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import current_user, require_csrf
from app.auth.password_policy import PasswordPolicyError, validate_new_password
from app.auth.security import (
    REGISTRATION_STATE_TTL_SECONDS,
    create_registration_state,
    create_session,
    hash_invitation_token,
    hash_password,
    hash_session_token,
    revoke_user_sessions,
    session_cookie_name,
    verify_login_password,
    verify_password,
    verify_registration_state,
)
from app.config import settings
from app.database import get_db
from app.models import NutritionTarget, TrackingQualitySettings, User, UserInvitation, UserSession
from app.schemas import (
    CsrfResponse,
    InvitationExchangeRequest,
    InvitationStateResponse,
    LoginRequest,
    PasswordChangeRequest,
    RegistrationRequest,
    UserResponse,
)
from app.services.rate_limit import (
    RateLimitExceeded,
    check_rate_limit,
    clear_rate_limit,
    ensure_rate_limit_available,
    normalize_account_identifier,
    normalize_client_ip,
    rate_limit_key_id,
)

router = APIRouter(prefix="/auth", tags=["Authentifizierung"])
logger = logging.getLogger("calograph.auth")
REGISTRATION_COOKIE_NAME = "calograph_registration"
REGISTRATION_COOKIE_PATH = "/api/v1/auth"


def _log_login(
    request: Request,
    outcome: str,
    client_key: str,
    account_key: str,
) -> None:
    logger.info(
        "security_event=login outcome=%s request_id=%s client_key=%s account_key=%s",
        outcome,
        getattr(request.state, "request_id", None),
        rate_limit_key_id(client_key),
        rate_limit_key_id(account_key),
    )


def _log_password_change(request: Request, outcome: str, user_key: str) -> None:
    logger.info(
        "security_event=password_change outcome=%s request_id=%s user_key=%s",
        outcome,
        getattr(request.state, "request_id", None),
        rate_limit_key_id(user_key),
    )


def _active_invitation_from_state(
    request: Request,
    db: Session,
    now: datetime,
    *,
    for_update: bool = False,
) -> UserInvitation | None:
    invitation_id = verify_registration_state(
        request.cookies.get(REGISTRATION_COOKIE_NAME, ""),
        now,
    )
    if invitation_id is None:
        return None
    statement = select(UserInvitation).where(
        UserInvitation.id == invitation_id,
        UserInvitation.used_at.is_(None),
        UserInvitation.revoked_at.is_(None),
        UserInvitation.expires_at > now,
    )
    if for_update:
        statement = statement.with_for_update()
    return db.scalar(statement)


def _set_registration_cookie(response: Response, invitation: UserInvitation) -> None:
    response.set_cookie(
        REGISTRATION_COOKIE_NAME,
        create_registration_state(invitation.id),
        max_age=REGISTRATION_STATE_TTL_SECONDS,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path=REGISTRATION_COOKIE_PATH,
    )


@router.post("/invitation/exchange", status_code=204)
def exchange_invitation(
    payload: InvitationExchangeRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> None:
    client = normalize_client_ip(request.client.host if request.client else None)
    check_rate_limit(db, "invitation-exchange", f"ip:{client}", 5, 15 * 60)
    now = datetime.now(UTC)
    invitation = db.scalar(
        select(UserInvitation).where(
            UserInvitation.token_hash == hash_invitation_token(payload.token),
            UserInvitation.used_at.is_(None),
            UserInvitation.revoked_at.is_(None),
            UserInvitation.expires_at > now,
        ).with_for_update()
    )
    if invitation is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="Einladung ist ungültig oder abgelaufen")
    invitation.token_hash = hash_invitation_token(f"exchanged_{secrets.token_urlsafe(40)}")
    db.commit()
    _set_registration_cookie(response, invitation)


@router.get("/invitation/status", response_model=InvitationStateResponse)
def invitation_status(
    request: Request,
    db: Session = Depends(get_db),
) -> InvitationStateResponse:
    return InvitationStateResponse(
        valid=_active_invitation_from_state(request, db, datetime.now(UTC)) is not None
    )


@router.post("/register", response_model=UserResponse, status_code=201)
def register(
    payload: RegistrationRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> User:
    client = normalize_client_ip(request.client.host if request.client else None)
    check_rate_limit(db, "register", client, 5)
    now = datetime.now(UTC)
    invitation = _active_invitation_from_state(request, db, now, for_update=True)
    if invitation is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="Einladung ist ungültig oder abgelaufen")
    if db.scalar(select(User.id).where(User.username == payload.username)):
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail="Benutzername ist bereits vergeben")
    try:
        validate_new_password(payload.password, payload.username)
    except PasswordPolicyError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=422, detail=str(exc)) from None
    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        timezone=settings.calograph_timezone,
    )
    db.add(user)
    db.flush()
    db.add(TrackingQualitySettings(user_id=user.id))
    db.add(
        NutritionTarget(
            user_id=user.id,
            valid_from=date.today(),
            calories_kcal=Decimal("2200"),
            protein_g=Decimal("140"),
        )
    )
    invitation.used_at = now
    db.commit()
    db.refresh(user)
    response.delete_cookie(
        REGISTRATION_COOKIE_NAME,
        path=REGISTRATION_COOKIE_PATH,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )
    return user


@router.post("/login")
def login(
    payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)
) -> dict[str, object]:
    client_key = f"ip:{normalize_client_ip(request.client.host if request.client else None)}"
    account_key = f"account:{normalize_account_identifier(payload.username)}"
    try:
        check_rate_limit(
            db,
            "login-ip",
            client_key,
            settings.login_ip_rate_limit,
            settings.login_ip_rate_limit_window_seconds,
        )
        ensure_rate_limit_available(
            db,
            "login-account",
            account_key,
            settings.login_rate_limit,
            settings.login_rate_limit_window_seconds,
        )
    except RateLimitExceeded:
        _log_login(request, "rate_limited", client_key, account_key)
        raise

    user = db.scalar(select(User).where(User.username == payload.username))
    password_matches = verify_login_password(
        payload.password,
        user.password_hash if user is not None else None,
    )
    if user is None or not user.is_active or not password_matches:
        try:
            check_rate_limit(
                db,
                "login-account",
                account_key,
                settings.login_rate_limit,
                settings.login_rate_limit_window_seconds,
            )
        except RateLimitExceeded:
            _log_login(request, "rate_limited", client_key, account_key)
            raise
        _log_login(request, "failed", client_key, account_key)
        raise_invalid_login()

    clear_rate_limit(db, "login-account", account_key)
    session, raw_token, csrf_token = create_session(db, user)
    response.set_cookie(
        session_cookie_name(),
        raw_token,
        max_age=int((session.expires_at - session.created_at).total_seconds()),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    _log_login(request, "succeeded", client_key, account_key)
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
    raw_session = request.cookies.get(session_cookie_name(), "")
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
    raw = request.cookies.get(session_cookie_name(), "")
    session = db.scalar(
        select(UserSession).where(UserSession.token_hash == hash_session_token(raw))
    )
    if session:
        session.revoked_at = datetime.now(UTC)
        db.commit()
    response.delete_cookie(
        session_cookie_name(),
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


@router.post("/password", status_code=204)
def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> None:
    user_key = f"user:{user.id}"
    try:
        ensure_rate_limit_available(
            db,
            "password-change",
            user_key,
            settings.password_change_rate_limit,
            settings.password_change_rate_limit_window_seconds,
        )
    except RateLimitExceeded:
        _log_password_change(request, "rate_limited", user_key)
        raise

    if not verify_password(user.password_hash, payload.current_password):
        try:
            check_rate_limit(
                db,
                "password-change",
                user_key,
                settings.password_change_rate_limit,
                settings.password_change_rate_limit_window_seconds,
            )
        except RateLimitExceeded:
            _log_password_change(request, "rate_limited", user_key)
            raise
        _log_password_change(request, "failed", user_key)
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="Aktuelles Passwort ist falsch")
    clear_rate_limit(db, "password-change", user_key)
    try:
        validate_new_password(payload.new_password, user.username)
    except PasswordPolicyError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=422, detail=str(exc)) from None
    user.password_hash = hash_password(payload.new_password)
    revoke_user_sessions(db, user.id)
    _log_password_change(request, "succeeded", user_key)
