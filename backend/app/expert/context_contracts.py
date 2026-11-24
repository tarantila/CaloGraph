"""Bounded, account-local Nutrition Expert context representations."""

from datetime import date
from typing import Literal

from pydantic import Field

from app.expert.contracts import ExpertContract, Measurement, NutritionValues


class MetricAggregate(ExpertContract):
    metric: str
    count: int = Field(ge=0)
    total: Measurement | None = Field(repr=False)
    mean: Measurement | None = Field(repr=False)


class ProviderCoverage(ExpertContract):
    provider_key: str
    days_with_data: int = Field(ge=0)


class HistoryAggregate(ExpertContract):
    evidence_id: str
    granularity: Literal["month", "year"]
    start: date
    end: date
    calendar_days: int = Field(ge=1)
    nutrition_days_with_data: int = Field(ge=0)
    metrics: tuple[MetricAggregate, ...]
    providers: tuple[ProviderCoverage, ...]
    target_days: int = Field(default=0, ge=0)
    target_calories_kcal: Measurement | None = Field(default=None, repr=False)
    calories_vs_target_kcal: Measurement | None = Field(default=None, repr=False)


class DailyNutritionDetail(ExpertContract):
    evidence_id: str
    date: date
    provider_key: str | None
    values: NutritionValues = Field(repr=False)
    meal_names: tuple[str, ...] = Field(default=(), max_length=100, repr=False)
    target_calories_kcal: Measurement | None = Field(default=None, repr=False)
    calories_vs_target_kcal: Measurement | None = Field(default=None, repr=False)


class MicronutrientCoverage(ExpertContract):
    evidence_id: str
    metric: str
    unit: Literal["mg", "ug"]
    total: Measurement | None = Field(repr=False)
    days_with_data: int = Field(ge=0)


class ExpertProfileContext(ExpertContract):
    evidence_id: str
    gender: str | None
    age_years: int | None
    height_cm: Measurement | None = Field(repr=False)
    diet_type: str | None


class ExpertHealthContext(ExpertContract):
    evidence_id: str
    notes: str | None = Field(repr=False)
    intolerances: str | None = Field(repr=False)


class TargetPeriod(ExpertContract):
    evidence_id: str
    valid_from: date
    valid_to: date | None
    calories_kcal: Measurement = Field(repr=False)
    maintenance_kcal: Measurement | None = Field(repr=False)
    protein_g: Measurement = Field(repr=False)
    carbs_g: Measurement | None = Field(repr=False)
    fat_g: Measurement | None = Field(repr=False)
    fiber_g: Measurement | None = Field(repr=False)


class ScalarDailyPoint(ExpertContract):
    evidence_id: str
    date: date
    provider_key: str | None = None
    value: Measurement | None = Field(repr=False)


class ScalarHistoryAggregate(ExpertContract):
    evidence_id: str
    granularity: Literal["month", "year"]
    start: date
    end: date
    measurement_count: int = Field(ge=0)
    providers: tuple[ProviderCoverage, ...] = ()
    total: Measurement | None = Field(repr=False)
    mean: Measurement | None = Field(repr=False)
    minimum: Measurement | None = Field(repr=False)
    maximum: Measurement | None = Field(repr=False)


class ExpertContext(ExpertContract):
    """Complete context for explicitly enabled domains; sensitive values hide from repr."""

    timezone: str
    coverage_start: date | None
    coverage_end: date
    coverage_complete: bool
    older_history_resolution: Literal["year"]
    target_comparison_basis: Literal["base_target_without_activity_credit"] = "base_target_without_activity_credit"
    recent_detail_start: date
    daily_nutrition: tuple[DailyNutritionDetail, ...] = Field(repr=False)
    aggregates: tuple[HistoryAggregate, ...] = Field(repr=False)
    micronutrients: tuple[MicronutrientCoverage, ...] = Field(repr=False)
    nutrition_status: Literal["available", "no_records", "disabled"]
    provider_coverage: tuple[ProviderCoverage, ...]
    targets_status: Literal["available", "no_records", "disabled"]
    weight_status: Literal["available", "no_records", "disabled"]
    activity_status: Literal["available", "no_records", "disabled"]
    weight_daily: tuple[ScalarDailyPoint, ...] = Field(repr=False)
    weight_aggregates: tuple[ScalarHistoryAggregate, ...] = Field(repr=False)
    activity_daily: tuple[ScalarDailyPoint, ...] = Field(repr=False)
    activity_aggregates: tuple[ScalarHistoryAggregate, ...] = Field(repr=False)
    target_periods: tuple[TargetPeriod, ...] = Field(repr=False)
    profile: ExpertProfileContext | None = Field(default=None, repr=False)
    health_context: ExpertHealthContext | None = Field(default=None, repr=False)
    limitations: tuple[str, ...] = Field(default=(), max_length=20)

    @property
    def coverage_end_date(self) -> date:
        return self.coverage_end

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        return tuple(
            [item.evidence_id for item in self.aggregates]
            + [item.evidence_id for item in self.daily_nutrition]
            + [item.evidence_id for item in self.micronutrients]
            + [item.evidence_id for item in self.target_periods]
            + ([self.profile.evidence_id] if self.profile is not None else [])
            + ([self.health_context.evidence_id] if self.health_context is not None else [])
            + [item.evidence_id for item in self.weight_daily]
            + [item.evidence_id for item in self.weight_aggregates]
            + [item.evidence_id for item in self.activity_daily]
            + [item.evidence_id for item in self.activity_aggregates]
        )
