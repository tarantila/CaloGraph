from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.auth.dependencies import current_user, require_csrf
from app.database import get_db
from app.google_health.errors import GoogleHealthDisabledError, GoogleHealthOAuthError
from app.google_health.service import (
    complete_google_health_oauth,
    google_health_status,
    start_google_health_oauth,
)
from app.models import User
from app.schemas_google_health import GoogleHealthOAuthStartResponse, GoogleHealthStatus

router = APIRouter(prefix="/google-health", tags=["Google Health"])


def _oauth_error(exc: GoogleHealthOAuthError) -> HTTPException:
    detail = {
        "invalid_state": "OAuth-Anfrage ist ungültig.",
        "unknown_state": "OAuth-Anfrage ist ungültig oder abgelaufen.",
        "expired_state": "OAuth-Anfrage ist ungültig oder abgelaufen.",
        "replayed_state": "OAuth-Anfrage wurde bereits verwendet.",
        "scope_missing": "Die erforderliche schreibgeschützte Berechtigung wurde nicht erteilt.",
        "reauth_required": "Google Health muss erneut autorisiert werden.",
        "rate_limited": "Google Health ist vorübergehend nicht verfügbar.",
        "transient_error": "Google Health ist vorübergehend nicht verfügbar.",
        "provider_error": "Google Health ist vorübergehend nicht verfügbar.",
        "invalid_response": "Google Health-Antwort ist ungültig.",
        "credential_unavailable": "Google Health ist derzeit nicht verfügbar.",
    }.get(exc.code, "Google Health-Anfrage fehlgeschlagen.")
    status = 429 if exc.code == "rate_limited" else 502 if exc.code in {"transient_error", "provider_error"} else exc.status_code
    return HTTPException(status_code=status, detail=detail)


@router.get("/status", response_model=GoogleHealthStatus)
def google_health_status_route(
    user: User = Depends(current_user), db: Session = Depends(get_db)
) -> GoogleHealthStatus:
    return google_health_status(db, user)


@router.post("/oauth/start", response_model=GoogleHealthOAuthStartResponse)
def google_health_oauth_start(
    request: Request,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> GoogleHealthOAuthStartResponse:
    try:
        url = start_google_health_oauth(
            db,
            user,
            client_ip=request.client.host if request.client else None,
            lock=False,
        )
    except GoogleHealthDisabledError as exc:
        raise HTTPException(status_code=404, detail="Google Health ist nicht verfügbar.") from exc
    except GoogleHealthOAuthError as exc:
        raise _oauth_error(exc) from exc
    return GoogleHealthOAuthStartResponse(authorization_url=url)
@router.get("/oauth/callback", response_model=GoogleHealthStatus)
def google_health_oauth_callback(
    request: Request,
    state: str | None = Query(default=None, max_length=512),
    code: str | None = Query(default=None, max_length=4096),
    error: str | None = Query(default=None, max_length=128),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> GoogleHealthStatus:
    try:
        return complete_google_health_oauth(
            db,
            user,
            state=state,
            code=code,
            error=error,
            request=request,
        )
    except GoogleHealthDisabledError as exc:
        raise HTTPException(status_code=404, detail="Google Health ist nicht verfügbar.") from exc
    except GoogleHealthOAuthError as exc:
        raise _oauth_error(exc) from exc
