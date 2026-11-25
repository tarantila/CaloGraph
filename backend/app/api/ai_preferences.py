"""Authenticated selection of an enabled account-local provider connection."""

from typing import Annotated

from fastapi import APIRouter, Depends, Security
from sqlalchemy.orm import Session

from app.ai.connections import ConnectionProblem
from app.ai.preferences import AISettingsPut, AISettingsService, AISettingsView
from app.ai.routing import AIRoutingService
from app.ai.routing_contracts import AIRoutingPut, AIRoutingView
from app.api.ai_connections import _problem
from app.api.ai_connections import router as connections_router
from app.auth.dependencies import current_user, require_csrf_exclusive
from app.auth.openapi import session_scheme
from app.database import get_db
from app.models import User

router = APIRouter(
    prefix="/settings/ai", tags=["AI providers"], dependencies=[Security(session_scheme)],
    responses=connections_router.responses,
)


@router.get("/preferences", response_model=AISettingsView, operation_id="ai_get_settings")
def get_ai_settings(owner: Annotated[User, Depends(current_user)], db: Annotated[Session, Depends(get_db)]) -> AISettingsView:
    return AISettingsService(db, owner=owner).get()


@router.put(
    "/preferences", response_model=AISettingsView, operation_id="ai_put_settings",
    description="Idempotently select an enabled account-local connection at its current configuration version, or clear both fields with null. Requires CSRF. No provider request or permission to transmit health data is implied.",
)
def put_ai_settings(
    payload: AISettingsPut, owner: Annotated[User, Depends(require_csrf_exclusive)], db: Annotated[Session, Depends(get_db)],
) -> AISettingsView:
    try:
        return AISettingsService(db, owner=owner).put(payload, _operation_locked=True)
    except ConnectionProblem as exc:
        _problem(exc)


@router.get("/routing", response_model=AIRoutingView, operation_id="ai_get_routing")
def get_ai_routing(owner: Annotated[User, Depends(current_user)], db: Annotated[Session, Depends(get_db)]) -> AIRoutingView:
    return AIRoutingService(db, owner=owner).get()


@router.put(
    "/routing", response_model=AIRoutingView, operation_id="ai_put_routing",
    description="Replace the complete account-local provider order and activation set at an expected routing version. Requires CSRF. Routing settings do not send health data or grant consent.",
)
def put_ai_routing(
    payload: AIRoutingPut, owner: Annotated[User, Depends(require_csrf_exclusive)], db: Annotated[Session, Depends(get_db)],
) -> AIRoutingView:
    try:
        return AIRoutingService(db, owner=owner).put(payload, _operation_locked=True)
    except ConnectionProblem as exc:
        _problem(exc)
