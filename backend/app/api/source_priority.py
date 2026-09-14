from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.dependencies import current_user, require_csrf
from app.database import SessionLocal, get_db
from app.models import User
from app.nutrition.projection.refresh import (
    NutritionProjectionRefreshError,
    refresh_stale_nutrition_projections,
)
from app.problem_types import ProblemHTTPException
from app.schemas_source_priority import (
    NutritionPriorityRefreshRequest,
    NutritionPriorityRefreshResponse,
    NutritionPriorityState,
    NutritionPriorityUpdateRequest,
)
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

_REFRESH_CONFLICT_TYPES: dict[str, str] = {
    "stale_policy": "urn:calograph:problem:source-priority-stale-policy",
    "selection_required": "urn:calograph:problem:source-priority-selection-required",
    "configuration_required": "urn:calograph:problem:source-priority-configuration-required",
    "no_providers": "urn:calograph:problem:source-priority-no-providers",
    "no_policy": "urn:calograph:problem:source-priority-no-policy",
}
_REFRESH_FAILURE_TYPE = "urn:calograph:problem:source-priority-projection-refresh-failed"
_REFRESH_CONFLICT_CODES = frozenset(_REFRESH_CONFLICT_TYPES)


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


def _refresh_error_code(exc: NutritionProjectionRefreshError) -> str | None:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code in _REFRESH_CONFLICT_CODES:
        return code
    message = str(exc).lower()
    for candidate in _REFRESH_CONFLICT_CODES:
        if candidate in message:
            return candidate
    if "expected policy version" in message or "current version" in message:
        return "stale_policy"
    if "usable effective" in message or "configuration" in message:
        return "configuration_required"
    if "no provider" in message:
        return "no_providers"
    if "no policy" in message or "effective nutrition policy disappeared" in message:
        return "no_policy"
    return None

def _raise_refresh_conflict(code: str) -> None:
    raise ProblemHTTPException(
        status_code=409,
        detail=code,
        problem_type=_REFRESH_CONFLICT_TYPES[code],
    )


@router.post(
    "/nutrition/refresh",
    response_model=NutritionPriorityRefreshResponse,
)
def refresh_nutrition_priority(
    payload: NutritionPriorityRefreshRequest,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> NutritionPriorityRefreshResponse:
    try:
        state = get_nutrition_priority_state(db, user.id)
        if state.status in {"selection_required", "configuration_required", "no_providers"}:
            _raise_refresh_conflict(state.status)

        result = refresh_stale_nutrition_projections(
            session_factory=SessionLocal,
            user_id=user.id,
            expected_version=payload.expected_version,
        )
    except ProblemHTTPException:
        raise
    except NutritionProjectionRefreshError as exc:
        code = _refresh_error_code(exc)
        if code is not None:
            _raise_refresh_conflict(code)
        raise ProblemHTTPException(
            status_code=500,
            detail="projection_refresh_failed",
            problem_type=_REFRESH_FAILURE_TYPE,
        ) from exc
    except Exception as exc:
        raise ProblemHTTPException(
            status_code=500,
            detail="projection_refresh_failed",
            problem_type=_REFRESH_FAILURE_TYPE,
        ) from exc

    return NutritionPriorityRefreshResponse(
        policy_version=result.policy_version,
        processed_count=result.processed_count,
        created_count=result.created_count,
        unchanged_count=result.unchanged_count,
        has_more=result.has_more,
        projection_refresh_required=result.projection_refresh_required,
    )
