from __future__ import annotations

from typing import Literal, TypedDict

from app.micronutrients import MICRONUTRIENTS


class AnalysisDefinition(TypedDict):
    analysis_key: str
    category_key: str
    domain_key: str
    status: Literal["available", "coming_soon"]
    card_capable: bool
    detail_capable: bool
    max_days: int | None
    reader_key: str | None


def _definition(
    analysis_key: str,
    category_key: str,
    *,
    status: Literal["available", "coming_soon"] = "coming_soon",
    domain_key: str | None = None,
    max_days: int | None = None,
    reader_key: str | None = None,
) -> AnalysisDefinition:
    return {
        "analysis_key": analysis_key,
        "category_key": category_key,
        "domain_key": domain_key or analysis_key,
        "status": status,
        "card_capable": status == "available",
        "detail_capable": status == "available",
        "max_days": max_days,
        "reader_key": reader_key,
    }


_ANALYSES: tuple[AnalysisDefinition, ...] = (
    _definition(
        "weight_kg",
        "body",
        status="available",
        max_days=3661,
        reader_key="analytics.weight",
    ),
    _definition(
        "active_energy_kcal",
        "activity",
        status="available",
        max_days=3661,
        reader_key="analytics.verification.activity",
    ),
    *(
        _definition(
            item.metric_type,
            "micronutrients",
            status="available",
            max_days=31,
            reader_key="analytics.nutrition.micronutrients",
        )
        for item in MICRONUTRIENTS
    ),
    _definition("fat_ratio_pct", "body"),
    _definition("bmi", "body", domain_key="body_mass_index"),
    _definition("fat_mass_kg", "body"),
    _definition("muscle_mass_kg", "body"),
    _definition("fat_free_mass_kg", "body"),
    _definition("hydration_kg", "body"),
    _definition("bone_mass_kg", "body"),
    _definition("waist_circumference_cm", "body"),
    _definition("hip_circumference_cm", "body"),
    _definition("steps", "activity", domain_key="step_count"),
    _definition("distance_m", "activity"),
    _definition("training", "activity", domain_key="workout_sessions"),
    _definition("dietary_energy_kcal", "nutrition"),
    _definition("protein_g", "nutrition"),
    _definition("carbohydrates_g", "nutrition"),
    _definition("fat_g", "nutrition"),
    _definition("fiber_g", "nutrition"),
    _definition("sugar_g", "nutrition"),
    _definition("saturated_fat_g", "nutrition"),
    _definition("water_intake_ml", "fluid", domain_key="water_intake_ml"),
)

ANALYSIS_REGISTRY: dict[str, AnalysisDefinition] = {
    item["analysis_key"]: item for item in _ANALYSES
}


def analysis_registry_entries() -> list[AnalysisDefinition]:
    return list(_ANALYSES)
