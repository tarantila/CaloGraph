"""REST adapter for user-scoped provider configuration; never probes providers."""

from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, Security
from sqlalchemy.orm import Session

from app.ai.connections import ConnectionProblem, ProviderConnectionService
from app.ai.connections_contracts import (
    ProviderConnectionListQuery,
    ProviderConnectionPage,
    ProviderConnectionPut,
    ProviderConnectionView,
)
from app.auth.dependencies import current_user, require_csrf_exclusive
from app.auth.openapi import session_scheme
from app.database import get_db
from app.models import User
from app.problem_types import ProblemHTTPException
from app.schemas import ProblemDetail

router = APIRouter(
    prefix="/settings/ai/connections", tags=["AI providers"], dependencies=[Security(session_scheme)],
    responses={
        status: {
            "description": description,
            "content": {"application/problem+json": {"schema": ProblemDetail.model_json_schema()}},
        }
        for status, description in (
            (401, "Missing or inactive account session."),
            (403, "Invalid CSRF token or request origin for a mutation."),
            (404, "Connection not found in the authenticated account."),
            (409, "Lifecycle conflict, immutable provider type, or account connection limit."),
            (422, "Invalid configuration; secrets and invalid values are not echoed."),
            (503, "Credential encryption is unavailable."),
        )
    },
)


def _problem(exc: ConnectionProblem) -> NoReturn:
    raise ProblemHTTPException(exc.status, exc.detail, f"urn:calograph:problem:{exc.code}") from exc


@router.get("", response_model=ProviderConnectionPage, operation_id="ai_list_connections")
def list_connections(
    query: Annotated[ProviderConnectionListQuery, Query()],
    owner: Annotated[User, Depends(current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ProviderConnectionPage:
    return ProviderConnectionService(db, owner=owner).list(query)


@router.get("/{connection_id}", response_model=ProviderConnectionView, operation_id="ai_get_connection")
def get_connection(
    connection_id: UUID,
    owner: Annotated[User, Depends(current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ProviderConnectionView:
    try:
        return ProviderConnectionService(db, owner=owner).get(connection_id)
    except ConnectionProblem as exc:
        _problem(exc)


@router.put(
    "/{connection_id}", response_model=ProviderConnectionView, operation_id="ai_put_connection",
    description=(
        "Idempotently create/replace configuration at a client-generated UUID scoped to this account. "
        "Requires X-CSRF-Token. Omit api_key to retain it, null removes it; saved secrets are never "
        "returned. Provider kind is immutable. Changing a custom destination requires explicit key "
        "replacement/removal. No network request or consent to transmit health data is made. "
        "ChatGPT-plan credentials must use a future supported OAuth flow, not this API-key resource."
    ),
    responses={201: {"model": ProviderConnectionView, "description": "Configuration created."}},
)
def put_connection(
    connection_id: UUID,
    payload: ProviderConnectionPut,
    response: Response,
    owner: Annotated[User, Depends(require_csrf_exclusive)],
    db: Annotated[Session, Depends(get_db)],
) -> ProviderConnectionView:
    try:
        result, created = ProviderConnectionService(db, owner=owner).put(connection_id, payload, _operation_locked=True)
        response.status_code = 201 if created else 200
        return result
    except ConnectionProblem as exc:
        _problem(exc)


@router.delete(
    "/{connection_id}", status_code=204, response_class=Response,
    operation_id="ai_delete_connection",
    description="Idempotently remove account-local configuration and credentials; requires X-CSRF-Token. Does not revoke a key at its provider.",
)
def delete_connection(
    connection_id: UUID,
    owner: Annotated[User, Depends(require_csrf_exclusive)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    ProviderConnectionService(db, owner=owner).delete(connection_id, _operation_locked=True)
    return Response(status_code=204)
