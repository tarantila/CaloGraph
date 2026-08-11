import secrets
from contextlib import nullcontext
from datetime import UTC, datetime
from typing import Never

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import current_user, require_csrf
from app.auth.password_policy import PasswordPolicyError, validate_new_password
from app.auth.security import (
    MFA_LOGIN_STATE_TTL_SECONDS,
    REGISTRATION_STATE_TTL_SECONDS,
    create_mfa_login_state,
    create_registration_state,
    create_session,
    hash_account_recovery_token,
    hash_invitation_token,
    hash_password,
    hash_session_token,
    mfa_challenge_cookie_name,
    revoke_user_sessions,
    session_cookie_name,
    verify_login_password,
    verify_mfa_login_state,
    verify_password,
    verify_registration_state,
)
from app.config import settings
from app.database import get_db
from app.models import (
    TrackingQualitySettings,
    User,
    UserInvitation,
    UserSession,
    UserTotpCredential,
)
from app.schemas import (
    AccountRecoveryCompleteRequest,
    CsrfResponse,
    InvitationExchangeRequest,
    InvitationStateResponse,
    LoginRequest,
    MfaCodeRequest,
    PasskeyAuthenticationCompleteRequest,
    PasswordChangeRequest,
    RegistrationRequest,
    UserResponse,
    WebAuthnOptionsResponse,
)
from app.security_events import log_security_event, security_reference
from app.services.account_recovery import (
    AccountRecoveryRejected,
    complete_account_recovery,
)
from app.services.mfa import consume_mfa_factor
from app.services.passkeys import (
    PasskeyAuthenticationError,
    begin_passkey_authentication,
    complete_passkey_authentication,
    passkey_authentication_user_id,
)
from app.services.rate_limit import (
    RateLimitExceeded,
    check_rate_limit,
    clear_rate_limit,
    ensure_rate_limit_available,
    login_password_slot,
    normalize_account_identifier,
    normalize_client_ip,
    rate_limit_key_id,
)
from app.services.user_operation_lock import (
    InactiveUserOperation,
    shared_user_operation,
)

router = APIRouter(prefix="/auth", tags=["Authentifizierung"])
REGISTRATION_COOKIE_NAME = "calograph_registration"
REGISTRATION_COOKIE_PATH = "/api/v1/auth"


def _log_login(
    request: Request,
    outcome: str,
    client_key: str,
    account_key: str,
) -> None:
    del request
    event, reason = {
        "failed": ("auth.login.failed", "invalid_credentials"),
        "mfa_failed": ("auth.login.failed", "invalid_mfa"),
        "mfa_required": ("auth.login.mfa_required", None),
        "succeeded": ("auth.login.succeeded", None),
        "succeeded_with_mfa": ("auth.login.succeeded", None),
    }[outcome]
    log_security_event(
        event,
        client_ref=rate_limit_key_id(client_key),
        target_ref=rate_limit_key_id(account_key),
        reason=reason,
    )


def _log_password_change(request: Request, outcome: str, user_key: str) -> None:
    del request
    event = {
        "failed": "auth.password.change_failed",
        "succeeded": "auth.password.changed",
    }[outcome]
    log_security_event(
        event,
        actor_ref=rate_limit_key_id(user_key),
        reason="invalid_current_password" if outcome == "failed" else None,
    )


def _set_mfa_challenge_cookie(response: Response, user: User) -> None:
    response.set_cookie(
        mfa_challenge_cookie_name(),
        create_mfa_login_state(user.id),
        max_age=MFA_LOGIN_STATE_TTL_SECONDS,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )


def _delete_mfa_challenge_cookie(response: Response) -> None:
    response.delete_cookie(
        mfa_challenge_cookie_name(),
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )


def _set_session_cookie(
    response: Response,
    session: UserSession,
    raw_token: str,
) -> None:
    response.set_cookie(
        session_cookie_name(),
        raw_token,
        max_age=int((session.expires_at - session.created_at).total_seconds()),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
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
        select(UserInvitation)
        .where(
            UserInvitation.token_hash == hash_invitation_token(payload.token),
            UserInvitation.used_at.is_(None),
            UserInvitation.revoked_at.is_(None),
            UserInvitation.expires_at > now,
        )
        .with_for_update()
    )
    if invitation is None:
        from fastapi import HTTPException

        log_security_event("auth.invitation.rejected", reason="invalid_or_expired")
        raise HTTPException(status_code=400, detail="Einladung ist ungültig oder abgelaufen")
    invitation.token_hash = hash_invitation_token(f"exchanged_{secrets.token_urlsafe(40)}")
    db.commit()
    _set_registration_cookie(response, invitation)
    log_security_event(
        "auth.invitation.exchanged",
        target_ref=security_reference("invitation", invitation.id),
    )


@router.get("/invitation/status", response_model=InvitationStateResponse)
def invitation_status(
    request: Request,
    db: Session = Depends(get_db),
) -> InvitationStateResponse:
    return InvitationStateResponse(
        valid=_active_invitation_from_state(request, db, datetime.now(UTC)) is not None
    )


@router.post("/recovery/complete", status_code=204)
def complete_recovery(
    payload: AccountRecoveryCompleteRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> None:
    ip_key = f"ip:{normalize_client_ip(request.client.host if request.client else None)}"
    token_key = f"token:{hash_account_recovery_token(payload.recovery_token)}"
    ensure_rate_limit_available(
        db,
        "recovery-complete-ip",
        ip_key,
        settings.recovery_ip_rate_limit,
        settings.recovery_rate_limit_window_seconds,
    )
    ensure_rate_limit_available(
        db,
        "recovery-complete-token",
        token_key,
        settings.recovery_rate_limit,
        settings.recovery_rate_limit_window_seconds,
    )
    try:
        complete_account_recovery(
            db,
            payload.recovery_token,
            payload.new_password,
        )
    except AccountRecoveryRejected:
        log_security_event(
            "auth.password.recovery_rejected",
            reason="invalid_token",
        )
        check_rate_limit(
            db,
            "recovery-complete-ip",
            ip_key,
            settings.recovery_ip_rate_limit,
            settings.recovery_rate_limit_window_seconds,
        )
        check_rate_limit(
            db,
            "recovery-complete-token",
            token_key,
            settings.recovery_rate_limit,
            settings.recovery_rate_limit_window_seconds,
        )
        raise HTTPException(
            status_code=400,
            detail="Recovery-Token ist ungültig oder abgelaufen",
        ) from None
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    clear_rate_limit(db, "recovery-complete-ip", ip_key)
    clear_rate_limit(db, "recovery-complete-token", token_key)


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
    log_security_event(
        "auth.registration.succeeded",
        actor_ref=security_reference("user", user.id),
        target_ref=security_reference("invitation", invitation.id),
    )
    return user


@router.post("/login")
def login(
    payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)
) -> dict[str, object]:
    client_key = f"ip:{normalize_client_ip(request.client.host if request.client else None)}"
    account_key = f"account:{normalize_account_identifier(payload.username)}"
    check_rate_limit(
        db,
        "login-ip",
        client_key,
        settings.login_ip_rate_limit,
        settings.login_ip_rate_limit_window_seconds,
    )
    with login_password_slot():
        check_rate_limit(
            db,
            "login-account",
            account_key,
            settings.login_rate_limit,
            settings.login_rate_limit_window_seconds,
        )
        user = db.scalar(select(User).where(User.username == payload.username))
        password_matches = verify_login_password(
            payload.password,
            user.password_hash if user is not None else None,
        )
    if user is None or not user.is_active or not password_matches:
        _log_login(request, "failed", client_key, account_key)
        raise_invalid_login()

    clear_rate_limit(db, "login-account", account_key)
    totp_credential = db.get(UserTotpCredential, user.id)
    if totp_credential is not None and totp_credential.enabled_at is not None:
        _set_mfa_challenge_cookie(response, user)
        _log_login(request, "mfa_required", client_key, account_key)
        return {"mfa_required": True}

    session, raw_token, csrf_token = create_session(db, user)
    _set_session_cookie(response, session, raw_token)
    _delete_mfa_challenge_cookie(response)
    _log_login(request, "succeeded", client_key, account_key)
    return {
        "mfa_required": False,
        "user": UserResponse.model_validate(user),
        "csrf_token": csrf_token,
    }


def raise_invalid_login() -> Never:
    from fastapi import HTTPException

    raise HTTPException(status_code=401, detail="Benutzername oder Passwort ist falsch")


def _reject_mfa_login(
    db: Session,
    request: Request,
    client_key: str,
    account_key: str,
) -> Never:
    rate_limit_error: RateLimitExceeded | None = None
    for action, key, limit in (
        ("mfa-ip", client_key, settings.mfa_ip_rate_limit),
        ("mfa-account", account_key, settings.mfa_rate_limit),
    ):
        try:
            check_rate_limit(
                db,
                action,
                key,
                limit,
                settings.mfa_rate_limit_window_seconds,
            )
        except RateLimitExceeded as exc:
            if rate_limit_error is None or exc.retry_after > rate_limit_error.retry_after:
                rate_limit_error = exc
    if rate_limit_error:
        raise rate_limit_error
    _log_login(request, "mfa_failed", client_key, account_key)
    raise_invalid_login()


@router.post("/passkey/options", response_model=WebAuthnOptionsResponse)
def passkey_login_options(
    request: Request,
    db: Session = Depends(get_db),
) -> WebAuthnOptionsResponse:
    client_key = f"ip:{normalize_client_ip(request.client.host if request.client else None)}"
    check_rate_limit(
        db,
        "passkey-options-ip",
        client_key,
        settings.passkey_ip_rate_limit,
        settings.passkey_rate_limit_window_seconds,
    )
    challenge_id, public_key = begin_passkey_authentication(db)
    return WebAuthnOptionsResponse(
        challenge_id=challenge_id,
        public_key=public_key,
    )


@router.post("/passkey/verify")
def verify_passkey_login(
    payload: PasskeyAuthenticationCompleteRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    client_key = f"ip:{normalize_client_ip(request.client.host if request.client else None)}"
    ensure_rate_limit_available(
        db,
        "passkey-verify-ip",
        client_key,
        settings.passkey_ip_rate_limit,
        settings.passkey_rate_limit_window_seconds,
    )
    authentication_user_id = passkey_authentication_user_id(db, payload.credential)
    operation = (
        shared_user_operation(db, authentication_user_id)
        if authentication_user_id is not None
        else nullcontext()
    )
    try:
        with operation:
            user = complete_passkey_authentication(
                db,
                payload.challenge_id,
                payload.credential,
            )
            clear_rate_limit(db, "passkey-verify-ip", client_key)
            session, raw_token, csrf_token = create_session(db, user)
    except PasskeyAuthenticationError, InactiveUserOperation:
        check_rate_limit(
            db,
            "passkey-verify-ip",
            client_key,
            settings.passkey_ip_rate_limit,
            settings.passkey_rate_limit_window_seconds,
        )
        log_security_event(
            "auth.passkey.login_failed",
            client_ref=rate_limit_key_id(client_key),
            reason="invalid_assertion",
        )
        raise_invalid_login()
    _set_session_cookie(response, session, raw_token)
    _delete_mfa_challenge_cookie(response)
    log_security_event(
        "auth.passkey.login_succeeded",
        actor_ref=security_reference("user", user.id),
        client_ref=rate_limit_key_id(client_key),
    )
    return {
        "mfa_required": False,
        "user": UserResponse.model_validate(user),
        "csrf_token": csrf_token,
    }


@router.post("/mfa/totp/verify")
def verify_totp_login(
    payload: MfaCodeRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    user_id = verify_mfa_login_state(request.cookies.get(mfa_challenge_cookie_name(), ""))
    if user_id is None:
        raise_invalid_login()

    client_key = f"ip:{normalize_client_ip(request.client.host if request.client else None)}"
    account_key = f"user:{user_id}"
    ensure_rate_limit_available(
        db,
        "mfa-ip",
        client_key,
        settings.mfa_ip_rate_limit,
        settings.mfa_rate_limit_window_seconds,
    )
    ensure_rate_limit_available(
        db,
        "mfa-account",
        account_key,
        settings.mfa_rate_limit,
        settings.mfa_rate_limit_window_seconds,
    )

    try:
        with shared_user_operation(db, user_id) as user:
            credential = db.scalar(
                select(UserTotpCredential)
                .where(
                    UserTotpCredential.user_id == user_id,
                    UserTotpCredential.enabled_at.is_not(None),
                )
                .with_for_update()
            )
            if credential is None or not consume_mfa_factor(
                db,
                credential,
                payload.code,
            ):
                _reject_mfa_login(db, request, client_key, account_key)

            db.commit()
            clear_rate_limit(db, "mfa-ip", client_key)
            clear_rate_limit(db, "mfa-account", account_key)
            session, raw_token, csrf_token = create_session(db, user)
    except InactiveUserOperation:
        _reject_mfa_login(db, request, client_key, account_key)
    _set_session_cookie(response, session, raw_token)
    _delete_mfa_challenge_cookie(response)
    _log_login(request, "succeeded_with_mfa", client_key, account_key)
    return {
        "mfa_required": False,
        "user": UserResponse.model_validate(user),
        "csrf_token": csrf_token,
    }


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
    log_security_event(
        "auth.session.logged_out",
        actor_ref=security_reference("user", user.id),
    )


@router.post("/password", status_code=204)
def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> None:
    user_key = f"user:{user.id}"
    ensure_rate_limit_available(
        db,
        "password-change",
        user_key,
        settings.password_change_rate_limit,
        settings.password_change_rate_limit_window_seconds,
    )

    if not verify_password(user.password_hash, payload.current_password):
        check_rate_limit(
            db,
            "password-change",
            user_key,
            settings.password_change_rate_limit,
            settings.password_change_rate_limit_window_seconds,
        )
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
