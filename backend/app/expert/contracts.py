"""Transport-neutral contracts; calendar dates refer to the owner's local days."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_NUTRITION_RANGE_DAYS = 365
MAX_NUTRITION_PAGE_DAYS = 31
NutritionMetric = Literal[
    "dietary_energy_kcal", "protein_g", "carbohydrates_g", "fat_g",
    "fiber_g", "sugar_g", "saturated_fat_g",
]
NUTRITION_METRICS: tuple[NutritionMetric, ...] = (
    "dietary_energy_kcal", "protein_g", "carbohydrates_g", "fat_g",
    "fiber_g", "sugar_g", "saturated_fat_g",
)
Measurement = Annotated[Decimal, Field(allow_inf_nan=False)]


class ExpertContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class NutritionDaysQuery(ExpertContract):
    start: date = Field(description="Inclusive first account-local calendar date.")
    end: date = Field(description="Inclusive last date; at most 365 calendar days.")
    page_size: int = Field(default=31, ge=1, le=MAX_NUTRITION_PAGE_DAYS)
    after: date | None = Field(
        default=None,
        description="Exclusive calendar-date cursor from next_after; retain start and end.",
    )

    @model_validator(mode="after")
    def bounded_range(self) -> NutritionDaysQuery:
        if self.start > self.end or (self.end - self.start).days >= MAX_NUTRITION_RANGE_DAYS:
            raise ValueError("nutrition date range must contain 1 to 365 days")
        if self.after is not None and not self.start <= self.after < self.end:
            raise ValueError("cursor must lie inside the range and precede its end")
        return self


class NutritionValues(ExpertContract):
    """Every field is required; null is absent/unusable, never an imputed zero.

    JSON decimals are strings to retain canonical precision. Explicit zero is
    present evidence. Presence is not proof of a complete food diary.
    """

    dietary_energy_kcal: Measurement | None
    protein_g: Measurement | None
    carbohydrates_g: Measurement | None
    fat_g: Measurement | None
    fiber_g: Measurement | None
    sugar_g: Measurement | None
    saturated_fat_g: Measurement | None


class NutritionDay(ExpertContract):
    date: date
    provider_key: str | None = Field(description="Actual whole-day winner, or null.")
    values: NutritionValues = Field(repr=False)


class NutritionEvidence(ExpertContract):
    id: str = Field(max_length=100, description="Reference scoped to this read, not a durable snapshot ID.")
    date: date
    provider_key: str
    metric: NutritionMetric
    value: Measurement = Field(repr=False)
    unit: Literal["kcal", "g"]


class NutritionDaysPage(ExpertContract):
    start: date
    end: date
    timezone: str
    days: tuple[NutritionDay, ...] = Field(min_length=1, max_length=MAX_NUTRITION_PAGE_DAYS, repr=False)
    evidence: tuple[NutritionEvidence, ...] = Field(max_length=MAX_NUTRITION_PAGE_DAYS * 7, repr=False)
    next_after: date | None = Field(
        description="Null on the last page. Pagination is live, not a cross-request snapshot.",
    )
