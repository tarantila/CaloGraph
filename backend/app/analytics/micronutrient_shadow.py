from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from time import monotonic
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analytics.service import PRIMARY_NUTRITION_METRICS, serialize_decimal
from app.database import SessionLocal
from app.micronutrients import MICRONUTRIENT_METRIC_TYPES, MICRONUTRIENTS
from app.models import HealthSample
from app.nutrition.resolution.daily_reader import resolve_daily_nutrients
from app.nutrition.resolution.metrics import CANONICAL_NUTRITION_METRICS
from app.nutrition.resolution.sources import resolve_default_provider_sources
from app.services.apple_health_nutrition_ingestion import apple_health_source_instance_id

LOGGER = logging.getLogger(__name__)
TELEMETRY_EVENT = "analytics.micronutrients.shadow"
TELEMETRY_VERSION = "n4.v1"
CANONICAL_TELEMETRY_EVENT = "analytics.micronutrients.canonical"
CANONICAL_TELEMETRY_VERSION = "n5.v1"
_MAX_EXCEPTION_CLASS_LENGTH = 64
_MAX_DAYS = 31


class MicronutrientShadowState(StrEnum):
    DISABLED = "disabled"
    PERIOD_ALL = "period_all"
    RANGE_TOO_LONG = "range_too_long"
    NOT_COMPARABLE = "not_comparable"
    MATCH = "match"
    MISMATCH = "mismatch"
    ERROR = "error"


class MicronutrientParityClassification(StrEnum):
    MATCH = "match"
    TOTAL_MISMATCH = "total_mismatch"
    AVERAGE_MISMATCH = "average_mismatch"
    DAYS_WITH_VALUE_MISMATCH = "days_with_value_mismatch"
    STATUS_MISMATCH = "status_mismatch"
    RECORDED_DAYS_MISMATCH = "recorded_days_mismatch"
    LEGACY_ONLY = "legacy_only"
    CANONICAL_ONLY = "canonical_only"
    NOT_COMPARABLE = "not_comparable"


class MicronutrientCanonicalTelemetryOutcome(StrEnum):
    LEGACY_INELIGIBLE = "legacy_ineligible"
    FALLBACK_NOT_COMPARABLE = "fallback_not_comparable"
    FALLBACK_PARITY = "fallback_parity"
    FALLBACK_ERROR = "fallback_error"
    CANONICAL_SERVED = "canonical_served"


@dataclass(frozen=True, slots=True)
class MicronutrientMetricResult:
    metric_type: str
    total: Decimal | None
    average_daily: Decimal | None
    days_with_value: int
    eu_nrv: Decimal | None
    percent_of_nrv: Decimal | None
    status: str
    coverage_ratio: float = 0.0


@dataclass(frozen=True, slots=True)
class MicronutrientPeriodResult:
    recorded_days: int
    nutrients: tuple[MicronutrientMetricResult, ...]
    filtered_updated_at: datetime | None = None
    available_sources: tuple[tuple[str, datetime | None], ...] = ()

    def by_metric(self) -> Mapping[str, MicronutrientMetricResult]:
        return {item.metric_type: item for item in self.nutrients}

    def _public_nutrients(self) -> list[dict[str, Any]]:
        return [
            {
                "id": definition.yazio_id,
                "metric_type": definition.metric_type,
                "label": definition.label,
                "category": definition.category,
                "unit": definition.unit,
                "eu_nrv": serialize_decimal(definition.eu_nrv),
                "total": serialize_decimal(item.total),
                "average_daily": serialize_decimal(item.average_daily),
                "days_with_value": item.days_with_value,
                "coverage_ratio": item.coverage_ratio,
                "percent_of_nrv": serialize_decimal(item.percent_of_nrv),
                "status": item.status,
            }
            for definition, item in zip(MICRONUTRIENTS, self.nutrients, strict=True)
        ]

    def to_public_value_scope(self) -> dict[str, Any]:
        return {
            "recorded_days": self.recorded_days,
            "nutrients": self._public_nutrients(),
        }

    def to_public(self, *, start: date, end: date, source: str | None) -> dict[str, Any]:
        return {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "source": source,
            **self.to_public_value_scope(),
            "last_updated_at": (
                self.filtered_updated_at.isoformat() if self.filtered_updated_at else None
            ),
            "available_sources": [
                {
                    "source_type": source_type,
                    "last_updated_at": updated_at.isoformat() if updated_at else None,
                }
                for source_type, updated_at in self.available_sources
            ],
            "definition": {
                "reference": "EU-NRV für Erwachsene, Verordnung (EU) Nr. 1169/2011, Anhang XIII",
                "average": "Summe im Zeitraum geteilt durch Tage mit Ernährungseinträgen derselben Quelle",
                "coverage_threshold": 0.7,
                "orientation_threshold_percent": 80,
            },
        }


@dataclass(frozen=True, slots=True)
class MicronutrientParityItem:
    metric_type: str
    classification: MicronutrientParityClassification


@dataclass(frozen=True, slots=True)
class MicronutrientParityResult:
    classification: MicronutrientParityClassification
    items: tuple[MicronutrientParityItem, ...]


@dataclass(frozen=True, slots=True)
class ShadowSourceMapping:
    source_type: str
    provider_key: str
    source_instance_id: UUID


@dataclass(frozen=True, slots=True)
class ShadowEligibility:
    state: MicronutrientShadowState
    reason: str | None = None

    @classmethod
    def disabled(cls) -> ShadowEligibility:
        return cls(MicronutrientShadowState.DISABLED, "disabled")

    @classmethod
    def check(
        cls,
        *,
        enabled: bool,
        source: str | None,
        period: str | None,
        start: date,
        end: date,
        max_days: int = _MAX_DAYS,
    ) -> ShadowEligibility:
        if not enabled:
            return cls.disabled()
        if period == "all":
            return cls(MicronutrientShadowState.PERIOD_ALL, "period_all")
        if (
            not isinstance(start, date)
            or isinstance(start, datetime)
            or not isinstance(end, date)
            or isinstance(end, datetime)
            or start > end
        ):
            return cls(MicronutrientShadowState.RANGE_TOO_LONG, "invalid_range")
        if max_days < 1 or max_days > _MAX_DAYS or (end - start).days + 1 > max_days:
            return cls(MicronutrientShadowState.RANGE_TOO_LONG, "range_too_long")
        if source is None:
            return cls(MicronutrientShadowState.NOT_COMPARABLE, "source_none")
        if source not in {"yazio_export_v1", "apple_health_xml"}:
            return cls(MicronutrientShadowState.NOT_COMPARABLE, "source_mapping")
        return cls(MicronutrientShadowState.MATCH)


@dataclass(frozen=True, slots=True)
class MicronutrientEvaluation:
    state: MicronutrientShadowState
    canonical: MicronutrientPeriodResult | None = None
    parity: MicronutrientParityResult | None = None
    reason: str | None = None
    exception_class: str | None = None


@dataclass(frozen=True, slots=True)
class MicronutrientShadowOutcome:
    state: MicronutrientShadowState
    classification: MicronutrientParityClassification | None = None
    reason: str | None = None
    exception_class: str | None = None


def read_legacy_micronutrient_period(
    db: Session,
    *,
    user_id: UUID,
    start: date,
    end: date,
    source: str | None,
) -> MicronutrientPeriodResult:
    all_source_rows = db.execute(
        select(HealthSample.source_type, func.max(HealthSample.updated_at))
        .where(
            HealthSample.user_id == user_id,
            HealthSample.local_date >= start,
            HealthSample.local_date <= end,
            HealthSample.metric_type.in_(MICRONUTRIENT_METRIC_TYPES),
        )
        .group_by(HealthSample.source_type)
        .order_by(HealthSample.source_type)
    ).all()
    sample_query = select(HealthSample).where(
        HealthSample.user_id == user_id,
        HealthSample.local_date >= start,
        HealthSample.local_date <= end,
        HealthSample.metric_type.in_(MICRONUTRIENT_METRIC_TYPES),
    )
    nutrition_day_query = (
        select(HealthSample.local_date)
        .where(
            HealthSample.user_id == user_id,
            HealthSample.local_date >= start,
            HealthSample.local_date <= end,
            HealthSample.metric_type.in_(PRIMARY_NUTRITION_METRICS),
            HealthSample.value > 0,
        )
        .distinct()
    )
    if source:
        sample_query = sample_query.where(HealthSample.source_type == source)
        nutrition_day_query = nutrition_day_query.where(HealthSample.source_type == source)

    samples = list(db.scalars(sample_query))
    recorded_dates = set(db.scalars(nutrition_day_query))
    if not recorded_dates:
        recorded_dates = {sample.local_date for sample in samples}

    totals: dict[str, Decimal] = defaultdict(Decimal)
    days_with_value: dict[str, set[date]] = defaultdict(set)
    for sample in samples:
        totals[sample.metric_type] += sample.value
        days_with_value[sample.metric_type].add(sample.local_date)

    recorded_days = len(recorded_dates)
    metrics: list[MicronutrientMetricResult] = []
    for definition in MICRONUTRIENTS:
        total = totals.get(definition.metric_type)
        available_days = len(days_with_value.get(definition.metric_type, set()))
        average = total / recorded_days if total is not None and recorded_days else None
        coverage_ratio = available_days / recorded_days if recorded_days else 0.0
        reference_percent = (
            average / definition.eu_nrv * Decimal("100")
            if average is not None and definition.eu_nrv
            else None
        )
        if average is None:
            status = "no_data"
        elif coverage_ratio < 0.7:
            status = "insufficient_data"
        elif reference_percent is not None and reference_percent < Decimal("80"):
            status = "below_orientation"
        else:
            status = "covered"
        metrics.append(
            MicronutrientMetricResult(
                metric_type=definition.metric_type,
                total=total,
                average_daily=average,
                days_with_value=available_days,
                eu_nrv=definition.eu_nrv,
                percent_of_nrv=reference_percent,
                status=status,
                coverage_ratio=coverage_ratio,
            )
        )

    return MicronutrientPeriodResult(
        recorded_days=recorded_days,
        nutrients=tuple(metrics),
        filtered_updated_at=max((sample.updated_at for sample in samples), default=None),
        available_sources=tuple(
            (source_type, updated_at) for source_type, updated_at in all_source_rows
        ),
    )


def read_canonical_micronutrient_period(
    db: Session,
    *,
    user_id: UUID,
    provider_key: str,
    source_instance_id: UUID,
    start: date,
    end: date,
) -> MicronutrientPeriodResult:
    values_by_metric: dict[str, dict[date, Decimal]] = {
        metric_type: {} for metric_type in MICRONUTRIENT_METRIC_TYPES
    }
    primary_recorded_dates: set[date] = set()
    all_value_dates: set[date] = set()
    current = start
    requested_metrics = tuple(CANONICAL_NUTRITION_METRICS)
    while current <= end:
        candidates = resolve_daily_nutrients(
            db,
            provider_key=provider_key,
            user_id=user_id,
            source_instance_id=source_instance_id,
            local_date=current,
            metric_keys=requested_metrics,
        )
        candidate_values = {
            metric_type: candidate.value
            for metric_type, candidate in zip(requested_metrics, candidates, strict=True)
        }
        if any(
            (value is not None and value > 0)
            for metric_type, value in candidate_values.items()
            if metric_type in PRIMARY_NUTRITION_METRICS
        ):
            primary_recorded_dates.add(current)
        for metric_type in MICRONUTRIENT_METRIC_TYPES:
            value = candidate_values.get(metric_type)
            if value is not None:
                values_by_metric[metric_type][current] = value
                all_value_dates.add(current)
        current += timedelta(days=1)

    recorded_dates = primary_recorded_dates or all_value_dates
    recorded_days = len(recorded_dates)
    metrics: list[MicronutrientMetricResult] = []
    for definition in MICRONUTRIENTS:
        values = values_by_metric[definition.metric_type]
        total = sum(values.values(), Decimal()) if values else None
        available_days = len(values)
        average = total / recorded_days if total is not None and recorded_days else None
        coverage_ratio = available_days / recorded_days if recorded_days else 0.0
        reference_percent = (
            average / definition.eu_nrv * Decimal("100")
            if average is not None and definition.eu_nrv
            else None
        )
        if average is None:
            status = "no_data"
        elif coverage_ratio < 0.7:
            status = "insufficient_data"
        elif reference_percent is not None and reference_percent < Decimal("80"):
            status = "below_orientation"
        else:
            status = "covered"
        metrics.append(
            MicronutrientMetricResult(
                metric_type=definition.metric_type,
                total=total,
                average_daily=average,
                days_with_value=available_days,
                eu_nrv=definition.eu_nrv,
                percent_of_nrv=reference_percent,
                status=status,
                coverage_ratio=coverage_ratio,
            )
        )
    return MicronutrientPeriodResult(recorded_days=recorded_days, nutrients=tuple(metrics))


def compare_micronutrient_periods(
    legacy: MicronutrientPeriodResult,
    canonical: MicronutrientPeriodResult,
) -> MicronutrientParityResult:
    if legacy.recorded_days != canonical.recorded_days:
        return MicronutrientParityResult(
            MicronutrientParityClassification.RECORDED_DAYS_MISMATCH,
            tuple(
                MicronutrientParityItem(
                    metric_type=definition.metric_type,
                    classification=MicronutrientParityClassification.RECORDED_DAYS_MISMATCH,
                )
                for definition in MICRONUTRIENTS
            ),
        )

    legacy_by_metric = legacy.by_metric()
    canonical_by_metric = canonical.by_metric()
    items: list[MicronutrientParityItem] = []
    for definition in MICRONUTRIENTS:
        legacy_item = legacy_by_metric.get(definition.metric_type)
        canonical_item = canonical_by_metric.get(definition.metric_type)
        if legacy_item is None and canonical_item is not None:
            classification = MicronutrientParityClassification.CANONICAL_ONLY
        elif legacy_item is not None and canonical_item is None:
            classification = MicronutrientParityClassification.LEGACY_ONLY
        elif legacy_item is None:
            classification = MicronutrientParityClassification.MATCH
        else:
            assert canonical_item is not None
            if legacy_item.total is None and canonical_item.total is not None:
                classification = MicronutrientParityClassification.CANONICAL_ONLY
            elif legacy_item.total is not None and canonical_item.total is None:
                classification = MicronutrientParityClassification.LEGACY_ONLY
            elif legacy_item.total != canonical_item.total:
                classification = MicronutrientParityClassification.TOTAL_MISMATCH
            elif legacy_item.average_daily != canonical_item.average_daily:
                classification = MicronutrientParityClassification.AVERAGE_MISMATCH
            elif legacy_item.days_with_value != canonical_item.days_with_value:
                classification = MicronutrientParityClassification.DAYS_WITH_VALUE_MISMATCH
            elif legacy_item.status != canonical_item.status:
                classification = MicronutrientParityClassification.STATUS_MISMATCH
            else:
                classification = MicronutrientParityClassification.MATCH
        items.append(MicronutrientParityItem(definition.metric_type, classification))

    precedence = (
        MicronutrientParityClassification.TOTAL_MISMATCH,
        MicronutrientParityClassification.AVERAGE_MISMATCH,
        MicronutrientParityClassification.DAYS_WITH_VALUE_MISMATCH,
        MicronutrientParityClassification.STATUS_MISMATCH,
        MicronutrientParityClassification.LEGACY_ONLY,
        MicronutrientParityClassification.CANONICAL_ONLY,
    )
    classification = next(
        (candidate for candidate in precedence if any(item.classification is candidate for item in items)),
        MicronutrientParityClassification.MATCH,
    )
    return MicronutrientParityResult(classification, tuple(items))


def resolve_shadow_source_mapping(
    db: Session,
    *,
    user_id: UUID,
    source: str,
) -> ShadowSourceMapping | None:
    if source == "apple_health_xml":
        return ShadowSourceMapping(source, "apple_health", apple_health_source_instance_id(user_id))
    if source == "yazio_export_v1":
        bindings = resolve_default_provider_sources(db, user_id=user_id, provider_keys=("yazio",))
        yazio = bindings.for_provider("yazio")
        if yazio is None:
            return None
        return ShadowSourceMapping(source, yazio.provider_key, yazio.source_instance_id)
    return None


def evaluate_micronutrient_candidate(
    db: Session,
    *,
    user_id: UUID,
    start: date,
    end: date,
    source: str | None,
    period: str | None,
    legacy: MicronutrientPeriodResult,
    enabled: bool,
    max_days: int = _MAX_DAYS,
) -> MicronutrientEvaluation:
    eligibility = ShadowEligibility.check(
        enabled=enabled,
        source=source,
        period=period,
        start=start,
        end=end,
        max_days=max_days,
    )
    if eligibility.state is not MicronutrientShadowState.MATCH:
        return MicronutrientEvaluation(eligibility.state, reason=eligibility.reason)
    assert source is not None
    mapping = resolve_shadow_source_mapping(db, user_id=user_id, source=source)
    if mapping is None:
        return MicronutrientEvaluation(
            MicronutrientShadowState.NOT_COMPARABLE,
            reason="source_mapping",
        )
    canonical = read_canonical_micronutrient_period(
        db,
        user_id=user_id,
        provider_key=mapping.provider_key,
        source_instance_id=mapping.source_instance_id,
        start=start,
        end=end,
    )
    parity = compare_micronutrient_periods(legacy, canonical)
    return MicronutrientEvaluation(
        MicronutrientShadowState.MATCH
        if parity.classification is MicronutrientParityClassification.MATCH
        else MicronutrientShadowState.MISMATCH,
        canonical=canonical,
        parity=parity,
    )


def _range_bucket(start: date, end: date) -> str:
    days = (end - start).days + 1
    if days == 1:
        return "1"
    if days <= 7:
        return "2-7"
    if days <= 31:
        return "8-31"
    return "32+"


def _duration_bucket(duration_seconds: float) -> str:
    milliseconds = duration_seconds * 1_000
    if milliseconds < 10:
        return "<10ms"
    if milliseconds < 50:
        return "10-49ms"
    if milliseconds < 200:
        return "50-199ms"
    return "200ms+"


def _exception_class(exc: BaseException) -> str:
    return type(exc).__name__[:_MAX_EXCEPTION_CLASS_LENGTH]


def _emit_telemetry(
    *,
    outcome: MicronutrientShadowOutcome,
    start: date,
    end: date,
    elapsed_seconds: float,
) -> None:
    fields: dict[str, Any] = {
        "event": TELEMETRY_EVENT,
        "version": TELEMETRY_VERSION,
        "outcome": outcome.state.value,
        "range_bucket": _range_bucket(start, end),
        "duration_bucket": _duration_bucket(elapsed_seconds),
    }
    if outcome.classification is not None:
        fields["classification"] = outcome.classification.value
    if outcome.exception_class is not None:
        fields["exception_class"] = outcome.exception_class
    try:
        LOGGER.info(json.dumps(fields, sort_keys=True, separators=(",", ":")), extra=fields)
    except Exception:
        return


def _rollback_safely(session: object) -> None:
    rollback = getattr(session, "rollback", None)
    if callable(rollback):
        try:
            rollback()
        except Exception:
            return


def _run_micronutrient_evaluation(
    user_id: UUID,
    start: date,
    end: date,
    source: str | None,
    period: str | None,
    legacy: MicronutrientPeriodResult,
    *,
    enabled: bool,
    max_days: int,
) -> MicronutrientEvaluation:
    eligibility = ShadowEligibility.check(
        enabled=enabled,
        source=source,
        period=period,
        start=start,
        end=end,
        max_days=max_days,
    )
    if eligibility.state is not MicronutrientShadowState.MATCH:
        return MicronutrientEvaluation(eligibility.state, reason=eligibility.reason)

    session: object | None = None
    try:
        session = SessionLocal()
        with session as candidate_session:
            return evaluate_micronutrient_candidate(
                candidate_session,
                user_id=user_id,
                start=start,
                end=end,
                source=source,
                period=period,
                legacy=legacy,
                enabled=enabled,
                max_days=max_days,
            )
    except Exception as exc:
        if session is not None:
            _rollback_safely(session)
        return MicronutrientEvaluation(
            MicronutrientShadowState.ERROR,
            exception_class=_exception_class(exc),
        )


def _canonical_telemetry_outcome(
    evaluation: MicronutrientEvaluation,
) -> MicronutrientCanonicalTelemetryOutcome:
    if (
        evaluation.state is MicronutrientShadowState.MATCH
        and evaluation.canonical is not None
    ):
        return MicronutrientCanonicalTelemetryOutcome.CANONICAL_SERVED
    if evaluation.state in {
        MicronutrientShadowState.DISABLED,
        MicronutrientShadowState.PERIOD_ALL,
        MicronutrientShadowState.RANGE_TOO_LONG,
    }:
        return MicronutrientCanonicalTelemetryOutcome.LEGACY_INELIGIBLE
    if evaluation.state is MicronutrientShadowState.NOT_COMPARABLE:
        return MicronutrientCanonicalTelemetryOutcome.FALLBACK_NOT_COMPARABLE
    if evaluation.state is MicronutrientShadowState.MISMATCH:
        return MicronutrientCanonicalTelemetryOutcome.FALLBACK_PARITY
    return MicronutrientCanonicalTelemetryOutcome.FALLBACK_ERROR


def _emit_canonical_telemetry(
    *,
    evaluation: MicronutrientEvaluation,
    start: date,
    end: date,
    elapsed_seconds: float,
) -> None:
    fields: dict[str, Any] = {
        "event": CANONICAL_TELEMETRY_EVENT,
        "version": CANONICAL_TELEMETRY_VERSION,
        "outcome": _canonical_telemetry_outcome(evaluation).value,
        "range_bucket": _range_bucket(start, end),
        "duration_bucket": _duration_bucket(elapsed_seconds),
    }
    if evaluation.parity is not None:
        fields["classification"] = evaluation.parity.classification.value
    if evaluation.exception_class is not None:
        fields["exception_class"] = evaluation.exception_class
    try:
        LOGGER.info(json.dumps(fields, sort_keys=True, separators=(",", ":")), extra=fields)
    except Exception:
        return


def run_micronutrient_canonical_read(
    user_id: UUID,
    start: date,
    end: date,
    source: str | None,
    period: str | None,
    legacy: MicronutrientPeriodResult,
    *,
    enabled: bool,
    max_days: int = _MAX_DAYS,
) -> MicronutrientEvaluation:
    started = monotonic()
    evaluation = _run_micronutrient_evaluation(
        user_id,
        start,
        end,
        source,
        period,
        legacy,
        enabled=enabled,
        max_days=max_days,
    )
    if evaluation.state is MicronutrientShadowState.MATCH and evaluation.canonical is not None:
        try:
            evaluation.canonical.to_public_value_scope()
        except Exception as exc:
            evaluation = MicronutrientEvaluation(
                MicronutrientShadowState.ERROR,
                exception_class=_exception_class(exc),
            )
    _emit_canonical_telemetry(
        evaluation=evaluation,
        start=start,
        end=end,
        elapsed_seconds=monotonic() - started,
    )
    return evaluation


def run_micronutrient_shadow(
    user_id: UUID,
    start: date,
    end: date,
    source: str | None,
    period: str | None,
    legacy: MicronutrientPeriodResult,
    *,
    enabled: bool,
    max_days: int = _MAX_DAYS,
) -> MicronutrientShadowOutcome:
    started = monotonic()
    evaluation = _run_micronutrient_evaluation(
        user_id,
        start,
        end,
        source,
        period,
        legacy,
        enabled=enabled,
        max_days=max_days,
    )
    outcome = MicronutrientShadowOutcome(
        evaluation.state,
        classification=(
            evaluation.parity.classification if evaluation.parity is not None else None
        ),
        reason=evaluation.reason,
        exception_class=evaluation.exception_class,
    )
    _emit_telemetry(
        outcome=outcome,
        start=start,
        end=end,
        elapsed_seconds=monotonic() - started,
    )
    return outcome


__all__ = [
    "MicronutrientCanonicalTelemetryOutcome",
    "MicronutrientEvaluation",
    "MicronutrientMetricResult",
    "MicronutrientParityClassification",
    "MicronutrientParityResult",
    "MicronutrientPeriodResult",
    "MicronutrientShadowOutcome",
    "MicronutrientShadowState",
    "ShadowEligibility",
    "ShadowSourceMapping",
    "compare_micronutrient_periods",
    "evaluate_micronutrient_candidate",
    "read_canonical_micronutrient_period",
    "read_legacy_micronutrient_period",
    "resolve_shadow_source_mapping",
    "run_micronutrient_canonical_read",
    "run_micronutrient_shadow",
]
