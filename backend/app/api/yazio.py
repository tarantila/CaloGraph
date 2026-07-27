from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import current_user, require_csrf
from app.database import get_db
from app.models import User, YazioConnection
from app.schemas import ImportSummary, YazioConnectionInput, YazioStatusResponse
from app.services.credential_crypto import CredentialEncryptionError
from app.services.rate_limit import check_rate_limit
from app.services.yazio_sync import (
    YazioConnectionNotConfigured,
    YazioSyncError,
    configure_yazio_connection,
    fetch_yazio_payload,
    run_manual_yazio_sync,
)

router = APIRouter(prefix="/yazio", tags=["YAZIO"])


@router.put("/connection", response_model=YazioStatusResponse)
def save_yazio_connection(
    payload: YazioConnectionInput,
    user: User = Depends(require_csrf),
) -> YazioStatusResponse:
    try:
        today = datetime.now(ZoneInfo(user.timezone)).date()
        fetch_yazio_payload(
            payload.email,
            payload.password,
            today,
            today,
            include_micronutrients=False,
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
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return YazioStatusResponse(
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
        return YazioStatusResponse(configured=False, sync_enabled=False)
    return YazioStatusResponse(
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
    days: int | None = Query(default=None, ge=1, le=366),
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> ImportSummary:
    check_rate_limit(db, "yazio-manual-sync", str(user.id), 2)
    try:
        return run_manual_yazio_sync(user.id, sync_days=days)
    except YazioConnectionNotConfigured as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except YazioSyncError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
