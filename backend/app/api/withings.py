from __future__ import annotations

from datetime import datetime, timedelta
from typing import NoReturn
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import current_user, require_csrf_exclusive
from app.config import settings
from app.database import get_db
from app.models import User, WithingsConnection
from app.schemas_withings import (
    WithingsConnectionTestResponse,
    WithingsCredentialsInput,
    WithingsOAuthStartResponse,
    WithingsStatus,
)
from app.schemas_withings_sync import WithingsSyncDomainResult, WithingsSyncResponse
from app.withings.credentials import WithingsCredentialError
from app.withings.errors import (
    WithingsDisabledError,
    WithingsNotConfiguredError,
    WithingsOAuthError,
)
from app.withings.service import (
    complete_withings_oauth,
    delete_withings_credentials,
    disconnect_withings,
    save_withings_credentials,
    start_withings_oauth,
    test_withings_connection,
    withings_status,
)
from app.withings.sync import WithingsDomainResult, WithingsSyncResult, WithingsSyncService

router = APIRouter(prefix="/withings", tags=["Withings"])


def _error(exc: WithingsOAuthError) -> HTTPException:
    details = {
        "invalid_state": "OAuth-Anfrage ist ungültig.",
        "unknown_state": "OAuth-Anfrage ist ungültig oder abgelaufen.",
        "expired_state": "OAuth-Anfrage ist ungültig oder abgelaufen.",
        "replayed_state": "OAuth-Anfrage wurde bereits verwendet.",
        "scope_missing": "Die erforderliche Withings-Berechtigung wurde nicht erteilt.",
        "reauth_required": "Withings muss erneut autorisiert werden.",
        "provider_error": "Withings ist vorübergehend nicht verfügbar.",
        "transient_error": "Withings ist vorübergehend nicht verfügbar.",
        "rate_limited": "Withings ist vorübergehend nicht verfügbar.",
        "invalid_response": "Die Withings-Antwort ist ungültig.",
        "credential_unavailable": "Withings ist derzeit nicht verfügbar.",
        "not_connected": "Withings ist nicht verbunden.",
    }
    status = 429 if exc.code == "rate_limited" else 502 if exc.code in {"provider_error", "transient_error"} else exc.status_code
    return HTTPException(status_code=status, detail=details.get(exc.code, "Withings-Anfrage fehlgeschlagen."))


def _wants_spa_redirect(request: Request) -> bool:
    qualities: dict[str, float] = {}
    for raw in request.headers.get("accept", "").split(","):
        parts = [part.strip() for part in raw.split(";")]
        media_type = parts[0].lower()
        if media_type not in {"text/html", "application/json"}:
            continue
        quality = 1.0
        for parameter in parts[1:]:
            name, separator, value = parameter.partition("=")
            if name.lower() == "q" and separator:
                try:
                    quality = max(0.0, min(1.0, float(value)))
                except ValueError:
                    quality = 0.0
        qualities[media_type] = max(qualities.get(media_type, 0.0), quality)
    return qualities.get("text/html", 0.0) > qualities.get("application/json", 0.0)


def _spa_redirect(result: str) -> RedirectResponse:
    return RedirectResponse(url=f"/konto/integrationen?withings={result}", status_code=303)


def _login_redirect() -> RedirectResponse:
    return RedirectResponse(url="/login?next=/konto/integrationen&withings=error", status_code=303)


def _raise_configuration(exc: Exception) -> NoReturn:
    if isinstance(exc, WithingsDisabledError):
        raise HTTPException(status_code=404, detail="Withings ist nicht verfügbar.") from exc
    if isinstance(exc, WithingsNotConfiguredError):
        raise HTTPException(status_code=409, detail="Withings ist nicht konfiguriert.") from exc
    raise exc


@router.get("/status", response_model=WithingsStatus)
def withings_status_route(user: User = Depends(current_user), db: Session = Depends(get_db)) -> WithingsStatus:
    return withings_status(db, user)


@router.post("/oauth/start", response_model=WithingsOAuthStartResponse)
def withings_oauth_start(
    user: User = Depends(require_csrf_exclusive), db: Session = Depends(get_db)
) -> WithingsOAuthStartResponse:
    if settings.withings_enabled:
        connection = db.scalar(
            select(WithingsConnection).where(WithingsConnection.user_id == user.id)
        )
        if connection is None or not connection.client_id or not connection.encrypted_client_secret:
            raise HTTPException(status_code=409, detail="Withings ist nicht konfiguriert.")
    try:
        return WithingsOAuthStartResponse(
            authorization_url=start_withings_oauth(db, user, lock=False)
        )
    except (WithingsDisabledError, WithingsNotConfiguredError) as exc:
        _raise_configuration(exc)
    except WithingsOAuthError as exc:
        raise _error(exc) from exc


def _credential_error(exc: WithingsCredentialError) -> HTTPException:
    details = {
        "credential_required": "Withings Client-ID und Client-Secret sind erforderlich.",
        "credential_too_long": "Withings-Zugangsdaten sind zu lang.",
        "credential_invalid": "Withings-Zugangsdaten sind ungültig.",
        "credential_unavailable": "Withings-Zugangsdaten sind derzeit nicht verfügbar.",
    }
    status = 503 if exc.code == "credential_unavailable" else 422
    return HTTPException(
        status_code=status,
        detail=details.get(exc.code, "Withings-Zugangsdaten sind ungültig."),
    )


@router.put("/credentials", response_model=WithingsStatus)
def withings_credentials_put(
    payload: WithingsCredentialsInput,
    user: User = Depends(require_csrf_exclusive),
    db: Session = Depends(get_db),
) -> WithingsStatus:
    if not settings.withings_enabled:
        raise HTTPException(status_code=404, detail="Withings ist nicht verfügbar.")
    try:
        return save_withings_credentials(db, user, payload, lock=False)
    except WithingsCredentialError as exc:
        raise _credential_error(exc) from exc


@router.delete("/credentials", response_model=WithingsStatus)
def withings_credentials_delete(
    user: User = Depends(require_csrf_exclusive),
    db: Session = Depends(get_db),
) -> WithingsStatus:
    if not settings.withings_enabled:
        raise HTTPException(status_code=404, detail="Withings ist nicht verfügbar.")
    return delete_withings_credentials(db, user, lock=False)


@router.get("/oauth/callback", response_model=None)
def withings_oauth_callback(
    request: Request,
    state: str | None = Query(default=None, max_length=512),
    code: str | None = Query(default=None, max_length=4096),
    error: str | None = Query(default=None, max_length=128),
    db: Session = Depends(get_db),
) -> WithingsStatus | RedirectResponse:
    try:
        user = current_user(request, db)
    except HTTPException as exc:
        if _wants_spa_redirect(request) and exc.status_code == 401:
            return _login_redirect()
        raise
    try:
        status = complete_withings_oauth(db, user, state=state, code=code, error=error, request=request)
    except (WithingsDisabledError, WithingsNotConfiguredError) as exc:
        if _wants_spa_redirect(request):
            return _spa_redirect("error")
        _raise_configuration(exc)
    except WithingsOAuthError as exc:
        if _wants_spa_redirect(request):
            return _spa_redirect("error")
        raise _error(exc) from exc
    if _wants_spa_redirect(request):
        return _spa_redirect("connected" if status.state == "active" else "error")
    return status


@router.post("/connection/test", response_model=WithingsConnectionTestResponse)
def withings_connection_test(
    user: User = Depends(require_csrf_exclusive), db: Session = Depends(get_db)
) -> WithingsConnectionTestResponse:
    try:
        return test_withings_connection(db, user)
    except (WithingsDisabledError, WithingsNotConfiguredError) as exc:
        _raise_configuration(exc)
    except WithingsOAuthError as exc:
        raise _error(exc) from exc


def _sync_domain_response(result: WithingsDomainResult) -> WithingsSyncDomainResult:
    return WithingsSyncDomainResult(
        status=result.status,
        fetched_count=result.fetched_count,
        persisted_count=result.persisted_count,
        requested_start=result.requested_start,
        requested_end=result.requested_end,
        covered_start=result.covered_start,
        covered_end=result.covered_end,
        error_code=result.error_code,
    )


@router.post("/sync", response_model=WithingsSyncResponse)
def withings_sync_route(
    days: int = Query(default=30, ge=1, le=366),
    user: User = Depends(require_csrf_exclusive),
    db: Session = Depends(get_db),
) -> WithingsSyncResponse:
    if not settings.withings_enabled:
        raise HTTPException(status_code=404, detail="Withings ist nicht verfügbar.")
    connection = db.scalar(
        select(WithingsConnection).where(WithingsConnection.user_id == user.id)
    )
    if connection is None or not connection.client_id or not connection.encrypted_client_secret:
        raise HTTPException(status_code=409, detail="Withings ist nicht konfiguriert.")
    end = datetime.now(ZoneInfo(user.timezone)).date()
    start = end - timedelta(days=days - 1)
    result: WithingsSyncResult = WithingsSyncService().sync(
        user_id=user.id,
        requested_start=start,
        requested_end=end,
        db=db,
        _operation_locked=True,
    )
    return WithingsSyncResponse(
        status=result.status,
        weight=_sync_domain_response(result.weight),
        activity_energy=_sync_domain_response(result.activity_energy),
    )

@router.delete("/connection", response_model=WithingsStatus)
def withings_connection_delete(
    user: User = Depends(require_csrf_exclusive), db: Session = Depends(get_db)
) -> WithingsStatus:
    if not settings.withings_enabled:
        raise HTTPException(status_code=404, detail="Withings ist nicht verfügbar.")
    return disconnect_withings(db, user, lock=False)
