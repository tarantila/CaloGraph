from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.nutrition.enums import (
    CoverageState,
    LineageState,
    PresenceState,
    ProjectionGranularity,
    ProjectionStatus,
    ResolutionState,
)
from app.nutrition.models import NutritionDailyProjectionFact
from app.nutrition.projection.contracts import validate_projection_decimal
from app.nutrition.repositories import get_current_projection
from app.nutrition.resolution.metrics import CANONICAL_NUTRITION_METRICS, DAILY_PROJECTION_METRICS


class NutritionProjectionReadState(StrEnum):
    NOT_PROJECTED = "not_projected"
    READY = "ready"


class NutritionProjectionReadError(ValueError):
    """The current nutrition projection cannot be represented safely."""


@dataclass(frozen=True, slots=True)
class CanonicalNutritionFact:
    metric_key: str
    unit: str
    value: Decimal | None
    presence_state: PresenceState
    coverage_state: CoverageState
    resolution_state: ResolutionState
    lineage_state: LineageState
    provider_key: str | None
    granularity: ProjectionGranularity | None


@dataclass(frozen=True, slots=True)
class CanonicalNutritionDay:
    state: NutritionProjectionReadState
    user_id: UUID
    local_date: date
    projection_id: UUID | None
    projection_version: int | None
    projection_status: ProjectionStatus | None
    facts: tuple[CanonicalNutritionFact, ...]
    has_primary_evidence: bool
    has_any_nutrition_value: bool
    calorie_value_available: bool
    calorie_coverage_complete: bool
    calorie_resolution_resolved: bool
    calorie_usable: bool


_EXPECTED_METRIC_KEYS = DAILY_PROJECTION_METRICS
_METRIC_ORDER = {metric_key: index for index, metric_key in enumerate(_EXPECTED_METRIC_KEYS)}


def _read_error(message: str) -> NutritionProjectionReadError:
    return NutritionProjectionReadError(message)


def _enum_from_persisted[EnumT: StrEnum](
    enum_type: type[EnumT], value: object, field_name: str
) -> EnumT:
    if not isinstance(value, str):
        raise _read_error(f"{field_name} is not a known enum value")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise _read_error(f"{field_name} is not a known enum value") from exc


def _not_projected(user_id: UUID, local_date: date) -> CanonicalNutritionDay:
    return CanonicalNutritionDay(
        state=NutritionProjectionReadState.NOT_PROJECTED,
        user_id=user_id,
        local_date=local_date,
        projection_id=None,
        projection_version=None,
        projection_status=None,
        facts=(),
        has_primary_evidence=False,
        has_any_nutrition_value=False,
        calorie_value_available=False,
        calorie_coverage_complete=False,
        calorie_resolution_resolved=False,
        calorie_usable=False,
    )


def read_canonical_nutrition_day(
    db: Session,
    user_id: UUID,
    local_date: date,
) -> CanonicalNutritionDay:
    """Read the user/date current projection as an immutable canonical contract."""
    projection = get_current_projection(db, user_id, local_date)
    if projection is None:
        return _not_projected(user_id, local_date)

    if projection.user_id != user_id:
        raise _read_error("projection is outside the requested user scope")
    if projection.local_date != local_date:
        raise _read_error("projection is outside the requested date scope")

    projection_status = _enum_from_persisted(
        ProjectionStatus,
        projection.projection_status,
        "projection_status",
    )
    if projection_status is not ProjectionStatus.READY:
        raise _read_error("current projection is not READY")
    if not isinstance(projection.id, UUID):
        raise _read_error("projection has no valid identity")
    if not isinstance(projection.projection_version, int) or isinstance(
        projection.projection_version, bool
    ):
        raise _read_error("projection has no valid version")

    facts = db.scalars(
        select(NutritionDailyProjectionFact)
        .where(
            NutritionDailyProjectionFact.user_id == user_id,
            NutritionDailyProjectionFact.projection_id == projection.id,
        )
        .order_by(
            case(
                _METRIC_ORDER,
                value=NutritionDailyProjectionFact.metric_key,
            )
        )
    ).all()

    actual_metric_keys = tuple(fact.metric_key for fact in facts)
    if any(not isinstance(metric_key, str) for metric_key in actual_metric_keys):
        raise _read_error("current projection contains an invalid metric key")
    if (
        len(actual_metric_keys) != len(_EXPECTED_METRIC_KEYS)
        or len(set(actual_metric_keys)) != len(actual_metric_keys)
        or set(actual_metric_keys) != set(_EXPECTED_METRIC_KEYS)
    ):
        raise _read_error("current projection does not contain exactly the canonical metrics")

    facts_by_metric = {fact.metric_key: fact for fact in facts}
    canonical_facts: list[CanonicalNutritionFact] = []
    for metric_key in _EXPECTED_METRIC_KEYS:
        fact = facts_by_metric[metric_key]
        definition = CANONICAL_NUTRITION_METRICS[metric_key]
        if fact.user_id != user_id or fact.projection_id != projection.id:
            raise _read_error("projection fact is outside the requested scope")
        if fact.unit != definition.canonical_unit:
            raise _read_error(f"projection fact has an invalid unit for {metric_key}")
        if fact.value is not None and not isinstance(fact.value, Decimal):
            raise _read_error(f"projection fact has an invalid value for {metric_key}")
        try:
            validated_value = validate_projection_decimal(fact.value, f"{metric_key}.value")
        except ValueError as exc:
            raise _read_error(f"projection fact has an invalid value for {metric_key}") from exc
        if fact.selected_provider_key is not None and not isinstance(
            fact.selected_provider_key, str
        ):
            raise _read_error(f"projection fact has an invalid provider for {metric_key}")

        canonical_facts.append(
            CanonicalNutritionFact(
                metric_key=metric_key,
                unit=fact.unit,
                value=validated_value,
                presence_state=_enum_from_persisted(
                    PresenceState,
                    fact.presence_state,
                    f"{metric_key}.presence_state",
                ),
                coverage_state=_enum_from_persisted(
                    CoverageState,
                    fact.coverage_state,
                    f"{metric_key}.coverage_state",
                ),
                resolution_state=_enum_from_persisted(
                    ResolutionState,
                    fact.resolution_state,
                    f"{metric_key}.resolution_state",
                ),
                lineage_state=_enum_from_persisted(
                    LineageState,
                    fact.lineage_state,
                    f"{metric_key}.lineage_state",
                ),
                provider_key=fact.selected_provider_key,
                granularity=(
                    None
                    if fact.selected_granularity is None
                    else _enum_from_persisted(
                        ProjectionGranularity,
                        fact.selected_granularity,
                        f"{metric_key}.selected_granularity",
                    )
                ),
            )
        )

    canonical_facts_tuple = tuple(canonical_facts)
    canonical_facts_by_metric = {fact.metric_key: fact for fact in canonical_facts_tuple}
    calorie = canonical_facts_by_metric["dietary_energy_kcal"]
    calorie_value_available = calorie.value is not None
    calorie_coverage_complete = calorie.coverage_state is CoverageState.COMPLETE
    calorie_resolution_resolved = calorie.resolution_state is ResolutionState.RESOLVED
    return CanonicalNutritionDay(
        state=NutritionProjectionReadState.READY,
        user_id=user_id,
        local_date=local_date,
        projection_id=projection.id,
        projection_version=projection.projection_version,
        projection_status=projection_status,
        facts=canonical_facts_tuple,
        has_primary_evidence=any(
            fact.presence_state in {PresenceState.SUPPLIED, PresenceState.EXPLICIT_ZERO}
            for fact in canonical_facts_tuple
        ),
        has_any_nutrition_value=any(fact.value is not None for fact in canonical_facts_tuple),
        calorie_value_available=calorie_value_available,
        calorie_coverage_complete=calorie_coverage_complete,
        calorie_resolution_resolved=calorie_resolution_resolved,
        calorie_usable=(
            calorie_value_available and calorie_coverage_complete and calorie_resolution_resolved
        ),
    )


__all__ = [
    "CanonicalNutritionDay",
    "CanonicalNutritionFact",
    "NutritionProjectionReadError",
    "NutritionProjectionReadState",
    "read_canonical_nutrition_day",
]
