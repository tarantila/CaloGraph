from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import current_user, require_csrf
from app.config import settings
from app.database import get_db
from app.models import User, YazioConnection
from app.schemas import ImportSummary, YazioConnectionInput, YazioStatusResponse
from app.services.credential_crypto import CredentialEncryptionError
from app.services.rate_limit import check_rate_limit, normalize_client_ip
from app.services.yazio_sync import (
    YazioAuthenticationError,
    YazioCircuitOpen,
    YazioConnectionNotConfigured,
    YazioDisabled,
    YazioOperationCapacityExceeded,
    YazioOperationDeadlineExceeded,
    YazioSyncError,
    configure_yazio_connection,
    run_manual_yazio_sync,
    validate_yazio_credentials,
)

router = APIRouter(prefix="/yazio", tags=["YAZIO"])


def _rate_limit_yazio_action(
    db: Session,
    request: Request,
    user: User,
) -> None:
    client = normalize_client_ip(request.client.host if request.client else None)
    check_rate_limit(
        db,
        "yazio-action-ip",
        f"ip:{client}",
        settings.yazio_rate_limit,
        settings.yazio_rate_limit_window_seconds,
    )
    check_rate_limit(
        db,
        "yazio-action-user",
        f"user:{user.id}",
        settings.yazio_rate_limit,
        settings.yazio_rate_limit_window_seconds,
    )


def _raise_yazio_http_error(exc: YazioSyncError) -> NoReturn:
    if isinstance(exc, YazioAuthenticationError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(exc, YazioOperationDeadlineExceeded):
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    if isinstance(exc, YazioOperationCapacityExceeded):
        raise HTTPException(
            status_code=429,
            detail=str(exc),
            headers={"Retry-After": "30"},
        ) from exc
    if isinstance(exc, YazioCircuitOpen):
        raise HTTPException(
            status_code=503,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    if isinstance(exc, YazioDisabled):
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.put("/connection", response_model=YazioStatusResponse)
def save_yazio_connection(
    payload: YazioConnectionInput,
    request: Request,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> YazioStatusResponse:
    if not settings.yazio_enabled:
        _raise_yazio_http_error(
            YazioDisabled("Die YAZIO-Funktion ist auf diesem Server deaktiviert.")
        )
    _rate_limit_yazio_action(db, request, user)
    try:
        validate_yazio_credentials(
            payload.email,
            payload.password,
            operation_key=user.id,
        )
        connection = configure_yazio_connection(
            user,
            payload.email,
            payload.password,
            sync_interval_minutes=payload.interval_hours * 60,
            sync_days=payload.sync_days,
        )
    except CredentialEncryptionError as exc:
        raise HTTPException(
            status_code=503,
            detail="Verschlüsselung für YAZIO-Verbindungen ist nicht eingerichtet.",
        ) from exc
    except YazioSyncError as exc:
        _raise_yazio_http_error(exc)
    return YazioStatusResponse(
        available=True,
        configured=True,
        sync_enabled=True,
        sync_interval_minutes=connection.sync_interval_minutes,
        sync_days=connection.sync_days,
        next_sync_at=connection.next_sync_at,
    )


@router.get("/status", response_model=YazioStatusResponse)
def yazio_status(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> YazioStatusResponse:
    connection = db.scalar(
        select(YazioConnection).where(YazioConnection.user_id == user.id)
    )
    if connection is None:
        return YazioStatusResponse(
            available=settings.yazio_enabled,
            configured=False,
            sync_enabled=False,
        )
    return YazioStatusResponse(
        available=settings.yazio_enabled,
        configured=True,
        sync_enabled=connection.sync_enabled,
        sync_interval_minutes=connection.sync_interval_minutes,
        sync_days=connection.sync_days,
        last_attempt_at=connection.last_attempt_at,
        last_success_at=connection.last_success_at,
        next_sync_at=connection.next_sync_at,
        last_error=connection.last_error,
    )


@router.post("/sync", response_model=ImportSummary)
def sync_yazio_now(
    request: Request,
    days: int | None = Query(default=None, ge=1, le=366),
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> ImportSummary:
    if not settings.yazio_enabled:
        _raise_yazio_http_error(
            YazioDisabled("Die YAZIO-Funktion ist auf diesem Server deaktiviert.")
        )
    _rate_limit_yazio_action(db, request, user)
    try:
        return run_manual_yazio_sync(user.id, sync_days=days)
    except YazioConnectionNotConfigured as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except YazioSyncError as exc:
        _raise_yazio_http_error(exc)
