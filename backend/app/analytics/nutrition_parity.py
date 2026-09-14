from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import HealthSample
from app.nutrition.resolution.metrics import CANONICAL_METRICS

CANONICAL_PARITY_METRICS: Final[tuple[str, ...]] = tuple(CANONICAL_METRICS)
MAX_PARITY_DAYS: Final[int] = 366


class NutritionParityClassification(StrEnum):
    MATCH = "match"
    BOTH_MISSING = "both_missing"
    LEGACY_ONLY = "legacy_only"
    PROJECTION_ONLY = "projection_only"
    VALUE_MISMATCH = "value_mismatch"
    LEGACY_MULTI_SOURCE = "legacy_multi_source"
    PROJECTION_NOT_READY = "projection_not_ready"
    NOT_PROJECTED = "not_projected"


@dataclass(frozen=True, slots=True)
class NutritionLegacySourceBreakdown:
    """A safe, immutable source-level total for one legacy metric."""

    source_type: str
    value: Decimal


@dataclass(frozen=True, slots=True)
class NutritionLegacyMetric:
    """One canonical legacy metric and its source-level totals."""

    metric_key: str
    total: Decimal | None
    present: bool
    source_breakdown: tuple[NutritionLegacySourceBreakdown, ...]


@dataclass(frozen=True, slots=True)
class NutritionLegacyDay:
    """Immutable user/date-scoped legacy nutrition totals."""

    user_id: UUID
    local_date: date
    metrics: tuple[NutritionLegacyMetric, ...]


def read_legacy_nutrition_day(
    db: Session,
    *,
    user_id: UUID,
    local_date: date,
) -> NutritionLegacyDay:
    """Read and aggregate canonical legacy nutrition samples for one user and day."""
    samples = db.scalars(
        select(HealthSample).where(
            HealthSample.user_id == user_id,
            HealthSample.local_date == local_date,
            HealthSample.metric_type.in_(CANONICAL_PARITY_METRICS),
        )
    ).all()

    values_by_metric_and_source: dict[tuple[str, str], Decimal] = {}
    for sample in samples:
        key = (sample.metric_type, sample.source_type)
        values_by_metric_and_source[key] = values_by_metric_and_source.get(key, Decimal("0")) + (
            sample.value
        )

    metrics = tuple(
        _legacy_metric(metric_key, values_by_metric_and_source)
        for metric_key in CANONICAL_PARITY_METRICS
    )
    return NutritionLegacyDay(user_id=user_id, local_date=local_date, metrics=metrics)


def _legacy_metric(
    metric_key: str,
    values_by_metric_and_source: dict[tuple[str, str], Decimal],
) -> NutritionLegacyMetric:
    source_values = tuple(
        NutritionLegacySourceBreakdown(source_type=source_type, value=value)
        for (current_metric, source_type), value in sorted(values_by_metric_and_source.items())
        if current_metric == metric_key
    )
    if not source_values:
        return NutritionLegacyMetric(
            metric_key=metric_key,
            total=None,
            present=False,
            source_breakdown=(),
        )
    return NutritionLegacyMetric(
        metric_key=metric_key,
        total=sum((item.value for item in source_values), Decimal("0")),
        present=True,
        source_breakdown=source_values,
    )
