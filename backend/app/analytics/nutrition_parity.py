from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.nutrition_projection import (
    CanonicalNutritionFact,
    NutritionProjectionReadError,
    NutritionProjectionReadState,
    read_canonical_nutrition_day,
)
from app.models import HealthSample
from app.nutrition.enums import CoverageState, LineageState, ResolutionState
from app.nutrition.resolution.metrics import CANONICAL_METRICS
from app.nutrition.resolution.parity import compare_decimal_parity

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


@dataclass(frozen=True, slots=True)
class NutritionMetricParity:
    """An immutable, identifier-free comparison for one canonical metric."""

    metric_key: str
    legacy_value: Decimal | None
    legacy_present: bool
    projection_value: Decimal | None
    projection_present: bool
    projection_provider_key: str | None
    projection_coverage_state: CoverageState | None
    projection_resolution_state: ResolutionState | None
    projection_lineage_state: LineageState | None
    classification: NutritionParityClassification

    @property
    def projection_provider(self) -> str | None:
        return self.projection_provider_key

    @property
    def provider_key(self) -> str | None:
        return self.projection_provider_key

    @property
    def projection_coverage(self) -> CoverageState | None:
        return self.projection_coverage_state

    @property
    def coverage_state(self) -> CoverageState | None:
        return self.projection_coverage_state

    @property
    def projection_resolution(self) -> ResolutionState | None:
        return self.projection_resolution_state

    @property
    def resolution_state(self) -> ResolutionState | None:
        return self.projection_resolution_state

    @property
    def projection_lineage(self) -> LineageState | None:
        return self.projection_lineage_state

    @property
    def lineage_state(self) -> LineageState | None:
        return self.projection_lineage_state


@dataclass(frozen=True, slots=True)
class NutritionDayParity:
    """An immutable, identifier-free comparison for one local calendar day."""

    local_date: date
    projection_state: NutritionProjectionReadState | None
    metrics: tuple[NutritionMetricParity, ...]
    match_count: int
    mismatch_count: int
    expected_difference_count: int
    comparable: bool

    @property
    def date(self) -> date:
        return self.local_date

    @property
    def state(self) -> NutritionProjectionReadState | None:
        return self.projection_state


# Only source names confirmed by repository provider/import contracts are aliases.
# In particular, legacy import names are not normalized by fuzzy matching.
_LEGACY_PROVIDER_ALIASES = MappingProxyType(
    {
        "yazio_export_v1": "yazio",
        "yazio": "yazio",
        "google_health": "google_health",
    }
)


def _unavailable_metric(
    legacy_metric: NutritionLegacyMetric,
    classification: NutritionParityClassification,
) -> NutritionMetricParity:
    return NutritionMetricParity(
        metric_key=legacy_metric.metric_key,
        legacy_value=legacy_metric.total,
        legacy_present=legacy_metric.present,
        projection_value=None,
        projection_present=False,
        projection_provider_key=None,
        projection_coverage_state=None,
        projection_resolution_state=None,
        projection_lineage_state=None,
        classification=classification,
    )


def _ready_metric_parity(
    legacy_metric: NutritionLegacyMetric,
    fact: CanonicalNutritionFact,
) -> NutritionMetricParity:
    projection_value = fact.value
    projection_present = projection_value is not None
    if not legacy_metric.present and not projection_present:
        classification = NutritionParityClassification.BOTH_MISSING
    elif legacy_metric.present and not projection_present:
        classification = NutritionParityClassification.LEGACY_ONLY
    elif projection_present and not legacy_metric.present:
        classification = NutritionParityClassification.PROJECTION_ONLY
    else:
        assert legacy_metric.total is not None
        assert projection_value is not None
        parity = compare_decimal_parity(
            legacy_metric.total,
            projection_value,
            len(legacy_metric.source_breakdown),
        )
        if parity.within_tolerance:
            classification = NutritionParityClassification.MATCH
        elif _is_explained_multi_source_difference(legacy_metric, fact):
            classification = NutritionParityClassification.LEGACY_MULTI_SOURCE
        else:
            classification = NutritionParityClassification.VALUE_MISMATCH

    return NutritionMetricParity(
        metric_key=legacy_metric.metric_key,
        legacy_value=legacy_metric.total,
        legacy_present=legacy_metric.present,
        projection_value=projection_value,
        projection_present=projection_present,
        projection_provider_key=fact.provider_key,
        projection_coverage_state=fact.coverage_state,
        projection_resolution_state=fact.resolution_state,
        projection_lineage_state=fact.lineage_state,
        classification=classification,
    )


def _is_explained_multi_source_difference(
    legacy_metric: NutritionLegacyMetric,
    fact: CanonicalNutritionFact,
) -> bool:
    if len(legacy_metric.source_breakdown) < 2 or fact.provider_key is None:
        return False
    selected_source: NutritionLegacySourceBreakdown | None = None
    for source in legacy_metric.source_breakdown:
        mapped_provider = _LEGACY_PROVIDER_ALIASES.get(source.source_type)
        if mapped_provider == fact.provider_key:
            selected_source = source
            break
    if selected_source is None or fact.value is None or legacy_metric.total is None:
        return False
    selected_parity = compare_decimal_parity(selected_source.value, fact.value, 1)
    if not selected_parity.within_tolerance:
        return False
    additional_total = sum(
        (
            source.value
            for source in legacy_metric.source_breakdown
            if source is not selected_source
        ),
        Decimal("0"),
    )
    explained_total = selected_source.value + additional_total
    return compare_decimal_parity(
        legacy_metric.total,
        explained_total,
        len(legacy_metric.source_breakdown),
    ).within_tolerance


def _day_with_unavailable_projection(
    legacy_day: NutritionLegacyDay,
    *,
    projection_state: NutritionProjectionReadState | None,
    classification: NutritionParityClassification,
) -> NutritionDayParity:
    metrics = tuple(
        _unavailable_metric(metric, classification) for metric in legacy_day.metrics
    )
    return NutritionDayParity(
        local_date=legacy_day.local_date,
        projection_state=projection_state,
        metrics=metrics,
        match_count=0,
        mismatch_count=0,
        expected_difference_count=0,
        comparable=False,
    )


def compare_nutrition_day(
    db: Session,
    *,
    user_id: UUID,
    local_date: date,
) -> NutritionDayParity:
    """Compare one day's legacy totals with its validated current projection."""
    legacy_day = read_legacy_nutrition_day(db, user_id=user_id, local_date=local_date)
    try:
        projection_day = read_canonical_nutrition_day(db, user_id, local_date)
    except NutritionProjectionReadError:
        return _day_with_unavailable_projection(
            legacy_day,
            projection_state=None,
            classification=NutritionParityClassification.PROJECTION_NOT_READY,
        )

    if projection_day.state is NutritionProjectionReadState.NOT_PROJECTED:
        return _day_with_unavailable_projection(
            legacy_day,
            projection_state=NutritionProjectionReadState.NOT_PROJECTED,
            classification=NutritionParityClassification.NOT_PROJECTED,
        )
    if projection_day.state is not NutritionProjectionReadState.READY:
        return _day_with_unavailable_projection(
            legacy_day,
            projection_state=None,
            classification=NutritionParityClassification.PROJECTION_NOT_READY,
        )

    facts_by_metric = {fact.metric_key: fact for fact in projection_day.facts}
    metrics = tuple(
        _ready_metric_parity(legacy_metric, facts_by_metric[legacy_metric.metric_key])
        for legacy_metric in legacy_day.metrics
    )
    return NutritionDayParity(
        local_date=legacy_day.local_date,
        projection_state=NutritionProjectionReadState.READY,
        metrics=metrics,
        match_count=sum(
            metric.classification is NutritionParityClassification.MATCH for metric in metrics
        ),
        mismatch_count=sum(
            metric.classification is NutritionParityClassification.VALUE_MISMATCH
            for metric in metrics
        ),
        expected_difference_count=sum(
            metric.classification is NutritionParityClassification.LEGACY_MULTI_SOURCE
            for metric in metrics
        ),
        comparable=True,
    )


__all__ = [
    "CANONICAL_PARITY_METRICS",
    "MAX_PARITY_DAYS",
    "NutritionDayParity",
    "NutritionLegacyDay",
    "NutritionLegacyMetric",
    "NutritionLegacySourceBreakdown",
    "NutritionMetricParity",
    "NutritionParityClassification",
    "compare_nutrition_day",
    "read_legacy_nutrition_day",
]
