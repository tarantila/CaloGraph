"""Account-local Nutrition Expert data-category settings."""

from typing import Annotated

from fastapi import APIRouter, Depends, Security
from sqlalchemy.orm import Session

from app.ai.connections import ConnectionProblem
from app.api.ai_connections import _problem
from app.api.ai_connections import router as connections_router
from app.auth.dependencies import current_user, require_csrf_exclusive
from app.auth.openapi import session_scheme
from app.database import get_db
from app.expert.preferences import ExpertPreferencesService
from app.expert.preferences_contracts import ExpertPreferencesPut, ExpertPreferencesView
from app.models import User

router = APIRouter(
    prefix="/settings/expert", tags=["Nutrition Expert"], dependencies=[Security(session_scheme)],
    responses=connections_router.responses,
)


@router.get("", response_model=ExpertPreferencesView, operation_id="expert_get_preferences")
def get_preferences(
    owner: Annotated[User, Depends(current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ExpertPreferencesView:
    try:
        return ExpertPreferencesService(db, owner=owner).get()
    except ConnectionProblem as exc:
        _problem(exc)


@router.put(
    "", response_model=ExpertPreferencesView, operation_id="expert_put_preferences",
    description=(
        "Replace the authenticated account's Nutrition Expert data categories. "
        "Saving settings reads no nutrition or health data and makes no provider request. "
        "Requires X-CSRF-Token."
    ),
)
def put_preferences(
    payload: ExpertPreferencesPut,
    owner: Annotated[User, Depends(require_csrf_exclusive)],
    db: Annotated[Session, Depends(get_db)],
) -> ExpertPreferencesView:
    try:
        return ExpertPreferencesService(db, owner=owner).put(payload, _operation_locked=True)
    except ConnectionProblem as exc:
        _problem(exc)
