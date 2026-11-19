"""Authenticated selection of an enabled account-local provider connection."""

from typing import Annotated

from fastapi import APIRouter, Depends, Security
from sqlalchemy.orm import Session

from app.ai.connections import ConnectionProblem
from app.ai.preferences import AISettingsPut, AISettingsService, AISettingsView
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
