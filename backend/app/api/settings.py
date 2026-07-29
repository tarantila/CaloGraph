import logging
from datetime import date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

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
    NutritionTarget,
    TrackingOverride,
    TrackingQualitySettings,
    User,
    UserTotpCredential,
)
from app.schemas import (
    MfaCodeRequest,
    MfaManagementRequest,
    MfaStatusResponse,
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
)
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
from app.services.rate_limit import (
    check_rate_limit,
    clear_rate_limit,
    ensure_rate_limit_available,
    rate_limit_key_id,
)

router = APIRouter(prefix="/settings", tags=["Einstellungen"])
logger = logging.getLogger("calograph.auth")


def _mfa_management_key(user: User) -> str:
    return f"user:{user.id}"


def _mfa_log_user_key(user: User) -> str:
    return rate_limit_key_id(_mfa_management_key(user))


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


@router.get("/profile", response_model=UserResponse)
def profile(user: User = Depends(current_user)) -> User:
    return user


@router.put("/profile", response_model=UserResponse)
def update_profile(
    payload: ProfileUpdate,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> User:
    try:
        ZoneInfo(payload.timezone)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(status_code=422, detail="Unbekannte IANA-Zeitzone") from exc
    user.timezone = payload.timezone
    user.week_starts_on = payload.week_starts_on
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
    logger.info(
        "security_event=mfa_totp_setup_started user_key=%s",
        _mfa_log_user_key(user),
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
    logger.info(
        "security_event=mfa_totp_enabled user_key=%s",
        _mfa_log_user_key(user),
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
    logger.info(
        "security_event=mfa_recovery_codes_replaced user_key=%s",
        _mfa_log_user_key(user),
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
    logger.info(
        "security_event=mfa_totp_disabled user_key=%s",
        _mfa_log_user_key(user),
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


@router.post("/targets", response_model=TargetResponse, status_code=201)
def create_target(
    payload: TargetInput,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> NutritionTarget:
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
    target = db.scalar(
        select(NutritionTarget).where(
            NutritionTarget.user_id == user.id,
            NutritionTarget.valid_from == valid_from,
        )
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Budget- und Zielversion nicht gefunden")
    for field, value in payload.model_dump(exclude={"valid_from"}).items():
        setattr(target, field, value)
    db.commit()
    db.refresh(target)
    return target


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
    return TokenCreatedResponse(
        id=token.id, label=token.label, token=raw, expires_at=token.expires_at
    )


@router.delete("/tokens/{token_id}", status_code=204)
def revoke_token(
    token_id: str, user: User = Depends(require_csrf), db: Session = Depends(get_db)
) -> None:
    token = db.scalar(select(ApiToken).where(ApiToken.id == token_id, ApiToken.user_id == user.id))
    if not token:
        raise HTTPException(status_code=404, detail="Token nicht gefunden")
    from datetime import UTC, datetime

    token.revoked_at = datetime.now(UTC)
    db.commit()


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
