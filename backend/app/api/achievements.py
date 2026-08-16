from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.auth.dependencies import current_user, require_csrf_exclusive
from app.config import settings
from app.database import get_db
from app.models import User
from app.schemas import AchievementListResponse, AchievementReconcileResponse, AchievementResponse
from app.services.achievements import list_achievements, reconcile_achievements
from app.services.rate_limit import check_rate_limit, normalize_client_ip

router = APIRouter(prefix="/achievements", tags=["Achievements"])


def _rate_limit_achievements(db: Session, request: Request, user: User) -> None:
    client_ip = normalize_client_ip(request.client.host if request.client else None)
    check_rate_limit(
        db,
        "achievements-ip",
        f"ip:{client_ip}",
        settings.reconcile_ip_rate_limit,
        settings.reconcile_rate_limit_window_seconds,
    )
    check_rate_limit(
        db,
        "achievements-user",
        f"user:{user.id}",
        settings.reconcile_rate_limit,
        settings.reconcile_rate_limit_window_seconds,
    )


def _response(status: object) -> AchievementResponse:
    return AchievementResponse.model_validate(status, from_attributes=True)


@router.get("", response_model=AchievementListResponse, response_model_exclude_none=True)
def achievements(
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> AchievementListResponse:
    _rate_limit_achievements(db, request, user)
    return AchievementListResponse(
        achievements=[_response(item) for item in list_achievements(db, user)]
    )


@router.post(
    "/reconcile",
    response_model=AchievementReconcileResponse,
    response_model_exclude_none=True,
)
def reconcile(
    request: Request,
    user: User = Depends(require_csrf_exclusive),
    db: Session = Depends(get_db),
) -> AchievementReconcileResponse:
    _rate_limit_achievements(db, request, user)
    statuses, newly_unlocked = reconcile_achievements(db, user)
    return AchievementReconcileResponse(
        achievements=[_response(item) for item in statuses],
        newly_unlocked=[_response(item) for item in newly_unlocked],
    )
