from __future__ import annotations

from datetime import datetime, timedelta
from typing import NoReturn
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth.dependencies import current_user, require_csrf, require_csrf_exclusive
from app.config import settings
from app.database import SessionLocal, get_db
from app.google_health.errors import GoogleHealthDisabledError, GoogleHealthOAuthError
from app.google_health.service import (
    complete_google_health_oauth,
    delete_google_health_credentials,
    disconnect_google_health,
    google_health_status,
    save_google_health_credentials,
    start_google_health_oauth,
)
from app.google_health.credentials import GoogleHealthCredentialError
from app.models import User
from app.schemas_google_health import (
    GoogleHealthConnectionTestResponse,
    GoogleHealthCredentialsInput,
    GoogleHealthOAuthStartResponse,
    GoogleHealthStatus,
    GoogleHealthSyncResponse,
)
from app.services.google_health_connection_test import GoogleHealthConnectionTestService
from app.services.google_health_nutrition_sync import GoogleHealthNutritionSyncError
from app.services.google_health_sync import GoogleHealthSyncService
from app.services.rate_limit import check_rate_limit, normalize_client_ip
from app.services.user_operation_lock import shared_user_operation

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


def _rate_limit_google_health_sync(db: Session, request: Request, user: User) -> None:
    with shared_user_operation(db, user.id):
        client = normalize_client_ip(request.client.host if request.client else None)
        check_rate_limit(
            db,
            "google-health-sync-ip",
            f"ip:{client}",
            settings.reconcile_ip_rate_limit,
            settings.reconcile_rate_limit_window_seconds,
        )
        check_rate_limit(
            db,
            "google-health-sync-user",
            f"user:{user.id}",
            settings.reconcile_rate_limit,
            settings.reconcile_rate_limit_window_seconds,
        )


def _sync_error(exc: GoogleHealthNutritionSyncError) -> NoReturn:
    details = {
        "disabled": (404, "Google Health ist nicht verfügbar."),
        "not_configured": (409, "Google Health ist nicht konfiguriert."),
        "connection_not_configured": (
            404,
            "Google Health-Verbindung ist nicht eingerichtet.",
        ),
        "connection_inactive": (409, "Google Health muss erneut autorisiert werden."),
        "scope_missing": (
            409,
            "Die erforderliche schreibgeschützte Berechtigung wurde nicht erteilt.",
        ),
        "reauth_required": (409, "Google Health muss erneut autorisiert werden."),
        "rate_limited": (429, "Google Health ist vorübergehend nicht verfügbar."),
        "transient_error": (502, "Google Health ist vorübergehend nicht verfügbar."),
        "provider_error": (502, "Google Health ist vorübergehend nicht verfügbar."),
        "pagination_error": (502, "Google Health ist vorübergehend nicht verfügbar."),
        "invalid_response": (502, "Google Health ist vorübergehend nicht verfügbar."),
        "persistence_error": (503, "Google Health-Daten konnten nicht gespeichert werden."),
        "credential_decryption_error": (503, "Google Health ist derzeit nicht verfügbar."),
        "credentials_unavailable": (503, "Google Health ist derzeit nicht verfügbar."),
        "invalid_request": (422, "Der angeforderte Datumsbereich ist ungültig."),
    }
    status_code, detail = details.get(
        exc.code,
        (502, "Google Health ist vorübergehend nicht verfügbar."),
    )
    raise HTTPException(status_code=status_code, detail=detail) from exc


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


@router.post("/sync", response_model=GoogleHealthSyncResponse)
def google_health_sync(
    request: Request,
    days: int = Query(default=30, ge=1, le=366),
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> GoogleHealthSyncResponse:
    if not settings.google_health_enabled:
        raise HTTPException(status_code=404, detail="Google Health ist nicht verfügbar.")

    _rate_limit_google_health_sync(db, request, user)
    end = datetime.now(ZoneInfo(user.timezone)).date()
    start = end - timedelta(days=days - 1)
    try:
        result = GoogleHealthSyncService(session_factory=SessionLocal).sync(
            user_id=user.id,
            requested_start=start,
            requested_end=end,
        )
    except GoogleHealthDisabledError as exc:
        raise HTTPException(status_code=404, detail="Google Health ist nicht verfügbar.") from exc
    except GoogleHealthNutritionSyncError as exc:
        _sync_error(exc)
    return GoogleHealthSyncResponse(
        status=result.status,
        nutrition=result.nutrition,
        activity_energy=result.activity_energy,
        weight=result.weight,
    )


@router.post("/connection/test", response_model=GoogleHealthConnectionTestResponse)
def google_health_connection_test(
    user: User = Depends(require_csrf),
) -> GoogleHealthConnectionTestResponse:
    if not settings.google_health_enabled:
        raise HTTPException(status_code=404, detail="Google Health ist nicht verfügbar.")
    result = GoogleHealthConnectionTestService(session_factory=SessionLocal).test(
        user_id=user.id
    )
    return GoogleHealthConnectionTestResponse(
        status=result.status,
        error_category=result.error_category,
    )
@router.get("/status", response_model=GoogleHealthStatus)
def google_health_status_route(
    user: User = Depends(current_user), db: Session = Depends(get_db)
) -> GoogleHealthStatus:
    return google_health_status(db, user)


def _credential_error(exc: GoogleHealthCredentialError) -> HTTPException:
    detail = {
        "credential_required": "Google Health-Zugangsdaten sind erforderlich.",
        "credential_pair_required": "Client-ID und Client-Secret müssen gemeinsam gesetzt werden.",
        "credential_too_long": "Google Health-Zugangsdaten sind zu lang.",
        "credential_invalid": "Google Health-Zugangsdaten sind ungültig.",
        "credential_unavailable": "Google Health ist derzeit nicht verfügbar.",
    }.get(exc.code, "Google Health-Zugangsdaten sind ungültig.")
    return HTTPException(status_code=422, detail=detail)


@router.put("/credentials", response_model=GoogleHealthStatus)
def google_health_credentials_put(
    payload: GoogleHealthCredentialsInput,
    user: User = Depends(require_csrf_exclusive),
    db: Session = Depends(get_db),
) -> GoogleHealthStatus:
    if not settings.google_health_enabled:
        raise HTTPException(status_code=404, detail="Google Health ist nicht verfügbar.")
    try:
        return save_google_health_credentials(db, user, payload, lock=False)
    except GoogleHealthCredentialError as exc:
        raise _credential_error(exc) from exc


@router.delete("/credentials", response_model=GoogleHealthStatus)
def google_health_credentials_delete(
    user: User = Depends(require_csrf_exclusive),
    db: Session = Depends(get_db),
) -> GoogleHealthStatus:
    if not settings.google_health_enabled:
        raise HTTPException(status_code=404, detail="Google Health ist nicht verfügbar.")
    return delete_google_health_credentials(db, user, lock=False)


@router.delete("/connection", response_model=GoogleHealthStatus)
def google_health_connection_delete(
    user: User = Depends(require_csrf_exclusive),
    db: Session = Depends(get_db),
) -> GoogleHealthStatus:
    if not settings.google_health_enabled:
        raise HTTPException(status_code=404, detail="Google Health ist nicht verfügbar.")
    return disconnect_google_health(db, user, lock=False)


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
    except HTTPException as exc:
        if _wants_spa_redirect(request) and exc.status_code == 401:
            return _login_spa_redirect()
        raise
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
