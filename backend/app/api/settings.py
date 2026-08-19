from datetime import date
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.activity import ACTIVE_ENERGY_METRIC, ACTIVITY_SOURCE_TYPES
from app.auth.dependencies import current_user, require_csrf
from app.auth.security import (
    create_api_token,
    hash_session_token,
    revoke_other_user_sessions,
    session_cookie_name,
    verify_password,
)
from app.config import settings
from app.database import get_db
from app.models import (
    ApiToken,
    HealthSample,
    NutritionTarget,
    PasskeyCredential,
    TrackingOverride,
    TrackingQualitySettings,
    User,
    UserSession,
    UserTotpCredential,
)
from app.problem_types import (
    ACTIVITY_SOURCE_UNAVAILABLE,
    INVALID_MFA,
    INVALID_TIMEZONE,
    LAST_TARGET_REQUIRED,
    TARGET_VERSION_NOT_FOUND,
    ProblemHTTPException,
)
from app.schemas import (
    ActivitySourceResponse,
    MfaCodeRequest,
    MfaManagementRequest,
    MfaStatusResponse,
    PasskeyDeleteRequest,
    PasskeyRegistrationCompleteRequest,
    PasskeyRegistrationOptionsRequest,
    PasskeyResponse,
    ProfileUpdate,
    RecoveryCodesResponse,
    TargetInput,
    TargetResponse,
    TokenCreatedResponse,
    TokenCreateRequest,
    TokenResponse,
    TotpSetupRequest,
    TotpSetupResponse,
    TrackingOverrideInput,
    TrackingQualityInput,
    TrackingQualityResponse,
    UserResponse,
    WebAuthnOptionsResponse,
)
from app.security_events import log_security_event, security_reference
from app.services.mfa import (
    MfaSetupError,
    begin_totp_setup,
    confirm_totp_setup,
    consume_mfa_factor,
    disable_totp,
    regenerate_recovery_codes,
    totp_status,
)
from app.services.mfa_crypto import MfaEncryptionError
from app.services.passkeys import (
    PasskeyRegistrationError,
    begin_passkey_registration,
    complete_passkey_registration,
    delete_passkey,
    list_passkeys,
)
from app.services.rate_limit import (
    check_rate_limit,
    clear_rate_limit,
    ensure_rate_limit_available,
)

router = APIRouter(prefix="/settings", tags=["Einstellungen"])


def _mfa_management_key(user: User) -> str:
    return f"user:{user.id}"


def _mfa_log_user_key(user: User) -> str:
    return security_reference("user", user.id)


def _verify_management_password(
    db: Session,
    user: User,
    password: str,
) -> None:
    key = _mfa_management_key(user)
    ensure_rate_limit_available(
        db,
        "mfa-management-password",
        key,
        settings.password_change_rate_limit,
        settings.password_change_rate_limit_window_seconds,
    )
    if verify_password(user.password_hash, password):
        clear_rate_limit(db, "mfa-management-password", key)
        return
    check_rate_limit(
        db,
        "mfa-management-password",
        key,
        settings.password_change_rate_limit,
        settings.password_change_rate_limit_window_seconds,
    )
    raise HTTPException(status_code=400, detail="Aktuelles Passwort ist falsch")


def _record_mfa_management_failure(db: Session, user: User) -> None:
    check_rate_limit(
        db,
        "mfa-management-factor",
        _mfa_management_key(user),
        settings.password_change_rate_limit,
        settings.password_change_rate_limit_window_seconds,
    )


def _ensure_mfa_management_factor_available(db: Session, user: User) -> None:
    ensure_rate_limit_available(
        db,
        "mfa-management-factor",
        _mfa_management_key(user),
        settings.password_change_rate_limit,
        settings.password_change_rate_limit_window_seconds,
    )


def _clear_mfa_management_factor_limit(db: Session, user: User) -> None:
    clear_rate_limit(db, "mfa-management-factor", _mfa_management_key(user))


def _preserve_only_current_session(
    request: Request,
    db: Session,
    user: User,
) -> None:
    raw_session = request.cookies.get(session_cookie_name(), "")
    revoke_other_user_sessions(
        db,
        user.id,
        hash_session_token(raw_session),
    )


def _current_session_id(request: Request, db: Session) -> UUID:
    raw_session = request.cookies.get(session_cookie_name(), "")
    session_id = db.scalar(
        select(UserSession.id).where(UserSession.token_hash == hash_session_token(raw_session))
    )
    if session_id is None:
        raise HTTPException(status_code=401, detail="Sitzung ungültig")
    return session_id


def _verify_management_second_factor_if_enabled(
    db: Session,
    user: User,
    code: str | None,
) -> None:
    credential = db.scalar(
        select(UserTotpCredential)
        .where(
            UserTotpCredential.user_id == user.id,
            UserTotpCredential.enabled_at.is_not(None),
        )
        .with_for_update()
    )
    if credential is None:
        return
    _ensure_mfa_management_factor_available(db, user)
    if not code or not consume_mfa_factor(db, credential, code):
        _record_mfa_management_failure(db, user)
        db.rollback()
        raise ProblemHTTPException(
            status_code=400,
            detail="MFA-Code ist ungültig",
            problem_type=INVALID_MFA,
        )
    db.commit()
    _clear_mfa_management_factor_limit(db, user)


@router.get("/profile", response_model=UserResponse)
def profile(user: User = Depends(current_user)) -> User:
    return user


@router.put("/profile", response_model=UserResponse)
def update_profile(
    payload: ProfileUpdate,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> User:
    if payload.timezone is not None:
        try:
            ZoneInfo(payload.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ProblemHTTPException(
                status_code=422,
                detail="Unbekannte IANA-Zeitzone",
                problem_type=INVALID_TIMEZONE,
            ) from exc
        user.timezone = payload.timezone
    if payload.language is not None:
        user.language = payload.language
    if payload.week_starts_on is not None:
        user.week_starts_on = payload.week_starts_on
    if payload.raw_payload_retention_days is not None:
        user.raw_payload_retention_days = payload.raw_payload_retention_days
    db.commit()
    db.refresh(user)
    return user


@router.get("/mfa", response_model=MfaStatusResponse)
def mfa_status(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> MfaStatusResponse:
    enabled, pending, remaining = totp_status(db, user.id)
    return MfaStatusResponse(
        totp_enabled=enabled,
        totp_setup_pending=pending,
        recovery_codes_remaining=remaining,
    )


@router.post("/mfa/totp/setup", response_model=TotpSetupResponse)
def setup_totp(
    payload: TotpSetupRequest,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> TotpSetupResponse:
    _verify_management_password(db, user, payload.current_password)
    try:
        setup = begin_totp_setup(db, user)
    except MfaSetupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except MfaEncryptionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    log_security_event(
        "auth.mfa.totp_setup_started",
        actor_ref=_mfa_log_user_key(user),
    )
    return TotpSetupResponse(
        secret=setup.secret,
        provisioning_uri=setup.provisioning_uri,
        qr_svg_data_url=setup.qr_svg_data_url,
    )


@router.post("/mfa/totp/confirm", response_model=RecoveryCodesResponse)
def confirm_totp(
    payload: MfaCodeRequest,
    request: Request,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> RecoveryCodesResponse:
    _ensure_mfa_management_factor_available(db, user)
    try:
        recovery_codes = confirm_totp_setup(db, user.id, payload.code)
    except MfaSetupError as exc:
        _record_mfa_management_failure(db, user)
        raise HTTPException(status_code=400, detail=str(exc)) from None
    _clear_mfa_management_factor_limit(db, user)
    _preserve_only_current_session(request, db, user)
    log_security_event(
        "auth.mfa.totp_enabled",
        actor_ref=_mfa_log_user_key(user),
    )
    return RecoveryCodesResponse(recovery_codes=recovery_codes)


@router.post("/mfa/totp/recovery-codes", response_model=RecoveryCodesResponse)
def replace_totp_recovery_codes(
    payload: MfaManagementRequest,
    request: Request,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> RecoveryCodesResponse:
    _verify_management_password(db, user, payload.current_password)
    _ensure_mfa_management_factor_available(db, user)
    credential = db.scalar(
        select(UserTotpCredential)
        .where(
            UserTotpCredential.user_id == user.id,
            UserTotpCredential.enabled_at.is_not(None),
        )
        .with_for_update()
    )
    if credential is None or not consume_mfa_factor(db, credential, payload.code):
        db.rollback()
        _record_mfa_management_failure(db, user)
        raise HTTPException(status_code=400, detail="MFA-Code ist ungültig")
    recovery_codes = regenerate_recovery_codes(db, user.id)
    _clear_mfa_management_factor_limit(db, user)
    _preserve_only_current_session(request, db, user)
    log_security_event(
        "auth.mfa.recovery_codes_replaced",
        actor_ref=_mfa_log_user_key(user),
    )
    return RecoveryCodesResponse(recovery_codes=recovery_codes)


@router.delete("/mfa/totp", status_code=204)
def remove_totp(
    payload: MfaManagementRequest,
    request: Request,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> None:
    _verify_management_password(db, user, payload.current_password)
    _ensure_mfa_management_factor_available(db, user)
    credential = db.scalar(
        select(UserTotpCredential)
        .where(
            UserTotpCredential.user_id == user.id,
            UserTotpCredential.enabled_at.is_not(None),
        )
        .with_for_update()
    )
    if credential is None or not consume_mfa_factor(db, credential, payload.code):
        db.rollback()
        _record_mfa_management_failure(db, user)
        raise HTTPException(status_code=400, detail="MFA-Code ist ungültig")
    disable_totp(db, user.id)
    _clear_mfa_management_factor_limit(db, user)
    _preserve_only_current_session(request, db, user)
    log_security_event(
        "auth.mfa.totp_disabled",
        actor_ref=_mfa_log_user_key(user),
    )


@router.get("/passkeys", response_model=list[PasskeyResponse])
def passkeys(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[PasskeyCredential]:
    return list_passkeys(db, user.id)


@router.post("/passkeys/options", response_model=WebAuthnOptionsResponse)
def passkey_registration_options(
    payload: PasskeyRegistrationOptionsRequest,
    request: Request,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> WebAuthnOptionsResponse:
    _verify_management_password(db, user, payload.current_password)
    _verify_management_second_factor_if_enabled(db, user, payload.code)
    challenge_id, public_key = begin_passkey_registration(
        db,
        user,
        _current_session_id(request, db),
    )
    log_security_event(
        "auth.passkey.registration_started",
        actor_ref=_mfa_log_user_key(user),
    )
    return WebAuthnOptionsResponse(
        challenge_id=challenge_id,
        public_key=public_key,
    )


@router.post("/passkeys", response_model=PasskeyResponse, status_code=201)
def register_passkey(
    payload: PasskeyRegistrationCompleteRequest,
    request: Request,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> PasskeyCredential:
    try:
        passkey = complete_passkey_registration(
            db,
            user,
            _current_session_id(request, db),
            payload.challenge_id,
            payload.label,
            payload.credential,
        )
    except PasskeyRegistrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    _preserve_only_current_session(request, db, user)
    log_security_event(
        "auth.passkey.registered",
        actor_ref=_mfa_log_user_key(user),
        target_ref=security_reference("passkey", passkey.id),
    )
    return passkey


@router.delete("/passkeys/{passkey_id}", status_code=204)
def remove_passkey(
    passkey_id: UUID,
    payload: PasskeyDeleteRequest,
    request: Request,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> None:
    _verify_management_password(db, user, payload.current_password)
    _verify_management_second_factor_if_enabled(db, user, payload.code)
    if not delete_passkey(db, user.id, passkey_id):
        raise HTTPException(status_code=404, detail="Passkey nicht gefunden")
    _preserve_only_current_session(request, db, user)
    log_security_event(
        "auth.passkey.removed",
        actor_ref=_mfa_log_user_key(user),
        target_ref=security_reference("passkey", passkey_id),
    )


@router.get("/targets", response_model=list[TargetResponse])
def targets(
    user: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[NutritionTarget]:
    return list(
        db.scalars(
            select(NutritionTarget)
            .where(NutritionTarget.user_id == user.id)
            .order_by(NutritionTarget.valid_from.desc())
        )
    )


@router.get("/activity-sources", response_model=list[ActivitySourceResponse])
def activity_sources(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[ActivitySourceResponse]:
    return [
        ActivitySourceResponse(source_type=source_type)
        for source_type in _available_activity_sources(db, user.id)
    ]


def _lock_target_owner(db: Session, user_id: UUID) -> None:
    db.scalar(select(User).where(User.id == user_id).with_for_update())

def _available_activity_sources(db: Session, user_id: UUID) -> list[str]:
    return list(
        db.scalars(
            select(HealthSample.source_type)
            .where(
                HealthSample.user_id == user_id,
                HealthSample.metric_type == ACTIVE_ENERGY_METRIC,
                HealthSample.source_type.in_(ACTIVITY_SOURCE_TYPES),
            )
            .distinct()
            .order_by(HealthSample.source_type)
        )
    )


def _validate_activity_source(
    db: Session,
    user: User,
    payload: TargetInput,
    *,
    existing_source: str | None = None,
) -> None:
    if payload.activity_mode != "full":
        return
    source_type = payload.activity_source_type
    if source_type == existing_source:
        return
    if source_type not in _available_activity_sources(db, user.id):
        raise ProblemHTTPException(
            status_code=422,
            detail="Aktivitätsquelle ist nicht verfügbar",
            problem_type=ACTIVITY_SOURCE_UNAVAILABLE,
        )


def _log_activity_target_change(target: NutritionTarget, user: User) -> None:
    details: dict[str, str] = {"activity_mode": target.activity_mode}
    if target.activity_source_type is not None:
        details["activity_source_type"] = target.activity_source_type
    log_security_event(
        "settings.target.activity_configured",
        actor_ref=security_reference("user", user.id),
        target_ref=security_reference("nutrition_target", target.id),
        details=details,
    )


@router.post("/targets", response_model=TargetResponse, status_code=201)
def create_target(
    payload: TargetInput,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> NutritionTarget:
    _lock_target_owner(db, user.id)
    _validate_activity_source(db, user, payload)
    existing = list(
        db.scalars(
            select(NutritionTarget)
            .where(NutritionTarget.user_id == user.id)
            .order_by(NutritionTarget.valid_from)
        )
    )
    if any(item.valid_from == payload.valid_from for item in existing):
        raise HTTPException(status_code=409, detail="Für dieses Datum besteht bereits ein Ziel")
    later = next((item for item in existing if item.valid_from > payload.valid_from), None)
    previous = next(
        (item for item in reversed(existing) if item.valid_from < payload.valid_from), None
    )
    if previous:
        previous.valid_to = payload.valid_from
    target = NutritionTarget(
        user_id=user.id,
        valid_to=later.valid_from if later else None,
        **payload.model_dump(),
    )
    db.add(target)
    db.commit()
    db.refresh(target)
    _log_activity_target_change(target, user)
    return target


@router.put("/targets/{valid_from}", response_model=TargetResponse)
def update_target(
    valid_from: date,
    payload: TargetInput,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> NutritionTarget:
    if payload.valid_from != valid_from:
        raise HTTPException(status_code=422, detail="Datum im Pfad und Inhalt stimmt nicht überein")
    _lock_target_owner(db, user.id)
    target = db.scalar(
        select(NutritionTarget).where(
            NutritionTarget.user_id == user.id,
            NutritionTarget.valid_from == valid_from,
        )
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Budget- und Zielversion nicht gefunden")
    _validate_activity_source(
        db,
        user,
        payload,
        existing_source=target.activity_source_type,
    )
    for field, value in payload.model_dump(exclude={"valid_from"}).items():
        setattr(target, field, value)
    db.commit()
    db.refresh(target)
    _log_activity_target_change(target, user)
    return target


@router.delete("/targets/{valid_from}", status_code=204)
def delete_target(
    valid_from: date,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> None:
    _lock_target_owner(db, user.id)
    targets = list(
        db.scalars(
            select(NutritionTarget)
            .where(NutritionTarget.user_id == user.id)
            .order_by(NutritionTarget.valid_from)
            .with_for_update()
        )
    )
    target_index = next(
        (index for index, item in enumerate(targets) if item.valid_from == valid_from),
        None,
    )
    if target_index is None:
        raise ProblemHTTPException(
            status_code=404,
            detail="Budget- und Zielversion nicht gefunden",
            problem_type=TARGET_VERSION_NOT_FOUND,
        )
    if len(targets) == 1:
        raise ProblemHTTPException(
            status_code=409,
            detail="Mindestens eine Budget- und Zielversion muss bestehen bleiben",
            problem_type=LAST_TARGET_REQUIRED,
        )
    previous = targets[target_index - 1] if target_index > 0 else None
    successor = targets[target_index + 1] if target_index + 1 < len(targets) else None
    if previous is not None:
        previous.valid_to = successor.valid_from if successor is not None else None
    db.delete(targets[target_index])
    db.commit()


@router.get("/tracking-quality", response_model=TrackingQualityResponse)
def tracking_quality(
    user: User = Depends(current_user), db: Session = Depends(get_db)
) -> TrackingQualitySettings:
    quality = db.get(TrackingQualitySettings, user.id)
    if quality is None:
        quality = TrackingQualitySettings(user_id=user.id)
        db.add(quality)
        db.commit()
        db.refresh(quality)
    return quality


@router.put("/tracking-quality", response_model=TrackingQualityResponse)
def update_tracking_quality(
    payload: TrackingQualityInput,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> TrackingQualitySettings:
    if payload.calories_partial_ratio > payload.calories_full_ratio:
        raise HTTPException(
            status_code=422, detail="Teil-Schwelle darf Voll-Schwelle nicht übersteigen"
        )
    if payload.median_partial_ratio > payload.median_full_ratio:
        raise HTTPException(status_code=422, detail="Median-Teil-Schwelle ist zu hoch")
    if not (
        payload.complete_score > payload.probably_complete_score > payload.probably_incomplete_score
    ):
        raise HTTPException(status_code=422, detail="Status-Punktgrenzen müssen streng absteigen")
    quality = db.get(TrackingQualitySettings, user.id)
    if quality is None:
        quality = TrackingQualitySettings(user_id=user.id)
        db.add(quality)
    for field, value in payload.model_dump().items():
        setattr(quality, field, value)
    db.commit()
    db.refresh(quality)
    return quality


@router.get("/tokens", response_model=list[TokenResponse])
def tokens(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[ApiToken]:
    return list(
        db.scalars(
            select(ApiToken).where(ApiToken.user_id == user.id).order_by(ApiToken.created_at.desc())
        )
    )


@router.post("/tokens", response_model=TokenCreatedResponse, status_code=201)
def new_token(
    payload: TokenCreateRequest,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> TokenCreatedResponse:
    token, raw = create_api_token(db, user, payload.label, payload.expires_at)
    log_security_event(
        "auth.api_token.created",
        actor_ref=security_reference("user", user.id),
        target_ref=security_reference("api_token", token.id),
    )
    return TokenCreatedResponse(
        id=token.id, label=token.label, token=raw, expires_at=token.expires_at
    )


@router.delete("/tokens/{token_id}", status_code=204)
def revoke_token(
    token_id: UUID, user: User = Depends(require_csrf), db: Session = Depends(get_db)
) -> None:
    token = db.scalar(select(ApiToken).where(ApiToken.id == token_id, ApiToken.user_id == user.id))
    if not token:
        raise HTTPException(status_code=404, detail="Token nicht gefunden")
    from datetime import UTC, datetime

    token.revoked_at = datetime.now(UTC)
    db.commit()
    log_security_event(
        "auth.api_token.revoked",
        actor_ref=security_reference("user", user.id),
        target_ref=security_reference("api_token", token.id),
    )


@router.put("/tracking/{day}", status_code=204)
def set_tracking_override(
    day: str,
    payload: TrackingOverrideInput,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> None:
    from datetime import date

    try:
        local_day = date.fromisoformat(day)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Ungültiges Datum") from exc
    item = db.scalar(
        select(TrackingOverride).where(
            TrackingOverride.user_id == user.id, TrackingOverride.local_date == local_day
        )
    )
    if item:
        item.status = payload.status
        item.note = payload.note
    else:
        db.add(
            TrackingOverride(
                user_id=user.id, local_date=local_day, status=payload.status, note=payload.note
            )
        )
    db.commit()
