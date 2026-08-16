from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.dependencies import current_user, require_csrf_exclusive
from app.database import get_db
from app.models import User
from app.schemas import AchievementListResponse, AchievementReconcileResponse, AchievementResponse
from app.services.achievements import list_achievements, reconcile_achievements

router = APIRouter(prefix="/achievements", tags=["Achievements"])


def _response(status: object) -> AchievementResponse:
    return AchievementResponse.model_validate(status, from_attributes=True)


@router.get("", response_model=AchievementListResponse, response_model_exclude_none=True)
def achievements(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> AchievementListResponse:
    return AchievementListResponse(
        achievements=[_response(item) for item in list_achievements(db, user)]
    )


@router.post(
    "/reconcile",
    response_model=AchievementReconcileResponse,
    response_model_exclude_none=True,
)
def reconcile(
    user: User = Depends(require_csrf_exclusive),
    db: Session = Depends(get_db),
) -> AchievementReconcileResponse:
    statuses, newly_unlocked = reconcile_achievements(db, user)
    return AchievementReconcileResponse(
        achievements=[_response(item) for item in statuses],
        newly_unlocked=[_response(item) for item in newly_unlocked],
    )
