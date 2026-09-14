from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Final
from uuid import UUID

from sqlalchemy import select, union
from sqlalchemy.orm import Session

from app.nutrition.models import (
    NutritionConsumptionEvent,
    NutritionProjectionHead,
    NutritionSourceObservation,
)

CANONICAL_HISTORY_CHUNK_SIZE: Final[int] = 500


class MovingAverageParityClassification(StrEnum):
    MATCH = "match"
    BOTH_MISSING = "both_missing"
    EXPECTED_TRACKING_DIFFERENCE = "expected_tracking_difference"
    EXPECTED_NUTRITION_DIFFERENCE = "expected_nutrition_difference"
    UNEXPLAINED_MISMATCH = "unexplained_mismatch"
    NOT_COMPARABLE = "not_comparable"


class HistoricalBudgetBalanceClassification(StrEnum):
    MATCH = "match"
    EXPECTED_TRACKING_DIFFERENCE = "expected_tracking_difference"
    EXPECTED_NUTRITION_DIFFERENCE = "expected_nutrition_difference"
    LEGACY_ONLY_HISTORY = "legacy_only_history"
    UNEXPLAINED_MISMATCH = "unexplained_mismatch"


class DataQualityParityClassification(StrEnum):
    MATCH = "match"
    EXPECTED_TRACKING_DIFFERENCE = "expected_tracking_difference"
    EXPECTED_NUTRITION_DIFFERENCE = "expected_nutrition_difference"
    UNEXPLAINED_MISMATCH = "unexplained_mismatch"
    NOT_COMPARABLE = "not_comparable"


@dataclass(frozen=True, slots=True)
class MovingAverageParity:
    local_date: date
    window: int
    legacy_value: Decimal | None
    canonical_value: Decimal | None
    classification: MovingAverageParityClassification
    explanation: str | None = None


@dataclass(frozen=True, slots=True)
class MovingAverageRangeParity:
    start_date: date
    end_date: date
    results: tuple[MovingAverageParity, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "results", tuple(self.results))


@dataclass(frozen=True, slots=True)
class HistoricalBudgetBalanceParity:
    legacy_counts: Mapping[str, int]
    canonical_counts: Mapping[str, int]
    classification: HistoricalBudgetBalanceClassification

    def __post_init__(self) -> None:
        object.__setattr__(self, "legacy_counts", MappingProxyType(dict(self.legacy_counts)))
        object.__setattr__(self, "canonical_counts", MappingProxyType(dict(self.canonical_counts)))


@dataclass(frozen=True, slots=True)
class DataQualityParity:
    start_date: date
    end_date: date
    total_days: int
    recorded_days: int
    missing_days: int
    incomplete_days: int
    coverage_ratio: Decimal | None
    comparable_days: int
    comparable_coverage_ratio: Decimal | None
    not_comparable_days: int
    classification: DataQualityParityClassification


@dataclass(frozen=True, slots=True)
class CanonicalHistorySummary:
    data_start_date: date | None
    data_end_date: date | None
    data_day_count: int
    explicit_zero_days: int
    incomplete_days: int
    not_comparable_days: int


def _validate_date_bound(value: date | None, name: str) -> None:
    if value is not None and (not isinstance(value, date) or isinstance(value, datetime)):
        raise ValueError(f"{name} must be a date")


def _date_predicates(
    column: Any,
    *,
    start_date: date | None,
    end_date: date | None,
) -> list[Any]:
    predicates = [column.is_not(None)]
    if start_date is not None:
        predicates.append(column >= start_date)
    if end_date is not None:
        predicates.append(column <= end_date)
    return predicates


def iter_canonical_history_date_chunks(
    db: Session,
    user_id: UUID,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    chunk_size: int = CANONICAL_HISTORY_CHUNK_SIZE,
) -> Iterator[tuple[date, ...]]:
    """Yield the user's canonical nutrition history dates in bounded keyset pages.

    Dates are sourced from observations, consumption events, and projection heads.
    The SQL ``UNION`` removes duplicates before each page is ordered and limited;
    ``last_date`` then advances the keyset without loading the full history.
    """
    if type(chunk_size) is not int or not 1 <= chunk_size <= CANONICAL_HISTORY_CHUNK_SIZE:
        raise ValueError(f"chunk_size must be between 1 and {CANONICAL_HISTORY_CHUNK_SIZE}")
    _validate_date_bound(start_date, "start_date")
    _validate_date_bound(end_date, "end_date")
    if start_date is not None and end_date is not None and start_date > end_date:
        raise ValueError("start_date must not be after end_date")

    def _iterate() -> Iterator[tuple[date, ...]]:
        observation_dates = select(
            NutritionSourceObservation.local_date.label("local_date")
        ).where(
            NutritionSourceObservation.user_id == user_id,
            *_date_predicates(
                NutritionSourceObservation.local_date,
                start_date=start_date,
                end_date=end_date,
            ),
        )
        event_dates = select(NutritionConsumptionEvent.local_date.label("local_date")).where(
            NutritionConsumptionEvent.user_id == user_id,
            *_date_predicates(
                NutritionConsumptionEvent.local_date,
                start_date=start_date,
                end_date=end_date,
            ),
        )
        projection_head_dates = select(
            NutritionProjectionHead.local_date.label("local_date")
        ).where(
            NutritionProjectionHead.user_id == user_id,
            *_date_predicates(
                NutritionProjectionHead.local_date,
                start_date=start_date,
                end_date=end_date,
            ),
        )
        canonical_dates = union(
            observation_dates,
            event_dates,
            projection_head_dates,
        ).cte("canonical_history_dates")

        last_date: date | None = None
        while True:
            statement = select(canonical_dates.c.local_date).select_from(canonical_dates)
            if last_date is not None:
                statement = statement.where(canonical_dates.c.local_date > last_date)
            statement = statement.order_by(canonical_dates.c.local_date).limit(chunk_size)
            chunk = tuple(db.scalars(statement).all())
            if not chunk:
                return
            yield chunk
            last_date = chunk[-1]


    return _iterate()


__all__ = [
    "CANONICAL_HISTORY_CHUNK_SIZE",
    "CanonicalHistorySummary",
    "DataQualityParity",
    "DataQualityParityClassification",
    "HistoricalBudgetBalanceClassification",
    "HistoricalBudgetBalanceParity",
    "MovingAverageParity",
    "MovingAverageParityClassification",
    "MovingAverageRangeParity",
    "iter_canonical_history_date_chunks",
]
