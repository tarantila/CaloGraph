from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.dependencies import current_user, require_csrf
from app.database import get_db
from app.models import User
from app.problem_types import ProblemHTTPException
from app.schemas_source_priority import NutritionPriorityState, NutritionPriorityUpdateRequest
from app.source_priority.public import (
    NutritionPriorityUpdateConflict,
    get_nutrition_priority_state,
    update_nutrition_priority,
)

router = APIRouter(prefix="/source-priority", tags=["Source Priority"])

_CONFLICT_PROBLEMS: dict[str, tuple[int, str]] = {
    "stale_policy": (409, "urn:calograph:problem:source-priority-stale-policy"),
    "provider_set_changed": (409, "urn:calograph:problem:source-priority-provider-set-changed"),
    "invalid_source_order": (422, "urn:calograph:problem:source-priority-invalid-source-order"),
    "advanced_configuration": (409, "urn:calograph:problem:source-priority-advanced-configuration"),
}


@router.get("/nutrition", response_model=NutritionPriorityState)
def nutrition_priority(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> NutritionPriorityState:
    return get_nutrition_priority_state(db, user.id)


@router.put("/nutrition", response_model=NutritionPriorityState)
def update_nutrition_priority_route(
    payload: NutritionPriorityUpdateRequest,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> NutritionPriorityState:
    try:
        state, _changed = update_nutrition_priority(db, user.id, payload)
    except NutritionPriorityUpdateConflict as exc:
        status_code, problem_type = _CONFLICT_PROBLEMS[exc.code]
        raise ProblemHTTPException(
            status_code=status_code,
            detail=exc.code,
            problem_type=problem_type,
        ) from exc
    return state
