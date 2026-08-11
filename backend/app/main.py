import logging
import re
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text

from app.api.router import api_router
from app.auth.password_policy import validate_password_blocklist
from app.config import settings
from app.database import engine
from app.security_events import (
    log_security_event,
    security_reference,
    security_request_context,
)
from app.services.rate_limit import RateLimitExceeded, normalize_client_ip
from app.services.user_operation_lock import InactiveUserOperation, UserOperationBusy

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("calograph")
REQUEST_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")

settings.validate_runtime_security()
validate_password_blocklist()

app = FastAPI(
    title="CaloGraph API",
    version="0.3.0",
    description=(
        "Lokale Analyse- und Import-API. CaloGraph greift nicht serverseitig auf Apple Health "
        "oder iCloud zu."
    ),
    docs_url=None,
    openapi_url=None,
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_host_list)
app.include_router(api_router)


@app.get("/api/docs", response_class=HTMLResponse, include_in_schema=False)
def api_docs() -> str:
    if not settings.enable_api_docs:
        raise HTTPException(status_code=404, detail="Nicht gefunden")
    return """<!doctype html><html lang="de"><meta charset="utf-8">
    <title>CaloGraph API</title><body><h1>CaloGraph API</h1>
    <p>Das maschinenlesbare OpenAPI-Schema ist unter
    <a href="/api/openapi.json">/api/openapi.json</a> verfügbar.</p></body></html>"""


@app.get("/api/openapi.json", include_in_schema=False)
def api_openapi() -> JSONResponse:
    if not settings.enable_api_docs:
        raise HTTPException(status_code=404, detail="Nicht gefunden")
    return JSONResponse(app.openapi())


@app.middleware("http")
async def security_and_request_id(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    incoming_request_id = request.headers.get("x-request-id", "")
    request_id = (
        incoming_request_id
        if REQUEST_ID_PATTERN.fullmatch(incoming_request_id)
        else uuid.uuid4().hex
    )
    request.state.request_id = request_id
    client = normalize_client_ip(request.client.host if request.client else None)
    with security_request_context(request_id, security_reference("client", client)):
        try:
            response = await call_next(request)
        except Exception:
            log_security_event("request.failed", reason="unhandled_exception")
            raise
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    if settings.enable_hsts:
        hsts = "max-age=31536000"
        if settings.hsts_include_subdomains:
            hsts += "; includeSubDomains"
        response.headers["Strict-Transport-Security"] = hsts
    logger.info(
        "request method=%s path=%s status=%s request_id=%s",
        request.method,
        request.url.path,
        response.status_code,
        request_id,
    )
    return response


def _log_import_rejection(request: Request, status_code: int) -> None:
    if (
        request.url.path.startswith("/api/v1/import/")
        and status_code in {400, 409, 413, 415, 422}
    ):
        log_security_event(
            "import.rejected",
            reason=f"http_{status_code}",
            details={"status_code": status_code},
        )


@app.exception_handler(InactiveUserOperation)
async def inactive_user_operation(
    request: Request,
    _exc: InactiveUserOperation,
) -> JSONResponse:
    _log_import_rejection(request, 409)
    return JSONResponse(
        status_code=409,
        content={
            "type": "about:blank",
            "title": "Anfrage fehlgeschlagen",
            "status": 409,
            "detail": "Das Konto ist nicht aktiv.",
            "request_id": getattr(request.state, "request_id", None),
        },
    )


@app.exception_handler(UserOperationBusy)
async def busy_user_operation(
    request: Request,
    _exc: UserOperationBusy,
) -> JSONResponse:
    _log_import_rejection(request, 409)
    return JSONResponse(
        status_code=409,
        content={
            "type": "about:blank",
            "title": "Anfrage fehlgeschlagen",
            "status": 409,
            "detail": "Für dieses Konto läuft gerade eine administrative Operation.",
            "request_id": getattr(request.state, "request_id", None),
        },
    )


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc, RateLimitExceeded):
        log_security_event(
            "security.rate_limit.triggered",
            target_ref=exc.key_ref,
            details={"action": exc.action, "retry_after": exc.retry_after},
        )
    else:
        _log_import_rejection(request, exc.status_code)
    return JSONResponse(
        status_code=exc.status_code,
        headers=exc.headers,
        content={
            "type": "about:blank",
            "title": "Anfrage fehlgeschlagen",
            "status": exc.status_code,
            "detail": str(exc.detail),
            "request_id": getattr(request.state, "request_id", None),
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    del exc
    return JSONResponse(
        status_code=422,
        content={
            "type": "about:blank",
            "title": "Validierung fehlgeschlagen",
            "status": 422,
            "detail": "Eingabedaten sind ungültig.",
            "request_id": getattr(request.state, "request_id", None),
        },
    )


@app.get("/health/live", include_in_schema=False)
def health_live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", include_in_schema=False)
def health_ready() -> dict[str, str]:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return {"status": "ready"}
