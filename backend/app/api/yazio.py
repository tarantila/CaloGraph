from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import current_user, require_csrf
from app.config import settings
from app.database import get_db
from app.models import User, YazioConnection
from app.schemas import (
    ImportSummary,
    YazioConnectionInput,
    YazioHistoricalRangeInput,
    YazioHistoricalSyncResponse,
    YazioStatusResponse,
)
from app.security_events import log_security_event, security_reference
from app.services.credential_crypto import CredentialEncryptionError
from app.services.rate_limit import check_rate_limit, normalize_client_ip
from app.services.user_operation_lock import shared_user_operation
from app.services.yazio_sync import (
    YazioAuthenticationError,
    YazioCircuitOpen,
    YazioConnectionDisabled,
    YazioConnectionNotConfigured,
    YazioDisabled,
    YazioInvalidResponseError,
    YazioNetworkTimeoutError,
    YazioOperationCapacityExceeded,
    YazioOperationDeadlineExceeded,
    YazioRateLimitedError,
    YazioSyncError,
    YazioUnavailableError,
    YazioVersionBlockedError,
    configure_yazio_connection,
    effective_sync_days,
    effective_sync_interval_minutes,
    enqueue_historical_yazio_sync,
    run_manual_yazio_sync,
    validate_yazio_credentials,
    yazio_failure_reason,
)

router = APIRouter(prefix="/yazio", tags=["YAZIO"])


def _rate_limit_yazio_action(
    db: Session,
    request: Request,
    user: User,
) -> None:
    with shared_user_operation(db, user.id):
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
    if isinstance(exc, YazioConnectionDisabled):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, YazioRateLimitedError):
        headers = (
            {"Retry-After": str(exc.retry_after)}
            if exc.retry_after is not None
            else {}
        )
        raise HTTPException(status_code=429, detail=str(exc), headers=headers) from exc
    if isinstance(exc, YazioOperationDeadlineExceeded | YazioNetworkTimeoutError):
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
    if isinstance(
        exc,
        YazioVersionBlockedError | YazioUnavailableError | YazioInvalidResponseError,
    ):
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
            sync_interval_minutes=payload.interval_hours * 60 if payload.interval_hours is not None else None,
            sync_days=payload.sync_days,
            start_day=payload.from_date,
            end_day=payload.end_date,
        )
    except CredentialEncryptionError as exc:
        log_security_event(
            "integration.yazio.connection_failed",
            actor_ref=security_reference("user", user.id),
            reason="credential_encryption_unavailable",
        )
        raise HTTPException(
            status_code=503,
            detail="Verschlüsselung für YAZIO-Verbindungen ist nicht eingerichtet.",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except YazioSyncError as exc:
        log_security_event(
            "integration.yazio.connection_failed",
            actor_ref=security_reference("user", user.id),
            reason=yazio_failure_reason(exc),
        )
        _raise_yazio_http_error(exc)
    log_security_event(
        "integration.yazio.connection_configured",
        actor_ref=security_reference("user", user.id),
        target_ref=security_reference("yazio_connection", connection.id),
    )
    return _status_response(connection)


def _status_response(connection: YazioConnection | None) -> YazioStatusResponse:
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
        sync_interval_minutes=effective_sync_interval_minutes(connection),
        sync_days=effective_sync_days(connection),
        sync_interval_override_minutes=connection.sync_interval_minutes,
        sync_days_override=connection.sync_days,
        historical_sync=YazioHistoricalSyncResponse(
            state=connection.historical_sync_state,
            start_date=connection.historical_sync_start_date,
            end_date=connection.historical_sync_end_date,
            started_at=connection.historical_sync_started_at,
            completed_at=connection.historical_sync_completed_at,
            last_error=connection.historical_sync_last_error,
        ),
        last_attempt_at=connection.last_attempt_at,
        last_success_at=connection.last_success_at,
        next_sync_at=connection.next_sync_at,
        last_error=connection.last_error,
    )


@router.get("/status", response_model=YazioStatusResponse)
def yazio_status(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> YazioStatusResponse:
    connection = db.scalar(
        select(YazioConnection).where(YazioConnection.user_id == user.id)
    )
    return _status_response(connection)


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
        log_security_event(
            "integration.yazio.sync_failed",
            actor_ref=security_reference("user", user.id),
            reason="connection_not_configured",
            details={"mode": "manual"},
        )
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except YazioSyncError as exc:
        _raise_yazio_http_error(exc)



@router.post("/sync/history/range", response_model=YazioStatusResponse)
def sync_yazio_history_range(
    payload: YazioHistoricalRangeInput,
    request: Request,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> YazioStatusResponse:
    _rate_limit_yazio_action(db, request, user)
    try:
        connection = enqueue_historical_yazio_sync(
            user.id,
            start_day=payload.from_date,
            end_day=payload.end_date,
        )
    except YazioConnectionNotConfigured as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except YazioSyncError as exc:
        _raise_yazio_http_error(exc)
    log_security_event(
        "integration.yazio.history_queued",
        actor_ref=security_reference("user", user.id),
        target_ref=security_reference("yazio_connection", connection.id),
        details={"mode": "range"},
    )
    return _status_response(connection)
