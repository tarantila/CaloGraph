from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class NutritionPrioritySource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    available: bool
    rank: int | None


class NutritionPriorityState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal[
        "no_providers",
        "configured",
        "selection_required",
        "configuration_required",
        "advanced_configuration",
    ]
    version: int | None
    sources: list[NutritionPrioritySource]
    configuration_mode: Literal["none", "global", "advanced"]
    projection_refresh_required: bool


class NutritionPriorityUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int | None
    source_order: list[str]


class NutritionPriorityRefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int


class NutritionPriorityRefreshResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_version: int
    processed_count: int
    created_count: int
    unchanged_count: int
    has_more: bool
    projection_refresh_required: bool


__all__ = [
    "NutritionPriorityRefreshRequest",
    "NutritionPriorityRefreshResponse",
    "NutritionPrioritySource",
    "NutritionPriorityState",
    "NutritionPriorityUpdateRequest",
]
