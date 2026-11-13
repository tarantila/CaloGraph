"""Authenticated metadata checks. The request never contains provider credentials."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Security
from sqlalchemy.orm import Session

from app.ai.connections import ConnectionProblem
from app.ai.probes import ProviderCheckService, provider_catalog
from app.ai.probes_contracts import ConnectionCheckPut, ConnectionCheckView, ProviderCatalog
from app.api.ai_connections import _problem
from app.auth.dependencies import current_user, require_csrf_exclusive
from app.auth.openapi import session_scheme
from app.database import get_db
from app.models import User
from app.schemas import ProblemDetail

router = APIRouter(
    prefix="/settings/ai", tags=["AI providers"], dependencies=[Security(session_scheme)],
    responses={code: {"description": description, "content": {
        "application/problem+json": {"schema": ProblemDetail.model_json_schema()},
    }} for code, description in (
        (401, "Authenticated session required."), (403, "CSRF/origin validation failed."),
        (404, "Connection not found in this account."), (409, "Configuration, lifecycle or replay conflict."),
        (422, "Invalid bounded input or privacy acknowledgement version."), (429, "Connection check rate limit."),
    )},
)


@router.get("/providers", response_model=ProviderCatalog, operation_id="ai_get_provider_catalog")
def get_provider_catalog(_owner: Annotated[User, Depends(current_user)]) -> ProviderCatalog:
    return provider_catalog()


@router.get("/connections/{connection_id}/checks/latest", response_model=ConnectionCheckView | None, operation_id="ai_get_latest_connection_check")
def latest_connection_check(
    connection_id: UUID, owner: Annotated[User, Depends(current_user)], db: Annotated[Session, Depends(get_db)],
) -> ConnectionCheckView | None:
    try:
        return ProviderCheckService(db, owner=owner).latest(connection_id)
    except ConnectionProblem as exc:
        _problem(exc)


@router.put(
    "/connections/{connection_id}/checks/{check_id}", response_model=ConnectionCheckView,
    operation_id="ai_put_connection_check", description=(
        "Explicit metadata-only connection check. Requires CSRF and current privacy-version acknowledgement. "
        "Sends stored credentials to the approved provider destination to read model metadata; never sends health data or a prompt. "
        "Client UUID is account-scoped. Repeating the same UUID and payload replays the recorded result for at least 24 hours; "
        "reusing it for a different connection/configuration conflicts. Check claim is durable before network I/O. "
        "An interrupted claim is never retried automatically. New checks require a new UUID. "
        "At most 10 checks/minute and 100 retained checks/account; results expire after 24h on new checks. "
        "Model discovery does not establish generation, streaming, tool or structured-output capabilities."
    ),
)
def put_connection_check(
    connection_id: UUID, check_id: UUID, payload: ConnectionCheckPut,
    owner: Annotated[User, Depends(require_csrf_exclusive)], db: Annotated[Session, Depends(get_db)],
) -> ConnectionCheckView:
    try:
        return ProviderCheckService(db, owner=owner).put(connection_id, check_id, payload, _operation_locked=True)
    except ConnectionProblem as exc:
        _problem(exc)
