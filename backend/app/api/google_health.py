from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
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
    status = (
        429
        if exc.code == "rate_limited"
        else 502
        if exc.code in {"transient_error", "provider_error"}
        else exc.status_code
    )
    return HTTPException(status_code=status, detail=detail)


def _wants_spa_redirect(request: Request) -> bool:
    qualities: dict[str, float] = {}
    for raw_item in request.headers.get("accept", "").split(","):
        parts = [part.strip() for part in raw_item.split(";")]
        media_type = parts[0].lower()
        if media_type not in {"text/html", "application/json"}:
            continue
        quality = 1.0
        for parameter in parts[1:]:
            name, separator, value = parameter.partition("=")
            if name.strip().lower() != "q" or not separator:
                continue
            try:
                quality = max(0.0, min(1.0, float(value.strip())))
            except ValueError:
                quality = 0.0
        qualities[media_type] = max(qualities.get(media_type, 0.0), quality)
    html_quality = qualities.get("text/html", 0.0)
    json_quality = qualities.get("application/json", 0.0)
    return html_quality > 0 and html_quality > json_quality


def _login_spa_redirect() -> RedirectResponse:
    return RedirectResponse(
        url="/login?next=/konto/integrationen&google_health=error",
        status_code=303,
    )


def _oauth_spa_redirect(result: str) -> RedirectResponse:
    return RedirectResponse(
        url=f"/konto/integrationen?google_health={result}",
        status_code=303,
    )


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


@router.get("/oauth/callback", response_model=None)
def google_health_oauth_callback(
    request: Request,
    state: str | None = Query(default=None, max_length=512),
    code: str | None = Query(default=None, max_length=4096),
    error: str | None = Query(default=None, max_length=128),
    db: Session = Depends(get_db),
) -> GoogleHealthStatus | RedirectResponse:
    try:
        user = current_user(request, db)
    except HTTPException as exc:
        if _wants_spa_redirect(request) and exc.status_code == 401:
            return _login_spa_redirect()
        raise
    try:
        status = complete_google_health_oauth(
            db,
            user,
            state=state,
            code=code,
            error=error,
            request=request,
        )
    except GoogleHealthDisabledError as exc:
        if _wants_spa_redirect(request):
            return _oauth_spa_redirect("error")
        raise HTTPException(status_code=404, detail="Google Health ist nicht verfügbar.") from exc
    except GoogleHealthOAuthError as exc:
        if _wants_spa_redirect(request):
            return _oauth_spa_redirect("error")
        raise _oauth_error(exc) from exc
    if _wants_spa_redirect(request):
        return _oauth_spa_redirect("connected" if status.state == "active" else "error")
    return status
