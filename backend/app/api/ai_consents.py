"""Authenticated, owner-bound AI data-consent resources."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, Security
from sqlalchemy.orm import Session

from app.ai.connections import ConnectionProblem
from app.ai.consents import AIConsentService
from app.ai.consents_contracts import AIConsentListQuery, AIConsentPage, AIConsentPut, AIConsentView
from app.api.ai_connections import _problem
from app.api.ai_connections import router as connections_router
from app.auth.dependencies import current_user, require_csrf_exclusive
from app.auth.openapi import session_scheme
from app.database import get_db
from app.models import User

router = APIRouter(
    prefix="/settings/ai/consents", tags=["AI providers"],
    dependencies=[Security(session_scheme)], responses=connections_router.responses,
)


@router.get("", response_model=AIConsentPage, operation_id="ai_list_consents")
def list_consents(
    query: Annotated[AIConsentListQuery, Query()],
    owner: Annotated[User, Depends(current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> AIConsentPage:
    try:
        return AIConsentService(db, owner=owner).list(query)
    except ConnectionProblem as exc:
        _problem(exc)


@router.get("/{consent_id}", response_model=AIConsentView, operation_id="ai_get_consent")
def get_consent(
    consent_id: UUID,
    owner: Annotated[User, Depends(current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> AIConsentView:
    try:
        return AIConsentService(db, owner=owner).get(consent_id)
    except ConnectionProblem as exc:
        _problem(exc)


@router.put(
    "/{consent_id}", response_model=AIConsentView,
    operation_id="ai_put_consent",
    description=(
        "Idempotently create immutable, account-local permission for a specific provider connection, "
        "purpose, data scope, history scope, connection configuration version, and policy version. "
        "The same client-generated ID and payload replays the resource; changing its payload is a "
        "409 conflict. Retired IDs whose resources were removed also return 409. Revocation is terminal. "
        "Requires X-CSRF-Token. This operation reads no "
        "nutrition data and makes no provider request."
    ),
    responses={201: {"model": AIConsentView, "description": "Consent created."}},
)
def put_consent(
    consent_id: UUID,
    payload: AIConsentPut,
    response: Response,
    owner: Annotated[User, Depends(require_csrf_exclusive)],
    db: Annotated[Session, Depends(get_db)],
) -> AIConsentView:
    try:
        result, created = AIConsentService(db, owner=owner).put(
            consent_id, payload, _operation_locked=True,
        )
        response.status_code = 201 if created else 200
        return result
    except ConnectionProblem as exc:
        _problem(exc)


@router.delete(
    "/{consent_id}", status_code=204, response_class=Response,
    operation_id="ai_revoke_consent",
    description=(
        "Idempotently revoke account-local data permission. The immutable resource remains available "
        "for audit and retries, but future authorization checks reject it. Unknown IDs are retained "
        "as invalidated so a delayed creation cannot activate them. Requires X-CSRF-Token."
    ),
)
def revoke_consent(
    consent_id: UUID,
    owner: Annotated[User, Depends(require_csrf_exclusive)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    try:
        AIConsentService(db, owner=owner).revoke(consent_id, _operation_locked=True)
        return Response(status_code=204)
    except ConnectionProblem as exc:
        _problem(exc)
