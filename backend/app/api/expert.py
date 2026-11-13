"""Thin REST adapter for bounded Expert application capabilities."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Security
from fastapi.security import APIKeyCookie
from sqlalchemy.orm import Session

from app.analytics.nutrition_analysis import AnalysisNutritionReadError
from app.analytics.provider_selection import NutritionProviderNotReady, NutritionProviderUnavailable
from app.auth.dependencies import current_user
from app.auth.security import session_cookie_name
from app.database import get_db
from app.expert.contracts import NutritionDaysPage, NutritionDaysQuery
from app.expert.service import ExpertAccessDenied, NutritionExpertReader
from app.models import User
from app.problem_types import (
    PROVIDER_SELECTION_NOT_READY,
    PROVIDER_SELECTION_UNAVAILABLE,
    ProblemHTTPException,
)
from app.schemas import ProblemDetail

session_scheme = APIKeyCookie(
    name=session_cookie_name(),
    scheme_name="CaloGraphSession",
    description="Authenticated CaloGraph browser session; not a delegated external tool credential.",
    auto_error=False,  # current_user remains the sole runtime session validator.
)
router = APIRouter(
    prefix="/expert", tags=["Nutrition Expert"], dependencies=[Security(session_scheme)],
)


@router.get(
    "/nutrition-days",
    response_model=NutritionDaysPage,
    operation_id="expert_get_nutrition_days",
    summary="Read a bounded page of canonical nutrition days",
    description=(
        "Authenticated account-local dates, including missing days. One winning provider owns "
        "every value of a day; secondary omissions never fall back. Decimal values are strings; "
        "null means missing/unusable and zero is present. No meal names or profile data are "
        "returned. Pages reflect live data, not a shared snapshot. This GET performs no AI calls."
    ),
    responses={
        code: {
            "description": description,
            "content": {"application/problem+json": {"schema": ProblemDetail.model_json_schema()}},
        }
        for code, description in (
            (401, "The account is not authenticated or is inactive."),
            (409, "An account lifecycle operation temporarily prevents access."),
            (422, "Invalid range, cursor, page size, or unknown query parameter."),
            (503, "The configured canonical nutrition sources cannot be read safely."),
        )
    },
)
def nutrition_days(
    query: Annotated[NutritionDaysQuery, Query()],
    user: Annotated[User, Depends(current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> NutritionDaysPage:
    try:
        return NutritionExpertReader(db, owner=user).get_nutrition_days(query)
    except ExpertAccessDenied as exc:
        raise ProblemHTTPException(401, "Konto inaktiv", "about:blank") from exc
    except NutritionProviderUnavailable as exc:
        raise ProblemHTTPException(
            503, "Kein konfigurierter Nutrition-Provider ist verfügbar.", PROVIDER_SELECTION_UNAVAILABLE,
        ) from exc
    except (NutritionProviderNotReady, AnalysisNutritionReadError) as exc:
        raise ProblemHTTPException(
            503, "Der konfigurierte Nutrition-Provider konnte nicht sicher gelesen werden.",
            PROVIDER_SELECTION_NOT_READY,
        ) from exc
