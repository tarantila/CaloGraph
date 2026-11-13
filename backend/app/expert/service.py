"""Bounded application reads shared by REST and future curated tool adapters.

No HTTP, model SDK, SQL supplied by callers, or alternate provider resolution.
This capability deliberately excludes meal names, profile data and AI egress.
"""

from datetime import timedelta

from sqlalchemy.orm import Session

from app.analytics.nutrition_analysis import read_analysis_nutrition
from app.analytics.provider_selection import (
    NutritionProviderUnavailable,
    resolve_nutrition_provider,
)
from app.expert.contracts import (
    NUTRITION_METRICS,
    NutritionDay,
    NutritionDaysPage,
    NutritionDaysQuery,
    NutritionEvidence,
    NutritionValues,
)
from app.models import User


class ExpertAccessDenied(PermissionError):
    """The server-bound owner is no longer active."""


class NutritionExpertReader:
    """Bind the authenticated owner in the adapter, never in model arguments.

    Each call reads one live page. It is not an all-history snapshot, a
    check-in, a target evaluation, or a promise of cross-page consistency.
    """

    def __init__(self, db: Session, *, owner: User) -> None:
        self._db = db
        self._owner = owner

    def get_nutrition_days(self, query: NutritionDaysQuery) -> NutritionDaysPage:
        if not self._owner.is_active:
            raise ExpertAccessDenied("account is inactive")
        selection = resolve_nutrition_provider(
            self._db, user_id=self._owner.id, include_analysis_demo=True,
        )
        if selection is None:
            raise NutritionProviderUnavailable("no readable nutrition provider")
        start = query.start if query.after is None else query.after + timedelta(days=1)
        # Subtract before adding so date.max with a short final page is safe.
        end = start + timedelta(days=min(query.page_size - 1, (query.end - start).days))
        canonical = read_analysis_nutrition(
            self._db, user_id=self._owner.id, selection=selection,
            start=start, end=end, include_meals=False,
        )
        days: list[NutritionDay] = []
        evidence: list[NutritionEvidence] = []
        for day in canonical.days:
            values = NutritionValues.model_validate({
                metric: getattr(day, metric) for metric in NUTRITION_METRICS
            })
            days.append(NutritionDay(date=day.date, provider_key=day.provider_key, values=values))
            if day.provider_key is None:
                continue
            for metric in NUTRITION_METRICS:
                value = getattr(values, metric)
                if value is not None:
                    evidence.append(NutritionEvidence(
                        id=f"nutrition:{day.date.isoformat()}:{metric}",
                        date=day.date, provider_key=day.provider_key, metric=metric,
                        value=value, unit="kcal" if metric == "dietary_energy_kcal" else "g",
                    ))
        return NutritionDaysPage(
            start=query.start, end=query.end, timezone=self._owner.timezone,
            days=tuple(days), evidence=tuple(evidence),
            next_after=end if end < query.end else None,
        )
