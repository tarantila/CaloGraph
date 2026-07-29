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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("calograph")
REQUEST_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")

settings.validate_runtime_security()
validate_password_blocklist()

app = FastAPI(
    title="CaloGraph API",
    version="0.1.2",
    description=(
        "Lokale Analyse- und Import-API. CaloGraph greift nicht serverseitig auf Apple Health "
        "oder iCloud zu."
    ),
    docs_url=None,
    openapi_url="/api/openapi.json",
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_host_list)
app.include_router(api_router)


@app.get("/api/docs", response_class=HTMLResponse, include_in_schema=False)
def api_docs() -> str:
    return """<!doctype html><html lang="de"><meta charset="utf-8">
    <title>CaloGraph API</title><body><h1>CaloGraph API</h1>
    <p>Das maschinenlesbare OpenAPI-Schema ist unter
    <a href="/api/openapi.json">/api/openapi.json</a> verfügbar.</p></body></html>"""


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
    response = await call_next(request)
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
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    logger.info(
        "request method=%s path=%s status=%s request_id=%s",
        request.method,
        request.url.path,
        response.status_code,
        request_id,
    )
    return response


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
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
